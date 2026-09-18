"""Regression tests for deterministic plan safety rules."""

from __future__ import annotations

import unittest

import main


class GeneratedPlanSafetyRulesTest(unittest.TestCase):
    def test_repeated_exercise_is_rejected_despite_markdown_or_numbering(self) -> None:
        violations = main.validate_generated_training_plan(
            "1. 深蹲：4组\n**深蹲**：3组\n",
            [],
        )
        self.assertTrue(any("重复安排动作" in item for item in violations))

    def test_different_exercise_variants_are_not_treated_as_duplicates(self) -> None:
        violations = main.validate_generated_training_plan(
            "杠铃深蹲：4组\n高脚杯深蹲：3组\n",
            [],
        )
        self.assertFalse(any("重复安排动作" in item for item in violations))

    def test_explicit_health_restriction_rejects_the_named_exercise(self) -> None:
        violations = main.validate_generated_training_plan(
            "深蹲：4组\n",
            [],
            safety_constraints=["膝盖疼痛，避免深蹲"],
        )
        self.assertTrue(any("健康限制" in item and "深蹲" in item for item in violations))

    def test_health_region_restriction_rejects_mapped_exercise(self) -> None:
        violations = main.validate_generated_training_plan(
            "哑铃罗马尼亚硬拉：3组\n",
            [],
            safety_constraints=["腰部不适"],
        )
        self.assertTrue(any("健康限制" in item and "腰部不适" in item for item in violations))

    def test_unmapped_health_fact_does_not_invent_an_exercise_ban(self) -> None:
        violations = main.validate_generated_training_plan(
            "深蹲：4组\n",
            [],
            safety_constraints=["高血压"],
        )
        self.assertFalse(any("健康限制" in item for item in violations))

    def test_negated_exercise_line_does_not_trigger_health_restriction(self) -> None:
        violations = main.validate_generated_training_plan(
            "本次避免深蹲，替换为臀桥。\n臀桥：3组\n",
            [],
            safety_constraints=["膝盖疼痛，避免深蹲"],
        )
        self.assertFalse(any("健康限制" in item for item in violations))

    def test_channel_validation_uses_the_same_decorated_name_matching(self) -> None:
        memories = [{
            "user_confirmed": True,
            "facts": [{
                "namespace": "youtube", "key": "avoid_channel",
                "value": "那个AthleanX频道", "status": "active", "user_confirmed": True,
            }],
        }]
        violations = main.validate_generated_training_plan(
            "教学视频来源：ATHLEAN-X™\n",
            memories,
        )
        self.assertTrue(any("YouTube 频道" in item for item in violations))


if __name__ == "__main__":
    unittest.main()
