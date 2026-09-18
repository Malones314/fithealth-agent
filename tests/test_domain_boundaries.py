"""domain 层不得接触运行时装配、HTTP 或 store 实例（拆分阶段 3）。"""

from __future__ import annotations

import ast
import unittest

from tests import module_map
from tests.source_tools import module_tree


class DomainBoundaryTest(unittest.TestCase):
    @staticmethod
    def domain_modules():
        return (
            module_map.PLAN_VALIDATION,
            module_map.PLAN_CONTEXT,
            module_map.INTENT_RULES,
            module_map.PROFILE_RULES,
            module_map.MEMORY_VIEW,
            module_map.RECOVERY_VIEW,
            module_map.RECORD_VIEW,
            module_map.SEGMENT_MERGE,
        )

    def test_domain_modules_do_not_import_runtime_or_http_frameworks(self) -> None:
        forbidden_prefixes = ("fastapi", "starlette", "fithealth_agent.runtime")
        for path in self.domain_modules():
            imports = [
                node.module or ""
                for node in ast.walk(module_tree(path))
                if isinstance(node, ast.ImportFrom)
            ] + [
                alias.name
                for node in ast.walk(module_tree(path))
                if isinstance(node, ast.Import)
                for alias in node.names
            ]
            with self.subTest(module=path.name):
                self.assertEqual(
                    [name for name in imports if name.startswith(forbidden_prefixes)],
                    [],
                )

    def test_domain_modules_do_not_read_store_instances(self) -> None:
        forbidden_names = {
            "deps",
            "profile_store",
            "daily_record_store",
            "info_store",
            "soreness_store",
            "plan_store",
            "health_store",
            "hr_stream_store",
        }
        for path in self.domain_modules():
            loaded = {
                node.id
                for node in ast.walk(module_tree(path))
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            with self.subTest(module=path.name):
                self.assertEqual(sorted(loaded & forbidden_names), [])


if __name__ == "__main__":
    unittest.main()
