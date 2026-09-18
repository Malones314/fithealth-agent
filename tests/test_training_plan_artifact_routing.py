from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import function_node, load_symbols
from fithealth_agent.domain.intent_rules import is_explicit_training_plan_request


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_truncation_check():
    """加载真实的 contains_truncation_marker，而不是写一个会漂移的桩。

    直接按文件路径加载 context_budget，绕开 fithealth_agent/__init__.py
    对 LLM 依赖链的急切导入（ARCH-02）。
    """
    path = REPO_ROOT / "fithealth_agent" / "context_budget.py"
    spec = importlib.util.spec_from_file_location("context_budget_for_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load context_budget")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.contains_truncation_marker


class TrainingPlanArtifactRoutingTest(unittest.TestCase):
    def test_compound_recovery_message_is_an_explicit_plan_request(self) -> None:
        self.assertTrue(is_explicit_training_plan_request(
            "上周我感冒了，没有进行训练，我现在已经恢复了，为我设计我今天的训练计划"
        ))

    def test_existing_plan_and_diagnostic_questions_are_not_new_plan_requests(self) -> None:
        for message in (
            "保存刚才的训练计划",
            "查看今天的训练计划",
            "为什么生成的训练计划没有保存到本地的选项",
        ):
            with self.subTest(message=message):
                self.assertFalse(is_explicit_training_plan_request(message))

    def test_chat_plan_artifact_requires_a_complete_plan_reply(self) -> None:
        chat = function_node(consumer_home("chat"), "chat")
        source = ast.unparse(chat)
        self.assertIn("chat_intent.create_training_plan", source)
        self.assertIn("chat_intent.training_plan_title", source)
        self.assertEqual(source.count("looks_like_complete_training_plan(answer)"), 3)
        self.assertIn(
            "chat_intent.create_training_plan and looks_like_complete_training_plan(answer)",
            source,
        )
        self.assertIn("scheduled_plan is not None and looks_like_complete_training_plan(answer)", source)

    def test_auto_correction_uses_a_fresh_agent_instance(self) -> None:
        chat = function_node(consumer_home("chat"), "chat")
        assignments = {
            target.id: node.value
            for node in ast.walk(chat)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertEqual(
            ast.unparse(assignments["correction_agent"]),
            # role 是 agent-trace 阶段 5 加的：两个循环共享一个 turn_id，事件的 span
            # 就是 role，少传这个实参会让修正循环的事件混进主循环。
            "deps.create_fithealth_agent(avoid_youtube_channels=avoided_youtube_channels,"
            " role='correction_agent')",
        )
        correction_calls = [
            node
            for node in ast.walk(chat)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "run_in_threadpool"
            and node.args
            and ast.unparse(node.args[0]) == "correction_agent.run"
        ]
        self.assertEqual(len(correction_calls), 1)

    def test_existing_plan_save_uses_latest_complete_assistant_plan(self) -> None:
        # TRAINING_SUBJECT_RULES 不用注入：两个函数各自在函数体内从 muscle_map
        # import 它，注入的桩会被局部 import 覆盖掉。
        # looks_like_complete_training_plan 现在会先排除被上下文预算截断的残片
        # （BUG-05）。contains_truncation_marker 注入真实实现而不是桩，避免漂移。
        namespace = load_symbols(
            {"looks_like_complete_training_plan", "most_recent_complete_training_plan"},
            namespace={"contains_truncation_marker": _load_truncation_check()},
        )
        complete_plan = (
            "# 背部训练\n\n热身\n哑铃划船 4 组 × 10 次\n"
            "胸支撑划船 3 组 × 12 次\n拉伸\n" + ("动作要点：核心收紧，腰背中立。\n" * 20)
        )
        history = [
            {"role": "assistant", "text": complete_plan},
            {"role": "user", "text": "今天是8-18"},
            {"role": "assistant", "text": "日期已更新，内容不重复。"},
        ]
        selected = namespace["most_recent_complete_training_plan"](history)
        self.assertTrue(selected.startswith("# 背部训练"))
        self.assertNotIn("内容不重复", selected)

if __name__ == "__main__":
    unittest.main()
