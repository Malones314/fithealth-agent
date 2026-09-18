"""一次性脚本：把测试里的 `main.<共享单例/外部依赖>` 打桩点改到 `deps` 上。

main.py 拆分阶段 1 用完即可删。

两类改写：

1. `patch.object(main, "route_chat_intent", ...)` → `patch.object(deps, ...)`
   main.py 现在调的是 `deps.route_chat_intent(...)`，桩必须打在被读取的那个模块属性上，
   否则测试变绿而桩根本没生效（拆分计划"约束 A"）。
2. `main.info_store.get_all()` / `cls.main.soreness_store = X` → `deps.info_store...`
   同一个道理：`main` 已经不再持有这些名字。

**只改 DEPS_SYMBOLS 里的名字。** `_current_recovery_snapshot`、
`_immediate_memory_candidate`、`looks_like_complete_training_plan` 等仍然定义在
main.py，桩必须继续打在 `main` 上——机械地把所有 `main.X` 换成 `deps.X` 正是计划里
明确警告过的做法。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


DEPS_SYMBOLS = (
    "profile_store",
    "daily_record_store",
    "info_store",
    "external_model_settings_store",
    "soreness_store",
    "plan_store",
    "plan_draft_cache",
    "health_store",
    "hr_stream_store",
    "health_import_service",
    "backup_service",
    "route_chat_intent",
    "route_information",
    "classify_user_health_statement",
    "create_fithealth_agent",
    "build_current_week_reply",
    "analyze_food_image",
    "parse_soreness_reply",
    "parse_fit_file",
    "inspect_fit_source",
    "extract_activity_fits",
)

_NAMES = "|".join(DEPS_SYMBOLS)
_MAIN = r"(?:cls\.|self\.)?main"

#: `patch.object(main, "soreness_store", ...)` / `setattr(main, "info_store", ...)`
PATCH_TARGET = re.compile(
    r"((?:patch\.object|setattr)\(\s*)" + _MAIN + r"(\s*,\s*)(['\"])(" + _NAMES + r")\3",
    re.S,
)
#: `main.info_store` / `cls.main.profile_store`
ATTRIBUTE = re.compile(r"\b" + _MAIN + r"\.(" + _NAMES + r")\b")


def rewrite(source: str) -> tuple[str, int]:
    result, patched = PATCH_TARGET.subn(r"\1deps\2\3\4\3", source)
    result, attributes = ATTRIBUTE.subn(r"deps.\1", result)
    return result, patched + attributes


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    total = 0
    for name in argv:
        path = Path(name)
        source = path.read_text(encoding="utf-8")
        rewritten, count = rewrite(source)
        if count:
            path.write_text(rewritten, encoding="utf-8")
            print(f"{path}: 改写 {count} 处")
            total += count
    print(f"合计 {total} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
