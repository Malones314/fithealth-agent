from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests import module_map
from tests.source_tools import module_tree


class MainFacadeTest(unittest.TestCase):
    def test_main_is_a_small_application_facade(self) -> None:
        lines = module_map.MAIN.read_text(encoding="utf-8-sig").splitlines()
        self.assertLessEqual(len(lines), 200)

        functions = {
            node.name
            for node in module_tree(module_map.MAIN).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(functions, {"_include_router"})

    def test_package_modules_do_not_import_main(self) -> None:
        violations: list[str] = []
        for path in sorted(Path(module_map.PACKAGE_DIR).rglob("*.py")):
            tree = ast.parse(
                path.read_text(encoding="utf-8-sig"),
                filename=str(path),
            )
            imports_main = any(
                isinstance(node, ast.Import)
                and any(alias.name == "main" for alias in node.names)
                or isinstance(node, ast.ImportFrom)
                and node.module == "main"
                for node in ast.walk(tree)
            )
            if imports_main:
                violations.append(str(path.relative_to(module_map.REPO_ROOT)))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
