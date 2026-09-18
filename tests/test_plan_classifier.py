from __future__ import annotations

import unittest
from unittest.mock import patch

from fithealth_agent import plan_classifier


class TrainingPlanValidatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = plan_classifier

    def test_keywords_never_directly_accept_a_document(self) -> None:
        note = "今天卧推、深蹲和硬拉都做了，重量比上周高，热身后拉伸。"
        with patch.object(self.validator, "_level2_llm_check", return_value=(False, "这是训练复盘")) as check:
            result = self.validator.validate_training_plan(note)
        check.assert_called_once_with(note)
        self.assertFalse(result["is_plan"])
        self.assertEqual(result["stage"], "level2_llm")

    def test_irrelevant_document_is_rejected_without_model(self) -> None:
        with patch.object(self.validator, "_level2_llm_check") as check:
            result = self.validator.validate_training_plan("周末旅行与阅读安排")
        check.assert_not_called()
        self.assertFalse(result["is_plan"])
        self.assertEqual(result["stage"], "level1_keywords")


if __name__ == "__main__":
    unittest.main()
