from __future__ import annotations

import unittest

from fithealth_agent import food_analysis


class FoodAnalysisJsonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = food_analysis
        cls.valid = (
            '{"items":[{"name":"米饭","portion":"一碗","calories_kcal":230,"protein_g":4.6,"carbs_g":50.2,"fat_g":0.5}],'
            '"total_kcal":230,"range_low_kcal":180,"range_high_kcal":280,'
            '"confidence":"medium","assumptions":["未计额外酱料"],"disclaimer":"仅供参考"}'
        )

    def test_accepts_one_complete_json_object(self) -> None:
        result = self.module._extract_json(self.valid)
        self.assertEqual(result["items"][0]["name"], "米饭")

    def test_accepts_json_markdown_fence(self) -> None:
        result = self.module._extract_json(f"```json\n{self.valid}\n```")
        self.assertEqual(result["confidence"], "medium")

    def test_rejects_explanation_wrapped_json(self) -> None:
        with self.assertRaises(self.module.FoodAnalysisError):
            self.module._extract_json("分析如下：\n" + self.valid)

    def test_rejects_multiple_json_objects(self) -> None:
        with self.assertRaises(self.module.FoodAnalysisError):
            self.module._extract_json(self.valid + "\n{" + '\"note\":\"extra\"}')

    def test_rejects_missing_or_invalid_schema_fields(self) -> None:
        with self.assertRaises(self.module.FoodAnalysisError):
            self.module._extract_json('{"items":[],"total_kcal":0}')
        with self.assertRaises(self.module.FoodAnalysisError):
            self.module._extract_json(self.valid.replace('"confidence":"medium"', '"confidence":"certain"'))

    def test_normalizes_item_macros_into_meal_totals(self) -> None:
        result = self.module._normalize_result(self.module._extract_json(self.valid))
        self.assertEqual(result["protein_g"], 4.6)
        self.assertEqual(result["carbs_g"], 50.2)
        self.assertEqual(result["fat_g"], 0.5)

    def test_item_sum_is_the_only_saved_calorie_scale(self) -> None:
        raw = self.module._extract_json(
            self.valid.replace('"total_kcal":230', '"total_kcal":800')
            .replace('"range_low_kcal":180', '"range_low_kcal":700')
            .replace('"range_high_kcal":280', '"range_high_kcal":900')
        )
        result = self.module._normalize_result(raw)
        self.assertEqual(result["total_kcal"], 230)
        self.assertEqual(result["model_total_kcal"], 800)
        self.assertLessEqual(result["range_low_kcal"], 230)
        self.assertGreaterEqual(result["range_high_kcal"], 230)


if __name__ == "__main__":
    unittest.main()
