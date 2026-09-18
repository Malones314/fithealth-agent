"""一次性脚本：把 main.py 里的共享单例与外部依赖引用改成 `deps.X` 属性访问。

main.py 拆分阶段 1 用完即可删。

刻意用 AST 而不是正则或 tokenize：只改写 **`Load` 上下文里的 `ast.Name`**，其余情况
在 AST 里根本不是 `Name` 节点，于是全部自动排除，不需要一条条写例外规则——

- `fithealth_agent.info_store` 的 `info_store` 是 `Attribute.attr`（字符串）；
- `health_store=health_store` 的关键字名是 `keyword.arg`（字符串），只有等号右边是 `Name`；
- `info_store = InfoStore()` 的左边是 `Name` 但 ctx 为 `Store`；
- `from x import route_chat_intent` 里是 `alias.name`（字符串）；
- 注释和字符串字面量里的同名文本不是 `Name`。

注意 `ast` 的 `col_offset` 是 **UTF-8 字节偏移**，而 main.py 有大量中文，所以插入要在
按行 encode 之后的 bytes 上做。按偏移倒序处理，前面的位置才不会失效；格式一个字符都不变。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


DEPS_SYMBOLS = {
    # 11 个 store / service 实例
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
    # 10 个被测试打桩的外部依赖函数
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
}


def load_positions(source: str) -> list[tuple[int, int, str]]:
    """`(行号, UTF-8 列字节偏移, 名字)`，只含 Load 上下文的目标符号。"""
    tree = ast.parse(source)
    import_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            import_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    found: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        if node.id not in DEPS_SYMBOLS or node.lineno in import_lines:
            continue
        found.append((node.lineno, node.col_offset, node.id))
    return found


def rewrite(source: str) -> tuple[str, int]:
    positions = load_positions(source)
    lines = source.splitlines(keepends=True)
    # 同一行可能有多处，按列倒序，避免前面的插入把后面的偏移顶歪。
    for lineno, col, name in sorted(positions, key=lambda item: (-item[0], -item[1])):
        raw = lines[lineno - 1].encode("utf-8")
        actual = raw[col : col + len(name.encode("utf-8"))].decode("utf-8")
        if actual != name:
            raise SystemExit(f"第 {lineno} 行第 {col} 字节处期望 {name!r}，实际是 {actual!r}")
        lines[lineno - 1] = (raw[:col] + b"deps." + raw[col:]).decode("utf-8")
    return "".join(lines), len(positions)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    path = Path(argv[0])
    source = path.read_text(encoding="utf-8")
    rewritten, count = rewrite(source)
    ast.parse(rewritten)  # 写回之前先确认还是合法 Python
    path.write_text(rewritten, encoding="utf-8")
    print(f"{path}: 改写 {count} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
