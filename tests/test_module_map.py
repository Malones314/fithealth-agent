"""module_map.py 自身的守门测试（main.py 拆分：阶段 0）。

这张表是后续 8 个阶段的单一事实来源。它一旦与实现漂移，症状是别处的测试抛
``StopIteration`` 或"抽不到函数"，排查起来要绕一大圈。所以在这里直接钉住：

- 声明的符号必须真的存在于声明的模块里；
- 声明的模块文件必须真的存在；
- 缺席断言用的 ``IMPLEMENTATION_MODULES`` 必须覆盖到 ``FUNCTION_HOME`` /
  ``CONSUMER_HOME`` 提到的每个文件，否则"某函数已彻底删除"会静默变成永真。

搬迁函数时如果忘了更新 module_map.py，**这个文件第一个报错**，并且报错信息直接
指出是哪个符号、期望在哪个文件。
"""

from __future__ import annotations

import ast
import unittest

from tests import module_map
from tests.source_tools import module_tree


def _top_level_symbols(path) -> set[str]:
    """模块顶层定义的名字：函数、类、模块级赋值。"""
    names: set[str] = set()
    for node in module_tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


class ModuleMapAccuracyTest(unittest.TestCase):
    def test_every_declared_module_file_exists(self) -> None:
        declared = (
            set(module_map.FUNCTION_HOME.values())
            | set(module_map.CONSUMER_HOME.values())
            | set(module_map.IMPLEMENTATION_MODULES)
        )
        for path in sorted(declared):
            with self.subTest(module=path.name):
                self.assertTrue(path.is_file(), f"module_map 指向了不存在的文件：{path}")

    def test_every_declared_symbol_lives_where_the_map_says(self) -> None:
        for name, path in sorted(module_map.FUNCTION_HOME.items()):
            with self.subTest(symbol=name, module=path.name):
                self.assertTrue(
                    name in _top_level_symbols(path),
                    f"FUNCTION_HOME 说 {name!r} 住在 {path.name}，但那里没有这个顶层符号。"
                    f"它是被搬走了还是改名了？请更新 tests/module_map.py。",
                )

    def test_consumer_keys_that_name_a_function_resolve_in_their_module(self) -> None:
        """键名取自消费者函数时，那个函数必须真的在声明的模块里。

        少数键钉的是模块级接线（如两个异常处理器），没有同名函数，跳过。
        """
        wiring_only = {
            "degraded_error_handlers",
            "memory_fact_routes",
            "session_intro_copy",
            "workout_state_routes",
        }
        aliases = {"reset_steps": "_reset_steps"}
        for key, path in sorted(module_map.CONSUMER_HOME.items()):
            if key in wiring_only:
                continue
            name = aliases.get(key, key)
            with self.subTest(consumer=key, module=path.name):
                self.assertTrue(
                    name in _top_level_symbols(path),
                    f"CONSUMER_HOME 说 {key!r} 的接线在 {path.name}，"
                    f"但那里没有 {name!r}。请更新 tests/module_map.py。",
                )

    def test_deps_consumers_are_implementation_modules(self) -> None:
        missing = sorted(
            path.name
            for path in module_map.DEPS_CONSUMERS
            if path not in set(module_map.IMPLEMENTATION_MODULES)
        )
        self.assertEqual(missing, [], "DEPS_CONSUMERS 必须是 IMPLEMENTATION_MODULES 的子集")

    def test_every_module_resolves_to_an_importable_name(self) -> None:
        expected = {
            "main.py": "main",
            "deps.py": "fithealth_agent.runtime.deps",
            "upload_io.py": "fithealth_agent.runtime.upload_io",
            "middleware.py": "fithealth_agent.runtime.middleware",
            "responses.py": "fithealth_agent.runtime.responses",
            "plan_validation.py": "fithealth_agent.domain.plan_validation",
            "plan_context.py": "fithealth_agent.domain.plan_context",
            "intent_rules.py": "fithealth_agent.domain.intent_rules",
            "profile_rules.py": "fithealth_agent.domain.profile_rules",
            "memory_view.py": "fithealth_agent.domain.memory_view",
            "recovery_view.py": "fithealth_agent.domain.recovery_view",
            "record_view.py": "fithealth_agent.domain.record_view",
            "segment_merge.py": "fithealth_agent.domain.segment_merge",
            "chat_workflow.py": "fithealth_agent.workflows.chat_workflow",
            "chat.py": "fithealth_agent.routes.chat",
            "logout.py": "fithealth_agent.routes.logout",
            "uploads.py": "fithealth_agent.routes.uploads",
            "upload_workflow.py": "fithealth_agent.workflows.upload_workflow",
            "logout_workflow.py": "fithealth_agent.workflows.logout_workflow",
            "workout_state.py": "fithealth_agent.routes.workout_state",
            "records.py": "fithealth_agent.routes.records",
            "plans.py": "fithealth_agent.routes.plans",
            "memories.py": "fithealth_agent.routes.memories",
            "health.py": "fithealth_agent.routes.health",
            "settings.py": "fithealth_agent.routes.settings",
            "maintenance_ops.py": "fithealth_agent.routes.maintenance_ops",
            # agent-trace 阶段 0-1
            "tool_output.py": "fithealth_agent.tool_output",
            "trace.py": "fithealth_agent.observability.trace",
            "config.py": "fithealth_agent.observability.config",
            "schema.py": "fithealth_agent.observability.schema",
            "redact.py": "fithealth_agent.observability.redact",
            "sink.py": "fithealth_agent.observability.sink",
            "http.py": "fithealth_agent.observability.http",
            "model_trace.py": "fithealth_agent.observability.model_trace",
            # agent-trace 阶段 5
            "react_trace.py": "fithealth_agent.observability.react_trace",
            "cost.py": "fithealth_agent.observability.cost",
            "agent.py": "fithealth_agent.agent",
        }
        for path in module_map.IMPLEMENTATION_MODULES:
            with self.subTest(module=path.name):
                self.assertEqual(module_map.module_import_name(path), expected[path.name])

    def test_absence_assertions_cover_every_declared_module(self) -> None:
        """IMPLEMENTATION_MODULES 必须是两张表的超集。

        否则函数搬到新模块之后，"它已经被删掉了"这类断言不会去看新模块，
        于是永远为真——正是 ARCH-09 想消灭的那种静默失效。
        """
        covered = set(module_map.IMPLEMENTATION_MODULES)
        declared = set(module_map.FUNCTION_HOME.values()) | set(module_map.CONSUMER_HOME.values())
        uncovered = sorted(path.name for path in declared - covered)
        self.assertEqual(
            uncovered,
            [],
            "这些模块出现在 FUNCTION_HOME/CONSUMER_HOME 里，"
            "但没被加进 IMPLEMENTATION_MODULES：" + ", ".join(uncovered),
        )


if __name__ == "__main__":
    unittest.main()
