from __future__ import annotations

import unittest
from unittest.mock import patch

from fithealth_agent import information_router


class InformationRouterMemorySafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = information_router

    def test_generic_training_question_is_decided_by_function_calling(self) -> None:
        messages = [{"role": "user", "text": "深蹲应该做几组？"}]
        with patch.object(self.router, "_level3_llm_decide", return_value={
            "save": False, "reason": "摘要模型未明确请求保存", "summary": "",
            "type": "training_feedback", "importance": 1, "facts": [],
        }) as decide:
            result = self.router.route_information(messages)
        decide.assert_called_once()
        self.assertFalse(result["save"])
        self.assertEqual(result["pipeline_stage"], "level3")

    def test_explicit_sedentary_pelvic_tilt_and_future_intensity_reaches_memory_model(self) -> None:
        messages = [{
            "role": "user",
            "text": "以后练胸的强度低一些，我是久坐的人，有些盆骨前倾",
        }]
        with patch.object(self.router, "_level3_llm_decide", return_value={
            "save": False, "reason": "test", "summary": "", "type": "training_feedback",
            "importance": 1, "facts": [],
        }) as decide:
            result = self.router.route_information(messages)
        decide.assert_called_once()
        self.assertEqual(result["pipeline_stage"], "level3")

    def test_semantic_fact_without_predefined_keyword_still_reaches_function_calling(self) -> None:
        messages = [{"role": "user", "text": "推举时我的右手腕需要保持中立位"}]
        with patch.object(self.router, "_level3_llm_decide", return_value={
            "save": False, "reason": "test", "summary": "", "type": "training_feedback",
            "importance": 1, "facts": [],
        }) as decide:
            self.router.route_information(messages)
        decide.assert_called_once()

    def test_valid_tool_call_creates_confirmable_memory(self) -> None:
        text = "[user] 我不喜欢跳绳，以后训练计划不要安排跳绳。"
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            self.router.requests if hasattr(self.router, "requests") else __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户不喜欢跳绳，后续计划避免安排跳绳。","type":"preference","importance":4,"avoid_youtube_channels":[],"facts":[{"namespace":"training","key":"avoid_exercise","value":"跳绳","status":"active","evidence":"不喜欢跳绳"}]}',
            }}]}}]}
            result = self.router._level3_llm_decide(text)
        self.assertTrue(result["save"])
        self.assertEqual(result["type"], "preference")
        self.assertEqual(result["facts"][0]["key"], "avoid_exercise")

    def test_negative_exercise_preference_is_rejected_before_persistence(self) -> None:
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            self.router.requests if hasattr(self.router, "requests") else __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户不喜欢深蹲。","type":"preference","importance":4,"avoid_youtube_channels":[],"facts":[{"namespace":"training","key":"prefer_exercise","value":"我不喜欢深蹲","status":"active","evidence":"不喜欢深蹲"}]}',
            }}]}}]}
            result = self.router._level3_llm_decide("[user] 我不喜欢深蹲", user_text="我不喜欢深蹲")
        self.assertFalse(result["save"])
        self.assertEqual(result["rejected_facts"], 1)

    def test_weekly_schedule_tool_fact_is_retained_for_confirmation(self) -> None:
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            self.router.requests if hasattr(self.router, "requests") else __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户确认周二背部训练。","type":"plan_decision","importance":5,"avoid_youtube_channels":[],"facts":[{"namespace":"plan","key":"weekly_schedule","value":"{\\"mon\\":\\"胸部训练\\",\\"tue\\":\\"背部训练\\"}","status":"active","evidence":"周一练胸、周二练背"}]}',
            }}]}}]}
            result = self.router._level3_llm_decide("[user] 周一练胸、周二练背，以后按这个安排")
        self.assertTrue(result["save"])
        self.assertEqual(result["facts"][0]["key"], "weekly_schedule")

    def test_rejects_tool_call_without_structured_facts(self) -> None:
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            self.router.requests if hasattr(self.router, "requests") else __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户偏好器械训练","type":"preference","importance":3,"avoid_youtube_channels":[]}',
            }}]}}]}
            result = self.router._level3_llm_decide("[user] 用户偏好器械训练")
        self.assertFalse(result["save"])

    def test_rejects_fact_whose_evidence_is_not_in_user_text(self) -> None:
        text = "[user] 我今天练胸感觉还可以"
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户希望胸部训练保持当前强度","type":"preference","importance":3,"avoid_youtube_channels":[],"facts":[{"namespace":"plan","key":"preference","value":"胸部训练保持当前强度","status":"active","evidence":"以后胸部训练保持当前强度"}]}'
            }}]}}]}
            result = self.router._level3_llm_decide(text)
        self.assertFalse(result["save"])
        self.assertEqual(result["rejected_facts"], 1)

    def test_rejects_evidence_copied_from_assistant_message(self) -> None:
        text = "[user] 我想减脂\n[assistant] 你的膝盖疼痛需要注意"
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户有膝盖疼痛","type":"constraint","importance":4,"avoid_youtube_channels":[],"facts":[{"namespace":"health","key":"injury_or_constraint","value":"膝盖疼痛","status":"active","evidence":"你的膝盖疼痛需要注意"}]}'
            }}]}}]}
            result = self.router._level3_llm_decide(text, user_text="我想减脂")
        self.assertFalse(result["save"])
        self.assertEqual(result["rejected_facts"], 1)

    def test_keeps_supported_facts_when_one_of_multiple_facts_lacks_evidence(self) -> None:
        text = "[user] 我是久坐的人，有些骨盆前倾"
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户久坐且骨盆前倾并且膝盖疼痛","type":"constraint","importance":4,"avoid_youtube_channels":[],"facts":[{"namespace":"health","key":"injury_or_constraint","value":"久坐","status":"active","evidence":"我是久坐的人"},{"namespace":"health","key":"injury_or_constraint","value":"膝盖疼痛","status":"active","evidence":"膝盖疼痛"}]}'
            }}]}}]}
            result = self.router._level3_llm_decide(text)
        self.assertTrue(result["save"])
        self.assertEqual(result["rejected_facts"], 1)
        self.assertEqual(len(result["facts"]), 1)
        self.assertEqual(result["facts"][0]["value"], "久坐")

    def test_latin_evidence_cannot_match_after_removing_literal_letters(self) -> None:
        self.assertFalse(
            self.router._evidence_supported(
                "train hard", "I do rain hard workouts"
            )
        )

    def test_temporary_health_fact_keeps_structured_validity_window(self) -> None:
        text = "[user] 我最近膝盖疼"
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            __import__("requests"), "post"
        ) as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "save_memory",
                "arguments": '{"summary":"用户最近膝盖疼","type":"training_feedback","importance":4,"avoid_youtube_channels":[],"facts":[{"namespace":"health","key":"recovery_status","value":"膝盖疼痛","status":"active","evidence":"我最近膝盖疼","duration_type":"temporary","valid_from":"2026-08-19","valid_until":"2026-09-02"}]}'
            }}]}}]}
            result = self.router._level3_llm_decide(text)
        self.assertTrue(result["save"])
        fact = result["facts"][0]
        self.assertEqual(fact["duration_type"], "temporary")
        self.assertEqual(fact["valid_until"], "2026-09-02")

    def test_model_failure_never_persists_raw_conversation(self) -> None:
        with patch.object(self.router, "LLM_LITE_API_KEY", "test-key"), patch.object(
            self.router.requests if hasattr(self.router, "requests") else __import__("requests"),
            "post", side_effect=RuntimeError("offline"),
        ):
            result = self.router._level3_llm_decide("[user] 今天练完腿很累")
        self.assertFalse(result["save"])
        self.assertEqual(result["summary"], "")


if __name__ == "__main__":
    unittest.main()
