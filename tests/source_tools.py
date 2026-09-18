"""实现源码的统一读取与符号抽取入口（main.py 拆分：阶段 0）。

在此之前，25 个测试文件各写一份
``ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))`` 再手抽函数
节点的逻辑。main.py 一旦按计划拆成多个模块，这些硬编码路径要改 25 处；而改漏
一处的表现是 ``next(...)`` 抛 ``StopIteration``——一条完全读不出原因的失败。

这里把"读源码"和"抽符号"收敛成一份实现，**路径一律由 tests/module_map.py 查表
决定**。于是每搬走一个函数只需要改 module_map.py 一行，测试文件不动。

仍然刻意不 import 实现模块：那会触发整条 LLM 依赖链，并在真实 ``data/`` 目录上
建 store 实例（ARCH-02）。
"""

from __future__ import annotations

import ast
import copy
import json
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo


#: 抽出来的函数在合成命名空间里执行时的默认全局量。
#:
#: 实现模块里的"私有大写常量"目前只引用 ``re``（见阶段 0 盘点），函数体则常用
#: 日期与 json。这些都是 main.py 自己也 import 的标准库，先注入好过让每个调用点
#: 重复写一遍 ``{"re": re, "date": date}``。调用方传入的 namespace 会覆盖同名项。
_DEFAULT_NAMESPACE = {
    "re": re,
    "json": json,
    "date": date,
    "datetime": datetime,
    "timedelta": timedelta,
    "timezone": timezone,
    "ZoneInfo": ZoneInfo,
}

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@lru_cache(maxsize=None)
def module_source(module_path: Path) -> str:
    """读实现模块的源码文本。整个测试进程里每个文件只读一次。"""
    return Path(module_path).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _cached_tree(module_path: Path) -> ast.Module:
    """内部用的解析缓存。**调用方不得修改返回的节点**。"""
    return ast.parse(module_source(module_path), filename=str(module_path))


def module_tree(module_path: Path) -> ast.Module:
    """给结构不变量测试用的语法树。

    每次调用都重新解析（约 70ms/次，只在 ``setUpClass`` 里调），这样调用方即使
    改了节点也污染不到别人。热路径（``load_functions``）走 ``_cached_tree``。
    """
    return ast.parse(module_source(module_path), filename=str(module_path))


def _is_private_upper_constant(node: ast.stmt) -> bool:
    """识别"私有大写常量"（如 ``_SET_COUNT_PATTERN``、``_REGION_ALIASES``）。

    自动收集而不是维护一份手写清单——否则实现模块每新增一个被抽出函数引用的
    常量，这些测试就会莫名其妙地断一次。
    """
    return isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name)
        and target.id.startswith("_")
        and target.id.lstrip("_").isupper()
        for target in node.targets
    )


def _assigned_names(node: ast.stmt) -> set[str]:
    if not isinstance(node, ast.Assign):
        return set()
    return {target.id for target in node.targets if isinstance(target, ast.Name)}


