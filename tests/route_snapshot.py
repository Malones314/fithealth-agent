"""`app.routes` 与 `/openapi.json` 的契约快照（main.py 拆分：阶段 0）。

拆分会把端点从 main.py 搬到 7 个 `routes/*.py` 里。**不得遗漏任何一条路由，也
不得改变任何一条的路径、方法、状态码或 operation id**——按资源表搬迁最容易犯的
错就是漏掉 `/analyze_food`、`/upload_health/activity`、`/data/profile/reset`
这类不在表里的独立端点，而漏掉的表现只是"前端某个按钮 404 了"，测试全绿。

所以在阶段 0 把契约冻成 `tests/baseline/route_snapshot.json`，由
`tests/test_route_snapshot.py` 每次跑测试都比一遍。

重新生成（**只在确实要改 HTTP 契约时**）::

    python -m tests.route_snapshot --write

`endpoint` 一栏记的是端点函数当前住在哪个模块，属于**参考信息**，不参与契约比对
——那一栏本来就会随拆分而变化，正是它在记录进度。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fithealth_agent.runtime import middleware


REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = Path(__file__).resolve().parent / "baseline" / "route_snapshot.json"

#: 参与严格比对的字段。`endpoint` 不在其中。
CONTRACT_FIELDS = ("path", "methods", "name", "response_class", "status_code")


def _unwrap_default(value):
    """FastAPI 用 DefaultPlaceholder 包装"调用方没显式指定"的参数。"""
    return getattr(value, "value", value)


def _class_name(value) -> str | None:
    value = _unwrap_default(value)
    if value is None:
        return None
    return getattr(value, "__name__", None) or type(value).__name__


def _endpoint_location(route) -> str | None:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return None
    module = getattr(endpoint, "__module__", "?")
    qualname = getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", "?"))
    return f"{module}:{qualname}"


def build_snapshot(app, *, allowed_paths=(), allowed_prefixes=(), untracked_paths=()) -> dict:
    """按**注册顺序**记录路由；顺序会影响路径匹配，不能排序后比较。"""
    routes = []
    for route in app.routes:
        routes.append(
            {
                "path": getattr(route, "path", None),
                "methods": sorted(getattr(route, "methods", None) or []),
                "name": getattr(route, "name", None),
                "response_class": _class_name(getattr(route, "response_class", None)),
                "status_code": _unwrap_default(getattr(route, "status_code", None)),
                "endpoint": _endpoint_location(route),
            }
        )

    schema = app.openapi()
    operations = {}
    for path, methods in sorted(schema.get("paths", {}).items()):
        for method, operation in sorted(methods.items()):
            operations[f"{method.upper()} {path}"] = {
                "operation_id": operation.get("operationId"),
                "responses": sorted(operation.get("responses", {})),
            }

    return {
        "routes": routes,
        "openapi_operations": operations,
        # 维护期排空依赖这两份**路径字符串**完全一致：白名单里的路径要放行，
        # untracked 里的路径不计入在途请求。改一个字符就会让维护期死等。
        "maintenance_allowed_paths": sorted(allowed_paths),
        "maintenance_allowed_prefixes": sorted(allowed_prefixes),
        "maintenance_untracked_paths": sorted(untracked_paths),
    }


def build_snapshot_from_main() -> dict:
    """import main 之前先把数据目录指向临时目录，避免碰到真实 data/。"""
    import importlib

    previous = os.environ.get("FITHEALTH_DATA_DIR")
    if previous is None:
        os.environ["FITHEALTH_DATA_DIR"] = tempfile.mkdtemp(prefix="fithealth-route-snapshot-")
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        main = importlib.import_module("main")
    finally:
        if previous is None:
            os.environ.pop("FITHEALTH_DATA_DIR", None)

    return build_snapshot(
        main.app,
        allowed_paths=middleware.MAINTENANCE_ALLOWED_PATHS,
        allowed_prefixes=middleware.MAINTENANCE_ALLOWED_PREFIXES,
        untracked_paths=middleware.MAINTENANCE_UNTRACKED_PATHS,
    )


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def contract_view(snapshot: dict) -> dict:
    """剥掉参考信息，只留严格比对的部分。"""
    return {
        "routes": [
            {field: route[field] for field in CONTRACT_FIELDS}
            for route in snapshot["routes"]
        ],
        "openapi_operations": snapshot["openapi_operations"],
        "maintenance_allowed_paths": snapshot["maintenance_allowed_paths"],
        "maintenance_allowed_prefixes": snapshot["maintenance_allowed_prefixes"],
        "maintenance_untracked_paths": snapshot["maintenance_untracked_paths"],
    }


def main() -> int:
    write = "--write" in sys.argv
    current = build_snapshot_from_main()
    if write:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(f"已写入 {SNAPSHOT_PATH}（{len(current['routes'])} 条路由）")
        return 0

    if not SNAPSHOT_PATH.is_file():
        print(f"快照不存在：{SNAPSHOT_PATH}；先跑 --write 生成基线", file=sys.stderr)
        return 2
    if contract_view(current) == contract_view(load_snapshot()):
        print(f"契约一致（{len(current['routes'])} 条路由）")
        return 0
    print("契约与快照不一致；跑 pytest tests/test_route_snapshot.py 看逐条差异", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
