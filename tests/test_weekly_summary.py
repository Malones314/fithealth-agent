from __future__ import annotations

import unittest
from datetime import date, timedelta

from fithealth_agent.weekly_summary import build_current_week_reply


class FakeHealthStore:
    def get_health_range(self, start_date: str, end_date: str) -> list[dict]:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        items = []
        for offset in range((end - start).days + 1):
            day = (start + timedelta(days=offset)).isoformat()
            item = {
                "date": day,
                "heart_rate": None,
                "stress": None,
                "respiration": None,
                "spo2": None,
                "hrv": None,
                "activity": None,
                "sleep": None,
            }
            if day == "2026-08-13":
                item.update(
                    {
                        "heart_rate": {"min": 52, "max": 145, "avg": 72.4},
                        "stress": {"avg": 24.5},
                        "hrv": {"last_night_average": 46},
                        "activity": {"steps": 8123},
                        "sleep": {"duration_min": 408},
                    }
                )
            items.append(item)
        return items


class FakeDailyRecordStore:
    def list_records(self) -> list[dict]:
        return [
            {"date": "2026-08-13", "category": "training", "record": {}},
            {"date": "2026-08-09", "category": "training", "record": {}},
        ]


class WeeklySummaryTest(unittest.TestCase):
    def test_builds_current_week_summary_without_llm(self) -> None:
        result = build_current_week_reply(
            "查看本周数据",
            health_store=FakeHealthStore(),
            daily_record_store=FakeDailyRecordStore(),
            profile={"weekly_weight_kg": [79.8, 79.5]},
            today=date(2026, 8, 14),
        )

        self.assertIn("2026-08-10 至 2026-08-14", result)
        self.assertIn("6小时48分", result)
        self.assertIn("72.4 (52-145) bpm", result)
        self.assertIn("8123", result)
        self.assertIn("已导入健康数据：1/5 天", result)
        self.assertIn("每日记录：共 1 条", result)
        self.assertIn("平均 79.7 kg", result)

    def test_does_not_intercept_weekly_training_plan_request(self) -> None:
        result = build_current_week_reply(
            "帮我制定本周训练计划",
            health_store=FakeHealthStore(),
            daily_record_store=FakeDailyRecordStore(),
            profile={},
            today=date(2026, 8, 14),
        )
        self.assertIsNone(result)

    def test_labels_photo_meal_estimates_separately_from_manual_nutrition(self) -> None:
        class MealsStore:
            def list_records(self) -> list[dict]:
                return [{
                    "date": "2026-08-13", "category": "daily_checkin", "record": {
                        "meal_estimates": [{
                            "source": "food_photo_estimate", "total_kcal": 520,
                            "protein_g": 35.5, "carbs_g": 61.2, "fat_g": 14.3,
                        }],
                    },
                }, {
                    "date": "2026-08-14", "category": "daily_checkin", "record": {"calories_kcal": 1800},
                }]

        result = build_current_week_reply(
            "查看本周营养数据", health_store=FakeHealthStore(), daily_record_store=MealsStore(), profile={},
            today=date(2026, 8, 14),
        )
        self.assertIn("图片估算 1 餐", result)
        self.assertIn("手动营养记录 1 天", result)


if __name__ == "__main__":
    unittest.main()
