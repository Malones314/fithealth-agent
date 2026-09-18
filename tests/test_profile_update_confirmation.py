from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import function_node


REPO_ROOT = Path(__file__).resolve().parents[1]


def _calls_profile_write(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """是否直接调用了 `deps.profile_store.update_profile`。

    阶段 1 之后 store 走 `deps.X` 属性访问，所以 func 是一层嵌套的 Attribute
    （`deps.profile_store.update_profile`），不再是 `Name('profile_store')` 打头。
    这里直接比 unparse 的结果，形状再变一次也不会悄悄变成永假。
    """
    return any(
        isinstance(node, ast.Call)
        and ast.unparse(node.func) == "deps.profile_store.update_profile"
        for node in ast.walk(function)
    )


class ProfileUpdateConfirmationFlowTest(unittest.TestCase):
    def test_chat_only_returns_profile_update_candidate(self) -> None:
        chat = function_node(consumer_home("chat"), "chat")
        self.assertFalse(_calls_profile_write(chat))
        self.assertIn("profile_update", ast.unparse(chat))

    def test_confirm_endpoint_is_the_only_profile_write_path(self) -> None:
        confirm = function_node(
            consumer_home("confirm_profile_update"), "confirm_profile_update"
        )
        self.assertTrue(_calls_profile_write(confirm))
        self.assertIn("validate_profile_tool_updates", ast.unparse(confirm))

if __name__ == "__main__":
    unittest.main()
