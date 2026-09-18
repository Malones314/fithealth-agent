from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fithealth_agent.plan_goal_validator import (
    normalize_requested_subjects,
    validate_plan_goal_alignment,
)


PLAN = """# 今日心肺与腹部稳定训练

## 热身
动态热身 5 分钟

## 主训练
跑步：3 组，每组 5 分钟
平板支撑：3 组，每组 30 秒
死虫：3 组，每组 12 次

## 拉伸
拉伸 5 分钟
"""


class PlanGoalValidatorTest(unittest.TestCase):
    def test_normalizes_compound_chinese_subject(self) -> None:
        self.assertEqual(
            normalize_requested_subjects([], "有氧和核心训练"),
            ["有氧", "核心"],
        )

    def test_local_fallback_accepts_semantic_action_evidence(self) -> None:
        result = validate_plan_goal_alignment(
            PLAN,
            requested_subjects=["有氧", "核心"],
            fallback_subject="有氧和核心训练",
            schedule_decision="override_today",
            allow_external_models=False,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_subjects"], [])

    def test_explicit_request_wins_over_conflicting_weekly_subject(self) -> None:
        result = validate_plan_goal_alignment(
            PLAN,
            requested_subjects=["有氧", "核心"],
            weekly_subject="腿部训练",
            schedule_decision="override_today",
            allow_external_models=False,
        )
        self.assertTrue(result["passed"])

    def test_follow_schedule_requires_weekly_subject(self) -> None:
        result = validate_plan_goal_alignment(
            PLAN,
            requested_subjects=[],
            weekly_subject="腿部训练",
            schedule_decision="follow_schedule",
            allow_external_models=False,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_subjects"], ["腿部"])

    def test_lite_model_must_account_for_every_target(self) -> None:
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "passed": True,
                "matched_subjects": ["有氧"],
                "missing_subjects": [],
                "reason": "只返回了一个目标",
            }, ensure_ascii=False)}}]
        }
        with patch.dict("os.environ", {"LLM_LITE_API_KEY": "test-key"}):
            result = validate_plan_goal_alignment(
                PLAN,
                requested_subjects=["有氧", "核心"],
                schedule_decision="override_today",
                requester=unittest.mock.Mock(return_value=response),
            )
        self.assertEqual(result["stage"], "local_evidence")
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
