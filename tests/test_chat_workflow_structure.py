from __future__ import annotations

import ast
import unittest

from tests import module_map
from tests.source_tools import function_node, module_tree


class ChatWorkflowStructureTest(unittest.TestCase):
    def test_workflow_has_explicit_state_and_result_types(self) -> None:
        classes = {
            node.name for node in module_tree(module_map.CHAT_WORKFLOW).body
            if isinstance(node, ast.ClassDef)
        }
        self.assertTrue({"ChatTurn", "ChatResult"}.issubset(classes))

    def test_workflow_never_constructs_http_responses(self) -> None:
        tree = module_tree(module_map.CHAT_WORKFLOW)
        imported = {
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(any(name.startswith("fastapi") for name in imported))
        self.assertNotIn("JSONResponse", called)

    def test_route_is_only_request_parse_workflow_and_response_adaptation(self) -> None:
        route = function_node(module_map.CHAT_ROUTE, "chat")
        calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)]
        names = {
            node.func.id for node in calls if isinstance(node.func, ast.Name)
        }
        loaded = {
            node.id for node in ast.walk(route)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        self.assertTrue({"read_chat_request", "run_chat_workflow", "JSONResponse"}.issubset(names))
        self.assertNotIn("deps", loaded)

    def test_named_workflow_stage_boundaries_exist(self) -> None:
        functions = {
            node.name for node in module_tree(module_map.CHAT_WORKFLOW).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue({
            "stage_request_context", "stage_health_safety", "stage_local_navigation",
            "stage_intent_routing", "stage_plan_context", "stage_postprocess",
        }.issubset(functions))


if __name__ == "__main__":
    unittest.main()