def function_node(module_path: Path, name: str) -> _FunctionNode:
    """取模块顶层的函数节点；找不到时给一条能读懂的失败信息。

    ``StopIteration`` 说明不了任何事，而"函数被搬走了但 module_map 没更新"正是
    拆分期间最常见的一种失败。
    """
    for node in _cached_tree(module_path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(
        f"{Path(module_path).name} 里没有顶层函数 {name!r}；"
        f"如果它已经被搬到别的模块，请更新 tests/module_map.py"
    )


def function_body_source(module_path: Path, name: str, *, drop_docstring: bool = True) -> str:
    """函数体的 unparse 结果。

    默认去掉 docstring：注释里常常照抄有缺陷的旧写法用于说明，会误伤
    ``assertNotIn`` 这类断言。
    """
    node = function_node(module_path, name)
    statements = node.body
    if drop_docstring and ast.get_docstring(node) is not None:
        statements = statements[1:]
    return "\n".join(ast.unparse(statement) for statement in statements)


def load_functions(
    module_path: Path,
    names,
    *,
    extra_consts: bool = True,
    constants: tuple[str, ...] = (),
    namespace: dict | None = None,
) -> dict:
    """把实现模块里的若干顶层函数抽到一个合成命名空间里执行，返回该命名空间。

    :param names: 要抽的顶层函数名。
    :param extra_consts: 是否连同全部"私有大写常量"一起搬（默认是，保持既有行为）。
    :param constants: 额外按名字点名要搬的模块级赋值（如公开大写常量）。点名了却
        找不到会直接报错，而不是留到 NameError。
    :param namespace: 注入的全局量，覆盖 ``_DEFAULT_NAMESPACE`` 里的同名项。

    节点按**源码顺序**放入合成模块，与实现模块本身一致；带 ``@app.post`` 之类
    装饰器的函数会被剥掉装饰器，好在没有 FastAPI 的环境里独立执行。
    """
    resolved: dict = dict(_DEFAULT_NAMESPACE)
    resolved.update(namespace or {})
    _exec_symbols_into(
        resolved, module_path, names, extra_consts=extra_consts, constants=constants
    )
    return resolved


def _exec_symbols_into(
    resolved: dict,
    module_path: Path,
    names,
    *,
    extra_consts: bool,
    constants: tuple[str, ...],
) -> None:
    """在 ``resolved`` 这一个共享的全局字典里执行抽出来的节点。

    共享而不是每个模块一份，是为了让跨模块互相调用的函数仍能看见彼此——阶段 3
    把计划族搬到 domain/plan_context.py、意图族搬到 domain/intent_rules.py 之后，
    ``resolve_plan_context`` 还是要能调到 ``current_instruction_override``。
    """
    wanted_functions = set(names)
    wanted_constants = set(constants)
    tree = _cached_tree(module_path)

    selected: list[ast.stmt] = []
    found_functions: set[str] = set()
    found_constants: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted_functions:
            found_functions.add(node.name)
            selected.append(node)
            continue
        assigned = _assigned_names(node)
        # ``names`` 里也允许直接写模块级常量名（如 _SAFETY_BYPASS_PATTERN），
        # 调用方不必区分"这个符号是函数还是常量"。
        if assigned & wanted_functions:
            found_functions |= assigned & wanted_functions
            selected.append(node)
        elif assigned & wanted_constants:
            found_constants |= assigned & wanted_constants
            selected.append(node)
        elif extra_consts and _is_private_upper_constant(node):
            selected.append(node)

    missing = sorted((wanted_functions - found_functions) | (wanted_constants - found_constants))
    if missing:
        raise AssertionError(
            f"{Path(module_path).name} 里找不到这些顶层符号：{missing}；"
            f"如果它们已经被搬到别的模块，请更新 tests/module_map.py"
        )

    # 缓存树是共享的，剥装饰器必须在副本上做。
    body = [copy.deepcopy(node) for node in selected]
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []

    exec(  # noqa: S102 - 合成命名空间里执行实现源码，正是这个工具的目的
        compile(ast.Module(body=body, type_ignores=[]), str(module_path), "exec"),
        resolved,
    )


def load_symbols(
    names,
    *,
    extra_consts: bool = True,
    constants: tuple[str, ...] = (),
    namespace: dict | None = None,
) -> dict:
    """按 ``FUNCTION_HOME`` 查表抽符号，跨模块自动合并，返回共享命名空间。

    **这是测试文件应该用的入口**：调用方只报符号名，完全不碰文件路径。于是阶段
    3 把这些函数搬进 ``domain/`` 之后，只需要改 module_map.py 里对应的几行，
    测试文件一个字都不用动。

    ``constants`` 里的名字同样按 ``FUNCTION_HOME`` 查表。
    """
    # 延迟导入：让本模块自己保持"纯按路径工作"，不依赖那张映射表。
    from tests.module_map import function_home

    wanted = set(names)
    wanted_constants = set(constants)
    by_module: dict[Path, tuple[set[str], set[str]]] = {}
    for name in wanted:
        functions, _ = by_module.setdefault(function_home(name), (set(), set()))
        functions.add(name)
    for name in wanted_constants:
        _, module_constants = by_module.setdefault(function_home(name), (set(), set()))
        module_constants.add(name)

    resolved: dict = dict(_DEFAULT_NAMESPACE)
    resolved.update(namespace or {})
    # 路径排序，保证多模块时的执行顺序稳定可复现。
    for module_path in sorted(by_module):
        functions, module_constants = by_module[module_path]
        _exec_symbols_into(
            resolved,
            module_path,
            functions,
            extra_consts=extra_consts,
            constants=tuple(sorted(module_constants)),
        )
    return resolved


def load_symbol(name: str, *, namespace: dict | None = None, extra_consts: bool = True):
    """``load_symbols`` 的单符号版本，直接返回那个可调用对象/常量。"""
    return load_symbols({name}, namespace=namespace, extra_consts=extra_consts)[name]


def load_function(
    module_path: Path,
    name: str,
    *,
    extra_consts: bool = True,
    constants: tuple[str, ...] = (),
    namespace: dict | None = None,
):
    """``load_functions`` 的单函数版本，直接返回那个可调用对象。"""
    resolved = load_functions(
        module_path,
        {name},
        extra_consts=extra_consts,
        constants=constants,
        namespace=namespace,
    )
    return resolved[name]
