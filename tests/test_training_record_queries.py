from __future__ import annotations

import unittest
from pathlib import Path

from tests.source_tools import load_symbols


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_functions():
    """按 tests/module_map.py 查表抽函数，避免 import main 拉起整条 LLM 依赖链。"""
    return load_symbols(
        {
            "is_training_record_query",
            "_requested_record_date",
            "_training_record_name",
            "extract_iso_dates",
        }
    )


class TrainingRecordQueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        functions = load_functions()
        cls.is_query = staticmethod(functions["is_training_record_query"])
        cls.requested_date = staticmethod(functions["_requested_record_date"])
        cls.record_name = staticmethod(functions["_training_record_name"])

    def test_record_requests_are_not_training_plan_requests(self) -> None:
        self.assertTrue(self.is_query("查看训练记录"))
        self.assertTrue(self.is_query("帮我查询我的训练记录"))
        self.assertTrue(self.is_query("回顾运动记录"))
        self.assertFalse(self.is_query("帮我制定跳绳训练计划"))

    def test_extracts_optional_requested_date(self) -> None:
        self.assertEqual(self.requested_date("查看 2026-08-13 的训练记录"), "2026-08-13")
        self.assertIsNone(self.requested_date("查看今天的训练记录"))

    def test_legacy_record_name_uses_first_active_segment_time(self) -> None:
        record = {
            "sport": "力量训练",
            "segments": [
                {"is_rest": True, "start_time": "2026-08-13T08:30:00+08:00"},
                {"is_rest": False, "start_time": "2026-08-13T09:05:00+08:00"},
                {"is_rest": False, "start_time": "2026-08-13T09:25:00+08:00"},
            ],
        }
        self.assertEqual(self.record_name(record, "2026-08-13"), "26-08-13-09-05-力量训练")


if __name__ == "__main__":
    unittest.main()
