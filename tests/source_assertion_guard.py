"""Static detection for assertions against implementation source text."""

from __future__ import annotations

import ast
from pathlib import Path


ASSERT_METHODS = {
    "assertIn",
    "assertNotIn",
    "assertRegex",
    "assertNotRegex",
    "assertTrue",
    "assertFalse",
}
SOURCE_READ_METHODS = {"read_text", "read"}
#: 产出实现源码的辅助函数。
#:
#: 前四个是各测试文件自己的函数体切片工具；后三个来自 tests/source_tools.py——
#: main.py 拆分（阶段 0）把 `(REPO_ROOT / "main.py").read_text()` 收敛成了
#: `module_source(consumer_home(...))`，那条 `.read_text` 调用不再出现在测试
#: 文件里。这些名字必须留在这里，否则本守门器会静默失去对这批文件的覆盖，
#: LEGACY_SOURCE_ASSERTION_TESTS 基线也会跟着一起假绿。
#:
#: `module_tree` 在列（它等价于原来的 `ast.parse(read_text())`，污染要继续往下传），
#: 但 `function_node` **不在**：拿到一个 AST 节点做结构断言是 ARCH-09 允许的，
#: 只有把它 `ast.unparse` 回文本再做字符串匹配才算源码文本断言——那一步由下面
#: 的 `unparse` 规则单独识别。
SOURCE_HELPERS = {
    "_body_source",
    "function_body",
    "method_body",
    "_method_source",
    "module_source",
    "module_tree",
    "function_body_source",
}


def _reads_implementation_source(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in SOURCE_READ_METHODS:
        return False
    receiver = ast.unparse(call.func.value).casefold()
    return any(
        marker in receiver
        for marker in (
            ".py",
            ".html",
            "repo_root",
            "package_dir",
            "templates_dir",
            "legacy_index",
            "main_path",
            "source_path",
        )
    )


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Attribute):
        return {target.attr}
    if isinstance(target, (ast.Tuple, ast.List)):
        return {name for item in target.elts for name in _target_names(item)}
    return set()


def _referenced_names(node: ast.AST) -> set[str]:
    return {
        item.id if isinstance(item, ast.Name) else item.attr
        for item in ast.walk(node)
        if isinstance(item, (ast.Name, ast.Attribute))
    }


def _is_source_producer(node: ast.AST, tainted: set[str]) -> bool:
    if _referenced_names(node) & tainted:
        return True
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        if _reads_implementation_source(item):
            return True
        name = item.func.id if isinstance(item.func, ast.Name) else item.func.attr if isinstance(item.func, ast.Attribute) else ""
        if name in SOURCE_HELPERS or (name == "unparse" and isinstance(item.func, ast.Attribute)):
            return True
    return False


def source_text_assertions(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    module_tainted: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is not None and _is_source_producer(value, module_tainted):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                module_tainted.update(name for target in targets for name in _target_names(target))

    findings: list[tuple[str, int]] = []
    for function in (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ):
        tainted = set(module_tainted)
        changed = True
        while changed:
            changed = False
            for node in ast.walk(function):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                    continue
                if not _is_source_producer(node.value, tainted):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {name for target in targets for name in _target_names(target)}
                if not names <= tainted:
                    tainted.update(names)
                    changed = True

        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            method = call.func.attr if isinstance(call.func, ast.Attribute) else ""
            if method not in ASSERT_METHODS or not call.args:
                continue
            # 参数既可能是先赋值再传进来的"被污染的名字"，也可能是**直接内联**的
            # 源码读取（`assertIn(x, module_source(...))`）。后者没有赋值语句可
            # 供污染传播，必须单独识别，否则内联写法就是一条绕过本守门器的暗道。
            if any(
                _referenced_names(argument) & tainted
                or _is_source_producer(argument, tainted)
                for argument in call.args
            ):
                findings.append((function.name, call.lineno))
        for assertion in (node for node in ast.walk(function) if isinstance(node, ast.Assert)):
            if _referenced_names(assertion.test) & tainted or _is_source_producer(
                assertion.test, tainted
            ):
                findings.append((function.name, assertion.lineno))
    return findings
