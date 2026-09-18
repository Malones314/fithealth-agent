"""受控清理 `memory/` 下的历史框架产物（agent-trace 阶段 0）。

## 为什么单独做成脚本，而不是在提交里直接删

`memory/traces/`、`memory/sessions/` 里是**对话历史与健康细节的明文**。删除不可
逆，而且这些文件不受 `/data/reset` 与备份事务管辖——没有恢复点可退。所以这里把
"登记"和"销毁"拆成两步：

1. 默认只**盘点**：逐文件记 sha256、字节数、mtime，写一份清单。清单里只有文件名
   和摘要，没有任何内容，可以留档；
2. 销毁必须显式带确认短语，与 `/data/reset` 同一套约定（`maintenance_ops.py`），
   并逐项返回成败——半删状态要看得见，而不是抛一个异常了事。

阶段 0 关掉框架的 6 个可选子系统之后，这四个目录**不再产生新文件**；这个脚本
处理的是存量。阶段 6 会把清理能力接进 `/data/reset`，届时本脚本只保留盘点用途。

## 用法

盘点（不删任何东西）::

    python scripts/purge_legacy_traces.py
    python scripts/purge_legacy_traces.py --manifest tool-output/legacy-traces.json

销毁（不可逆）::

    python scripts/purge_legacy_traces.py --delete --confirm 删除全部轨迹
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

#: 允许操作的目录，**硬编码**且只认仓库内相对路径。不接受调用方传任意路径：
#: 这个脚本唯一的动作是不可逆删除，不该有"删哪都行"的入口。
TARGET_DIRS = (
    "memory/traces",
    "memory/sessions",
    "memory/todos",
    "memory/devlogs",
)

CONFIRMATION = "删除全部轨迹"


def _resolved_target(relative: str) -> Path:
    """把白名单条目解析成绝对路径，并确认它没跑到仓库外面去。

    `Path.resolve()` 会展开符号链接，所以一条指向别处的 `memory/traces` 软链在这里
    就会被拒掉，而不是等到 `unlink()` 之后才发现删错了东西。
    """
    target = (REPO_ROOT / relative).resolve()
    if not target.is_relative_to(REPO_ROOT):
        raise SystemExit(f"拒绝操作仓库之外的路径：{relative} -> {target}")
    return target


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def inventory() -> dict:
    """逐文件登记，不改动任何东西。"""
    entries: list[dict] = []
    for relative in TARGET_DIRS:
        target = _resolved_target(relative)
        if not target.is_dir():
            continue
        for item in sorted(target.rglob("*")):
            if not item.is_file():
                continue
            stat = item.stat()
            entries.append(
                {
                    "path": item.relative_to(REPO_ROOT).as_posix(),
                    "bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "sha256": _digest(item),
                }
            )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": REPO_ROOT.as_posix(),
        "target_dirs": list(TARGET_DIRS),
        "file_count": len(entries),
        "total_bytes": sum(entry["bytes"] for entry in entries),
        "files": entries,
    }


def purge(manifest: dict) -> list[dict]:
    """按清单逐项删除。一项失败不影响其余项，成败原样返回。"""
    results: list[dict] = []
    for entry in manifest["files"]:
        target = (REPO_ROOT / entry["path"]).resolve()
        if not target.is_relative_to(REPO_ROOT):
            results.append({**entry, "deleted": False, "error": "路径越界"})
            continue
        try:
            target.unlink()
        except OSError as exc:
            results.append({**entry, "deleted": False, "error": str(exc)})
        else:
            results.append({"path": entry["path"], "deleted": True, "error": None})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="清单落盘路径（默认 tool-output/legacy-trace-manifest-<时间戳>.json）",
    )
    parser.add_argument("--delete", action="store_true", help="执行不可逆删除")
    parser.add_argument("--confirm", default="", help=f"删除时必须传 {CONFIRMATION}")
    args = parser.parse_args(argv)

    manifest = inventory()
    print(f"盘点：{manifest['file_count']} 个文件，{manifest['total_bytes']:,} 字节")
    for relative in TARGET_DIRS:
        count = sum(1 for item in manifest["files"] if item["path"].startswith(relative))
        print(f"  {relative:<20} {count}")

    manifest_path = args.manifest or (
        REPO_ROOT
        / "tool-output"
        / f"legacy-trace-manifest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.delete:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"清单已写入 {manifest_path}")
        print(f"如需销毁：--delete --confirm {CONFIRMATION}")
        return 0

    if args.confirm != CONFIRMATION:
        print(f"确认短语不正确，未删除任何文件。需要 --confirm {CONFIRMATION}", file=sys.stderr)
        return 2

    # 先把清单落盘再删：删完之后清单是唯一的留档，顺序反了就等于没登记。
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"清单已写入 {manifest_path}")

    results = purge(manifest)
    failures = [item for item in results if not item["deleted"]]
    manifest_path.write_text(
        json.dumps({**manifest, "purge_results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已删除 {len(results) - len(failures)}/{len(results)} 个文件")
    if failures:
        print("以下文件未能删除：", file=sys.stderr)
        for item in failures:
            print(f"  {item['path']}：{item['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
