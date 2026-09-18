"""AGENT-01（健康风险拦截）与 BUG-01（安全约束死代码）的回归测试。

这两条必须一起测：BUG-01 的拦截以前是死代码，一旦接通，原来那条宽正则
（含「不管」）会立刻把正常提问挡在 409 外面。所以既要测"该拦的拦住"，
也要测"不该拦的放行"。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

from tests.module_map import consumer_home, function_home
from tests.source_tools import load_symbol, module_source


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


health_safety = load_module("health_safety_under_test", PACKAGE_DIR / "health_safety.py")


# ══════════════════════════════════════════════════════════════════════════
# AGENT-01：健康风险筛查
# ══════════════════════════════════════════════════════════════════════════


class EmergencyScreeningTest(unittest.TestCase):
    def test_lite_classifier_handles_subject_and_negation_semantics(self) -> None:
        class Response:
            def raise_for_status(self):
                return None
            def __init__(self, value):
                self.value = value
            def json(self):
                return {"choices": [{"message": {"content": '{"user_symptom": ' + str(self.value).lower() + '}'}}]}

        values = iter((False, False, True))
        def requester(*_args, **_kwargs):
            return Response(next(values))

        self.assertFalse(health_safety.classify_user_health_statement("我儿子手臂痛", requester=requester, api_key="x"))
        self.assertFalse(health_safety.classify_user_health_statement("我不觉得手臂痛", requester=requester, api_key="x"))
        self.assertTrue(health_safety.classify_user_health_statement("我手臂现在很痛", requester=requester, api_key="x"))

    def test_lite_classifier_treats_explicit_recovery_as_a_health_update(self) -> None:
        captured = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": '{"user_health_update": true}'}}]}

        def requester(*_args, **kwargs):
            captured.update(kwargs)
            return Response()

        self.assertTrue(health_safety.classify_user_health_statement(
            "大腿内侧已恢复", requester=requester, api_key="x"
        ))
        prompt = captured["json"]["messages"][0]["content"]
        self.assertIn("此前症状已经恢复", prompt)
        self.assertIn("我不觉得手臂痛", prompt)

    def assert_level(self, text: str, expected: str | None) -> None:
        finding = health_safety.screen_health_risk(text)
        actual = finding.level if finding else None
        self.assertEqual(actual, expected, f"{text!r} 期望 {expected}，实际 {actual}")

    def test_cardiac_and_neuro_red_flags_are_emergency(self) -> None:
        for text in (
            "我练到一半胸口疼，还有点晕，明天该练什么？",
            "刚才做深蹲的时候眼前发黑差点晕倒",
            "跑步时喘不上气，正常吗",
            "训练完突然说话不清楚，右边胳膊也没力气",
            "胸闷得厉害，帮我看看今天还能不能练",
        ):
            with self.subTest(text=text):
                self.assert_level(text, health_safety.EMERGENCY)

    def test_natural_chinese_red_flags_allow_short_modifiers(self) -> None:
        for text, expected in (
            ("胸口很痛", health_safety.EMERGENCY),
            ("胸口有点疼", health_safety.EMERGENCY),
            ("胸部隐隐作痛", health_safety.EMERGENCY),
            ("我胸口压得难受", health_safety.EMERGENCY),
            ("我的腰疼得特别厉害", health_safety.URGENT),
            ("心跳得很快", health_safety.CAUTION),
            ("膝盖非常肿", health_safety.URGENT),
            ("刚才差点晕过去", health_safety.EMERGENCY),
            ("我有点站不稳", health_safety.EMERGENCY),
        ):
            with self.subTest(text=text):
                self.assert_level(text, expected)

    def test_negation_and_history_do_not_cross_clause_boundaries(self) -> None:
        for text in (
            "我最近没睡好，今天胸口很痛",
            "以前练腿都没事，今天胸口发闷",
            "上次是膝盖，这次胸痛得厉害",
            "没有力气了，感觉快要晕倒",
        ):
            with self.subTest(text=text):
                self.assert_level(text, health_safety.EMERGENCY)

    def test_unresolved_symptoms_are_not_treated_as_negated(self) -> None:
        for text in (
            "未见好转的胸口痛",
            "尚未缓解的胸口痛",
            "没有缓解的胸口痛",
            "并未好转的胸口痛",
            "未曾缓解的胸口痛",
            "不会缓解的胸口痛",
            "无法缓解的胸口痛",
            "毫无改善的胸口痛",
            "胸口痛一直未见好转",
        ):
            with self.subTest(text=text):
                self.assert_level(text, health_safety.EMERGENCY)

        for text in (
            "没有胸痛",
            "无胸痛",
            "无明显胸痛",
            "未见胸痛",
            "尚未出现胸痛",
            "从未有过胸痛",
        ):
            with self.subTest(text=text):
                self.assert_level(text, None)

    def test_natural_stroke_wording_distinguishes_unclear_from_clear_speech(self) -> None:
        for text in (
            "说话不太清楚",
            "说话不怎么清楚",
            "说话不够清楚",
            "说话不是很清楚",
        ):
            with self.subTest(text=text):
                self.assert_level(text, health_safety.EMERGENCY)
        self.assert_level("说话很清楚", None)

    def test_acute_injury_is_urgent(self) -> None:
        for text in (
            "我膝盖肿了，走不了路，帮我出个腿部计划",
            "卧推时肩膀听到一声响然后很疼",
            "脚踝扭到了，疼得受不了",
            "我腿断了",
            "骨头折了，不能负重",
        ):
            with self.subTest(text=text):
                self.assert_level(text, health_safety.URGENT)

    def test_moderate_signals_are_caution(self) -> None:
        for text in ("今天有点头晕，帮我安排训练", "最近静息心率偏高", "有点低烧，还能练吗"):
            with self.subTest(text=text):
                self.assert_level(text, health_safety.CAUTION)

    def test_negated_past_and_hypothetical_are_not_flagged(self) -> None:
        """误报会让人很快关掉这个功能，所以否定/既往/假设语境必须放行。"""
        for text in (
            "今天状态不错，胸口不疼了，可以正常练",
            "我没有胸痛，只是有点累",
            "以前有过晕厥，现在早就好了",
            "如果训练时胸痛会不会很危险？",
            "上次那个膝盖肿胀已经消失了",
        ):
            with self.subTest(text=text):
                self.assert_level(text, None)

    def test_third_party_symptoms_are_not_attributed_to_user(self) -> None:
        for text in (
            "我朋友胸口痛",
            "同事刚才差点晕倒",
            "我妈妈呼吸有点困难",
            "她说话不太清楚",
        ):
            with self.subTest(text=text):
                self.assert_level(text, None)
        self.assert_level("我朋友问我胸口痛怎么办", health_safety.EMERGENCY)
        self.assert_level("我朋友胸口痛，但我自己也胸闷", health_safety.EMERGENCY)

    def test_ordinary_training_talk_is_not_flagged(self) -> None:
        for text in (
            "帮我安排一个胸部训练",
            "今天练胸感觉还可以",
            "深蹲 100kg x 5，腿有点酸",
            "这周三我想把练背挪到练腿",
            "我的可用器械有哪些",
            "把第 2 和第 3 组合并",
        ):
            with self.subTest(text=text):
                self.assert_level(text, None)

    def test_highest_level_wins(self) -> None:
        finding = health_safety.screen_health_risk("膝盖有点肿，而且刚才胸口发闷")
        self.assertEqual(finding.level, health_safety.EMERGENCY)

    def test_merge_findings_keeps_highest_level_and_same_level_labels(self) -> None:
        urgent = health_safety.acute_pain_finding(["腿部", "腿部", "肩部"])
        caution = health_safety.screen_health_risk("心跳得很快")
        merged = health_safety.merge_findings(caution, urgent)
        self.assertEqual(merged.level, health_safety.URGENT)
        self.assertEqual(merged.labels, ("腿部剧烈疼痛", "肩部剧烈疼痛"))

        emergency = health_safety.screen_health_risk("胸口痛")
        self.assertEqual(
            health_safety.merge_findings(emergency, urgent).level,
            health_safety.EMERGENCY,
        )

    def test_emergency_and_urgent_block_plans_caution_does_not(self) -> None:
        emergency = health_safety.screen_health_risk("胸口疼")
        urgent = health_safety.screen_health_risk("膝盖肿了走不了路")
        caution = health_safety.screen_health_risk("今天有点头晕")
        self.assertTrue(emergency.blocks_training_plan())
        self.assertTrue(urgent.blocks_training_plan())
        self.assertFalse(caution.blocks_training_plan())

    def test_emergency_reply_is_actionable_and_refuses_training(self) -> None:
        finding = health_safety.screen_health_risk("胸口疼，还有点喘不上气")
        reply = health_safety.emergency_reply(finding)
        self.assertIn("120", reply)
        self.assertIn("停止", reply)
        self.assertIn("不会", reply)          # 明确拒绝给训练建议
        self.assertIn("不是医生", reply)      # 非诊断声明

    def test_directive_forbids_plan_generation_for_urgent(self) -> None:
        finding = health_safety.screen_health_risk("膝盖肿了，走不了路")
        directive = health_safety.risk_directive(finding)
        self.assertIn("禁止生成", directive)
        self.assertIn("最高优先级", directive)

    def test_screening_handles_empty_and_non_string(self) -> None:
        for value in ("", None, 123, []):
            with self.subTest(value=value):
                self.assertIsNone(health_safety.screen_health_risk(value))


# ══════════════════════════════════════════════════════════════════════════
# BUG-01：安全约束冲突拦截
# ══════════════════════════════════════════════════════════════════════════


class SafetyBypassPatternTest(unittest.TestCase):
    """直接测 main.py 里的正则与 resolve_plan_context，不启动 FastAPI。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 直接复用实现里的正则定义，避免测试自己再抄一份而悄悄漂移。
        cls.pattern = load_symbol("_SAFETY_BYPASS_PATTERN")

    def _search(self, text: str):
        return self.pattern.search(re.sub(r"\s+", "", text))

    def test_explicit_bypass_is_detected(self) -> None:
        for text in (
            "忽略我的腰伤，照常给我练腿",
            "不用管我的伤，正常安排",
            "跳过限制，直接上强度",
            "我的腰伤没关系，照常训练",
            "别管那些限制，硬练",
            "无视肩膀的限制，正常推",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(self._search(text), f"应识别为绕过安全限制：{text}")

    def test_common_chinese_filler_is_not_a_bypass(self) -> None:
        """「不管」曾经出现在正则里，是最主要的误报来源。"""
        for text in (
            "不管怎样都行，你安排吧",
            "不管练什么都可以",
            "今天不管练胸还是练背我都没问题",
            "我不太在意顺序，忽略顺序问题就行",
            "帮我安排今天的训练",
            "无视我的问题了吗",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self._search(text), f"不应判为绕过安全限制：{text}")


class ResolvePlanContextBlockingTest(unittest.TestCase):
    """blocking_reasons 以前从不被填充，导致拦截判断恒为假。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = module_source(function_home("resolve_plan_context"))

    def test_blocking_reasons_key_is_actually_populated(self) -> None:
        self.assertIn(
            '"blocking_reasons": blocking_reasons',
            self.main_source,
            "resolve_plan_context 必须真正返回 blocking_reasons",
        )

    def test_workflow_state_comes_from_plan_workflow(self) -> None:
        """状态不再由 main.py 手写字符串，避免两套状态机各写各的。"""
        self.assertIn('context["workflow_state"] = str(state_for_context(context))', self.main_source)
        self.assertNotIn('"workflow_state": "constraint_conflict" if safety_bypass', self.main_source)

    @unittest.skipIf(
        sys.version_info < (3, 11),
        "plan_workflow.py 使用 3.11+ 的 StrEnum；项目本身要求 3.11+",
    )
    def test_state_for_context_maps_blocking_reasons_to_conflict(self) -> None:
        plan_workflow = load_module("plan_workflow_under_test", PACKAGE_DIR / "plan_workflow.py")
        state = plan_workflow.state_for_context({"blocking_reasons": [{"rule": "腰伤"}]})
        self.assertEqual(str(state), "constraint_conflict")
        # 没有阻断原因时不应误报冲突
        self.assertEqual(str(plan_workflow.state_for_context({"blocking_reasons": []})), "ready_to_generate")


class ChatWiringTest(unittest.TestCase):
    """确认拦截确实接进了 /chat，而不是又变成一处死代码。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = module_source(consumer_home("chat"))

    def test_emergency_screening_runs_before_intent_routing(self) -> None:
        screen_at = self.main_source.index("health_risk = screen_health_risk(message)")
        route_at = self.main_source.index("deps.route_chat_intent,")
        self.assertLess(
            screen_at, route_at,
            "急症筛查必须在意图路由之前，才能在关闭外部模型时依然生效",
        )

    def test_risk_is_passed_into_agent_input(self) -> None:
        self.assertIn("risk=health_risk", self.main_source)

    def test_urgent_risk_blocks_plan_artifact(self) -> None:
        self.assertIn("health_risk.blocks_training_plan()", self.main_source)

    def test_prompt_contains_risk_grading(self) -> None:
        prompts = (PACKAGE_DIR / "prompts.py").read_text(encoding="utf-8")
        for token in ("健康安全规则", "胸痛", "晕厥", "120", "不做医学诊断"):
            with self.subTest(token=token):
                self.assertIn(token, prompts)


if __name__ == "__main__":
    unittest.main()
