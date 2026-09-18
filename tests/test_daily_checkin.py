from __future__ import annotations

import unittest
from pathlib import Path

from fithealth_agent import daily_checkin, storage


class DailyCheckinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.checkin = daily_checkin
        cls.storage = storage

    def test_normalizes_valid_structured_record(self) -> None:
        record_date, record = self.checkin.validate_daily_checkin({
            "date": "2026-08-15", "weight_kg": "79.6", "fatigue_level": "4",
            "training_rpe": "7", "cheat_meal": False, "note": "easy run",
        })
        self.assertEqual(record_date, "2026-08-15")
        self.assertEqual(record["weight_kg"], 79.6)
        self.assertEqual(record["fatigue_level"], 4)
        self.assertFalse(record["cheat_meal"])

    def test_rejects_empty_and_out_of_range_records(self) -> None:
        with self.assertRaises(ValueError):
            self.checkin.validate_daily_checkin({"date": "2026-08-15"})
        with self.assertRaises(ValueError):
            self.checkin.validate_daily_checkin({
                "date": "2026-08-15", "cheat_meal": False, "meal_estimates": [],
            })
        with self.assertRaises(ValueError):
            self.checkin.validate_daily_checkin({"date": "2026-08-15", "pain_level": 11})

    def test_preserves_structured_food_photo_estimates(self) -> None:
        _, record = self.checkin.validate_daily_checkin({
            "date": "2026-08-15",
            "meal_estimates": [{
                "source": "food_photo_estimate", "confidence": "medium", "user_confirmed": True,
                "items": [{"name": "rice", "portion": "1 bowl", "calories_kcal": 230,
                           "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5}],
                "total_kcal": 230, "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5,
                "range_low_kcal": 180, "range_high_kcal": 280, "assumptions": ["oil unknown"],
            }],
        })
        meal = record["meal_estimates"][0]
        self.assertEqual(meal["source"], "food_photo_estimate")
        self.assertEqual(meal["items"][0]["carbs_g"], 50.2)

    def test_explicit_nulls_and_empty_meals_are_valid_updates(self) -> None:
        day, record, cleared = self.checkin.validate_daily_checkin_update({
            "date": "2026-08-15", "weight_kg": None, "meal_estimates": [],
        })
        self.assertEqual(day, "2026-08-15")
        self.assertEqual(record["meal_estimates"], [])
        self.assertEqual(cleared, ["weight_kg"])

        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            store = self.storage.DailyRecordStore(Path(directory) / "records.json")
            store.upsert_dated_record(
                "2026-08-15", "daily_checkin", {"weight_kg": 70, "meal_estimates": [{"x": 1}]}
            )
            store.upsert_dated_record(
                "2026-08-15", "daily_checkin", record, clear_fields=tuple(cleared)
            )
            saved = store.list_records()[0]["record"]
            self.assertNotIn("weight_kg", saved)
            self.assertEqual(saved["meal_estimates"], [])

    def test_rejects_unconfirmed_low_confidence_meal(self) -> None:
        with self.assertRaisesRegex(ValueError, "明确确认"):
            self.checkin.validate_daily_checkin({
                "date": "2026-08-15",
                "meal_estimates": [{
                    "source": "food_photo_estimate", "confidence": "low", "user_confirmed": False,
                    "items": [{"name": "rice", "portion": "1 bowl", "calories_kcal": 230,
                               "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5}],
                    "total_kcal": 230, "protein_g": 4.6, "carbs_g": 50.2, "fat_g": 0.5,
                    "range_low_kcal": 180, "range_high_kcal": 280,
                }],
            })

    def test_updates_record_by_stable_id(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            store = self.storage.DailyRecordStore(Path(directory) / "records.json")
            item = store.add_record("2026-08-15", "daily_checkin", {"fatigue_level": 3})
            updated = store.update_record(
                item["id"], date="2026-08-16", category="daily_checkin", record={"fatigue_level": 5}
            )
            self.assertEqual(updated["date"], "2026-08-16")
            self.assertEqual(updated["record"]["fatigue_level"], 5)
            self.assertEqual(updated["revision"], 2)

    def test_conditional_update_rejects_stale_revision(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            store = self.storage.DailyRecordStore(Path(directory) / "records.json")
            item = store.add_record("2026-08-15", "training", {"name": "first"})
            first, stale = store.update_record_if_revision(
                item["id"], expected_revision=1, date="2026-08-15", category="training",
                record={"name": "first edit"},
            )
            self.assertFalse(stale)
            self.assertEqual(first["revision"], 2)
            second, stale = store.update_record_if_revision(
                item["id"], expected_revision=1, date="2026-08-15", category="training",
                record={"name": "stale edit"},
            )
            self.assertIsNone(second)
            self.assertTrue(stale)
            self.assertEqual(store.list_records()[0]["record"]["name"], "first edit")

    def test_legacy_agent_nutrition_moves_into_the_daily_checkin(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            store = self.storage.DailyRecordStore(Path(directory) / "records.json")
            store.add_record("2026-08-23", "daily_checkin", {"weight_kg": 79.8})
            store.add_record("2026-08-23", "nutrition", {
                "carbs_g": 280, "protein_g": 172, "fat_g": 50,
                "total_kcal": 2258, "source": "agent_tool",
            })
            records = store.list_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["category"], "daily_checkin")
            self.assertEqual(records[0]["record"]["weight_kg"], 79.8)
            self.assertEqual(records[0]["record"]["calories_kcal"], 2258)
            self.assertNotIn("total_kcal", records[0]["record"])

    def test_legacy_chinese_agent_nutrition_is_normalized_and_accumulated(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            store = self.storage.DailyRecordStore(Path(directory) / "records.json")
            store.add_record("2026-08-24", "nutrition", {
                "餐次": "晚餐", "食物": "紫薯", "重量_g": 230,
                "热量_kcal": 189, "蛋白质_g": 3.2, "碳水_g": 43.5,
                "脂肪_g": 0.5, "source": "agent_tool",
            })
            store.add_record("2026-08-24", "nutrition", {
                "热量_kcal": 100, "蛋白质_g": 10, "碳水_g": 5,
                "脂肪_g": 2, "source": "agent_tool",
            })
            records = store.list_records()
            self.assertEqual(len(records), 1)
            record = records[0]["record"]
            self.assertEqual(record["calories_kcal"], 289.0)
            self.assertEqual(record["protein_g"], 13.2)
            self.assertEqual(record["carbs_g"], 48.5)
            self.assertEqual(record["fat_g"], 2.5)
            self.assertNotIn("食物", record)
            self.assertEqual(len(record["meal_estimates"]), 2)
            self.assertTrue(all(meal["source"] == "text_nutrition" for meal in record["meal_estimates"]))

    def test_already_migrated_chinese_checkin_is_repaired_in_place(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.json"
            path.write_text(json.dumps([{
                "id": "legacy", "date": "2026-08-24", "category": "daily_checkin",
                "created_at": "2026-08-24T10:34:00+00:00", "revision": 1,
                "record": {
                    "weight_kg": 70, "calories_kcal": 0,
                    "餐次": "晚餐", "食物": "紫薯", "重量_g": 230,
                    "热量_kcal": 189, "蛋白质_g": 3.2, "碳水_g": 43.5,
                    "脂肪_g": 0.5, "膳食纤维_g": 7, "备注": "估算值",
                    "source": "agent_tool",
                },
            }], ensure_ascii=False), encoding="utf-8")
            record = self.storage.DailyRecordStore(path).list_records()[0]["record"]
            self.assertEqual(record["weight_kg"], 70)
            self.assertEqual(record["calories_kcal"], 189)
            self.assertEqual(record["protein_g"], 3.2)
            self.assertEqual(record["carbs_g"], 43.5)
            self.assertEqual(record["fat_g"], 0.5)
            self.assertEqual(record["nutrition_source"], "agent_tool")
            self.assertEqual(record["meal_estimates"][0]["source"], "text_nutrition")
            self.assertNotIn("紫薯", str(record))

    def test_non_agent_nutrition_is_not_silently_migrated(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            store = self.storage.DailyRecordStore(Path(directory) / "records.json")
            store.add_record("2026-08-23", "nutrition", {"source": "imported", "calories_kcal": 500})
            self.assertEqual(store.list_records()[0]["category"], "nutrition")

    def test_additive_upsert_accumulates_daily_nutrients_atomically(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            store = self.storage.DailyRecordStore(Path(directory) / "records.json")
            nutrient_fields = ("calories_kcal", "protein_g", "carbs_g", "fat_g")
            store.upsert_dated_record(
                "2026-08-24", "daily_checkin",
                {"calories_kcal": 189, "protein_g": 3.2, "carbs_g": 43.5, "fat_g": 0.5},
                additive_fields=nutrient_fields,
            )
            store.upsert_dated_record(
                "2026-08-24", "daily_checkin",
                {"calories_kcal": 100, "protein_g": 10, "carbs_g": 5, "fat_g": 2},
                additive_fields=nutrient_fields,
            )
            record = store.list_records()[0]["record"]
            self.assertEqual(record, {
                "calories_kcal": 289, "protein_g": 13.2,
                "carbs_g": 48.5, "fat_g": 2.5,
            })

    def test_missing_group_is_reconciled_from_daily_total_without_duplication(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.json"
            photo = daily_checkin.nutrition_estimate_from_totals(
                {"calories_kcal": 674, "protein_g": 84.7, "carbs_g": 13.5, "fat_g": 31},
                source="food_photo_estimate",
            )
            path.write_text(json.dumps([{
                "id": "mixed", "date": "2026-08-24", "category": "daily_checkin",
                "created_at": "2026-08-24T00:00:00+00:00", "revision": 1,
                "record": {
                    "calories_kcal": 863, "protein_g": 87.9, "carbs_g": 57,
                    "fat_g": 31.5, "nutrition_source": "agent_tool",
                    "meal_estimates": [photo],
                },
            }], ensure_ascii=False), encoding="utf-8")
            store = self.storage.DailyRecordStore(path)
            first = store.list_records()[0]["record"]
            self.assertEqual([meal["source"] for meal in first["meal_estimates"]], [
                "food_photo_estimate", "text_nutrition",
            ])
            residual = first["meal_estimates"][1]
            self.assertEqual(residual["total_kcal"], 189)
            self.assertEqual(residual["protein_g"], 3.2)
            self.assertEqual(residual["carbs_g"], 43.5)
            self.assertEqual(residual["fat_g"], 0.5)
            second = store.list_records()[0]["record"]
            self.assertEqual(len(second["meal_estimates"]), 2)


if __name__ == "__main__":
    unittest.main()
