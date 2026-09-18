from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fithealth_agent import health_safety, information_router, plan_classifier


REPO_ROOT = Path(__file__).resolve().parents[1]


class ExternalModelControlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = information_router
        cls.validator = plan_classifier

    def test_logout_summary_does_not_call_llm_when_disabled(self) -> None:
        messages = [{"role": "user", "text": "今天训练后腿部疲劳，明天减少训练量"}]
        with patch.object(self.router, "_level3_llm_decide") as decide:
            result = self.router.route_information(messages, allow_external_models=False)
        decide.assert_not_called()
        self.assertFalse(result["save"])
        self.assertEqual(result["pipeline_stage"], "external_models_disabled")

    def test_ambiguous_plan_does_not_call_llm_when_disabled(self) -> None:
        text = "本周安排三次运动，每次约四十分钟。"
        with patch.object(self.validator, "_level2_llm_check") as decide:
            result = self.validator.validate_training_plan(text, allow_external_models=False)
        decide.assert_not_called()
        self.assertFalse(result["is_plan"])
        self.assertEqual(result["stage"], "external_models_disabled")

    def test_keyword_matched_plan_requires_semantic_validation_when_disabled(self) -> None:
        text = "卧推 4 组，深蹲 4 组，硬拉 3 组，热身后拉伸。"
        result = self.validator.validate_training_plan(text, allow_external_models=False)
        self.assertFalse(result["is_plan"])
        self.assertEqual(result["stage"], "external_models_disabled")

    def test_invalid_model_response_never_uses_a_local_memory_fallback(self) -> None:
        text = "用户说跳绳也是可用器械，并希望在训练计划中加入跳绳。"
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            self.router.requests if hasattr(self.router, "requests") else __import__("requests"),
            "post",
        ) as post:
            response = post.return_value
            response.json.return_value = {"choices": [{"message": {"content": "not a tool call"}}]}
            response.raise_for_status.return_value = None
            result = self.router._level3_llm_decide(text)
        self.assertFalse(result["save"])
        self.assertEqual(result["summary"], "")
        self.assertIn("未明确请求保存", result["reason"])


class HealthStatementGateTest(unittest.TestCase):
    """`classify_user_health_statement` 必须受"外部模型"开关管辖。

    这个闸门原先**完全不存在**：关掉开关之后它照样把用户消息发给外部模型，而 README
    的"外部数据传输"一节明确承诺开关控制哪些信息离开本机。其余 6 个模型触点都有这道
    检查，只有它漏了。缺陷能活下来是因为成功与失败都静默返回 `None`——是 agent-trace
    阶段 4 的 `model_call` 事件把它照出来的。
    """

    def test_no_request_is_made_when_external_models_are_disabled(self) -> None:
        requester = Mock()
        result = health_safety.classify_user_health_statement(
            "我膝盖疼得走不了路",
            allow_external_models=False,
            requester=requester,
            api_key="test-key",
        )
        requester.assert_not_called()
        # 返回 None（"不确定"）而不是 False：False 会让调用方**跳过**确定性的本地风险
        # 筛查，等于关掉联网模型顺带关掉本地安全网，与 AGENT-01 的意图相反。
        self.assertIsNone(result)

    def test_the_chat_workflow_passes_the_switch_through(self) -> None:
        """光有参数不够——调用点不传就等于没有这道闸门。

        用 AST 结构断言：找到 `chat()` 里那次调用，检查它带了
        `allow_external_models` 关键字参数。
        """
        source = (REPO_ROOT / "fithealth_agent" / "workflows" / "chat_workflow.py")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        keywords: list[list[str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            names = {
                item.attr
                for item in ast.walk(node)
                if isinstance(item, ast.Attribute) and item.attr == "classify_user_health_statement"
            }
            if names:
                keywords.append(sorted(kw.arg or "**" for kw in node.keywords))
        self.assertEqual(keywords, [["allow_external_models"]])

    def test_the_classifier_leaves_room_for_a_complete_json_answer(self) -> None:
        """`max_tokens` 曾经是 40，模型写不完 JSON 就被截断，于是几乎必然解析失败。

        实测证据：`completion_tokens` 恰好等于 40 且内容 `json.loads` 失败。
        """
        captured: dict[str, object] = {}

        def requester(_url, **kwargs):
            captured.update(kwargs.get("json") or {})
            raise RuntimeError("stop here")

        health_safety.classify_user_health_statement(
            "我膝盖疼", api_key="test-key", requester=requester
        )
        self.assertGreaterEqual(int(captured["max_tokens"]), 200)


if __name__ == "__main__":
    unittest.main()
