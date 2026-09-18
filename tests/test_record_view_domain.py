from __future__ import annotations

import unittest

from fithealth_agent.domain.record_view import (
    _nutrition_record_items,
    _training_record_items,
)


class RecordViewDomainTest(unittest.TestCase):
    def test_training_projection_accepts_preloaded_records(self) -> None:
        records = [{
            "id": "r1", "date": "2026-08-28", "category": "训练",
            "created_at": "2026-08-28T10:00:00+08:00",
            "record": {
                "sport": "力量训练",
                "segments": [{
                    "segment_type": "set_active", "is_rest": False,
                    "start_time": "2026-08-28T09:00:00+08:00",
                }],
            },
        }]
        items = _training_record_items(records, "2026-08-28")
        self.assertEqual(items[0]["id"], "r1")
        self.assertEqual(items[0]["name"], "26-08-28-09-00-力量训练")

    def test_nutrition_projection_accepts_preloaded_records(self) -> None:
        records = [{
            "id": "n1", "date": "2026-08-28", "category": "daily_checkin",
            "record": {"calories_kcal": 500, "protein_g": 30, "meal_slot": "午餐"},
        }]
        items = _nutrition_record_items(records, "2026-08-28")
        self.assertEqual(items[0]["id"], "n1::manual")
        self.assertEqual(items[0]["total_kcal"], 500)


if __name__ == "__main__":
    unittest.main()
