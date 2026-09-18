from __future__ import annotations

import ast
import unittest

from tests import module_map
from tests.source_tools import function_node, module_tree


class UploadWorkflowStructureTest(unittest.TestCase):
    def test_workflow_has_no_fastapi_or_json_response_imports(self) -> None:
        tree = module_tree(module_map.UPLOAD_WORKFLOW)
        imported = [
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ]
        self.assertFalse(any(name.startswith("fastapi") for name in imported))
        self.assertNotIn("JSONResponse", {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        })

    def test_route_adapters_construct_http_responses(self) -> None:
        for name in ("analyze_food", "upload_fit", "upload_plan", "upload_health", "upload_activity_from_health_zip"):
            node = function_node(module_map.UPLOADS_ROUTE, name)
            calls = {
                ast.unparse(call.func) for call in ast.walk(node)
                if isinstance(call, ast.Call)
            }
            with self.subTest(route=name):
                self.assertIn("JSONResponse", calls)

    def test_logout_route_is_thin_adapter(self) -> None:
        node = function_node(module_map.LOGOUT_ROUTE, "logout")
        calls = {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}
        self.assertIn("run_logout", calls)
        self.assertIn("JSONResponse", calls)


if __name__ == "__main__":
    unittest.main()
