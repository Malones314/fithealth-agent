"""AGENT-02 回归测试：Agent 工具写入的确定性校验。

原状：`save_daily_record` 只检查 date 非空、category 是非空字符串、record 是
dict，其余全放行。模型因此能写进 date="昨天"、任意深度嵌套的对象，以及
category="training" 的记录——后者会直接出现在训练记录列表里被当成真实训练，
而 prompts.py 里写着"Agent 没有保存训练的权限"。权限其实是给了的。
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import date, timedelta
from pathlib import Path


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


daily_checkin = load_module("daily_checkin_agent_writes", PACKAGE_DIR / "daily_checkin.py")
validate = daily_checkin.validate_agent_daily_record
TODAY = date(2026, 8, 20)


class CategoryPrivilegeTest(unittest.TestCase):
    """最重要的一条：模型不能借工具写出"训练记录"。"""

    def test_training_category_is_rejected(self) -> None:
        for category in ("training", "训练", "力量训练"):
            with self.subTest(category=category):
                with self.assertRaises(ValueError) as ctx:
                    validate("2026-08-20", category, {"note": "今天练了胸"}, today=TODAY)
                # 错误信息要能指导模型改走正确路径，而不只是说"不行"
                self.assertIn("侧栏", str(ctx.exception))

    def test_checkin_category_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate("2026-08-20", "daily_checkin", {"weight_kg": 80}, today=TODAY)
        self.assertIn("打卡", str(ctx.exception))

    def test_arbitrary_categories_are_rejected(self) -> None:
        """白名单而非黑名单：FIT 导入会产生中文分类，黑名单列不全。"""
        for category in ("跳绳", "有氧运动", "", "  ", "whatever"):
            with self.subTest(category=category):
                with self.assertRaises(ValueError):
                    validate("2026-08-20", category, {"note": "x"}, today=TODAY)

    def test_allowed_categories_pass(self) -> None:
        for category in daily_checkin.AGENT_ALLOWED_CATEGORIES:
            with self.subTest(category=category):
                payload = {"calories_kcal": 100} if category == "nutrition" else {"note": "ok"}
                _d, resolved, _r = validate("2026-08-20", category, payload, today=TODAY)
                self.assertEqual(resolved, category)

    def test_health_categories_are_never_training_records(self) -> None:
        for category in (*daily_checkin.AGENT_ALLOWED_CATEGORIES, "daily_checkin"):
            with self.subTest(category=category):
                self.assertFalse(daily_checkin.is_training_record_item({
                    "category": category,
                    "record": {"sport": "nutrition", "segments": []},
                }))

    def test_only_structured_workouts_are_training_records(self) -> None:
        self.assertTrue(daily_checkin.is_training_record_item({
            "category": "training",
            "record": {"sport": "strength_training", "segments": []},
        }))
        self.assertFalse(daily_checkin.is_training_record_item({
            "category": "training", "record": {"note": "not a parsed workout"},
        }))


class DateValidationTest(unittest.TestCase):
    def test_relative_dates_are_rejected_with_actionable_message(self) -> None:
        for value in ("昨天", "今天", "yesterday", "", None, "2026/08/20"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    validate(value, "daily", {"note": "x"}, today=TODAY)
                self.assertIn("YYYY-MM-DD", str(ctx.exception))

    def test_future_dates_are_rejected(self) -> None:
        future = (TODAY + timedelta(days=5)).isoformat()
        with self.assertRaises(ValueError) as ctx:
            validate(future, "daily", {"note": "x"}, today=TODAY)
        self.assertIn("未来", str(ctx.exception))

    def test_tomorrow_is_tolerated_for_timezone_skew(self) -> None:
        tomorrow = (TODAY + timedelta(days=1)).isoformat()
        resolved, _c, _r = validate(tomorrow, "daily", {"note": "x"}, today=TODAY)
        self.assertEqual(resolved, tomorrow)

    def test_absurdly_early_dates_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate("1970-01-01", "daily", {"note": "x"}, today=TODAY)

    def test_date_is_normalized(self) -> None:
        resolved, _c, _r = validate("  2026-08-20  ", "daily", {"note": "x"}, today=TODAY)
        self.assertEqual(resolved, "2026-08-20")


class RecordStructureTest(unittest.TestCase):
    def test_non_dict_and_empty_records_are_rejected(self) -> None:
        for value in ("not a dict", [], None, {}, 42):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate("2026-08-20", "daily", value, today=TODAY)

    def test_deep_nesting_is_rejected(self) -> None:
        deep = {"a": {"b": {"c": {"d": "too deep"}}}}
        with self.assertRaises(ValueError) as ctx:
            validate("2026-08-20", "daily", deep, today=TODAY)
        self.assertIn("嵌套", str(ctx.exception))

    def test_shallow_nesting_is_allowed(self) -> None:
        ok = {"nutrition": {"protein_g": 150, "note": "还行"}}
        _d, _c, record = validate("2026-08-20", "daily", ok, today=TODAY)
        self.assertEqual(record["nutrition"]["protein_g"], 150)

    def test_too_many_fields_rejected(self) -> None:
        wide = {f"field_{i}": i for i in range(40)}
        with self.assertRaises(ValueError) as ctx:
            validate("2026-08-20", "daily", wide, today=TODAY)
        self.assertIn("字段数", str(ctx.exception))

    def test_oversized_payload_is_rejected(self) -> None:
        big = {f"f{i}": "x" * 400 for i in range(15)}
        with self.assertRaises(ValueError) as ctx:
            validate("2026-08-20", "daily", big, today=TODAY)
        self.assertIn("超过上限", str(ctx.exception))

    def test_long_strings_are_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate("2026-08-20", "daily", {"note": "字" * 600}, today=TODAY)
        self.assertIn("字符", str(ctx.exception))

    def test_long_keys_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate("2026-08-20", "daily", {"k" * 50: 1}, today=TODAY)

    def test_unsupported_value_types_are_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate("2026-08-20", "daily", {"when": date(2026, 8, 20)}, today=TODAY)
        self.assertIn("不支持的值类型", str(ctx.exception))

    def test_long_lists_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate("2026-08-20", "daily", {"items": list(range(100))}, today=TODAY)


class NumericRangeReuseTest(unittest.TestCase):
    """已知字段必须和手动录入共用同一套范围口径，不能两套标准。"""

    def test_known_fields_are_range_checked(self) -> None:
        for field, value in (("weight_kg", 999), ("fatigue_level", 42), ("pain_level", -3)):
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as ctx:
                    validate("2026-08-20", "recovery", {field: value}, today=TODAY)
                self.assertIn("之间", str(ctx.exception))

    def test_in_range_values_are_normalized(self) -> None:
        _d, _c, record = validate("2026-08-20", "recovery", {"weight_kg": 79.0}, today=TODAY)
        self.assertEqual(record["weight_kg"], 79)
        self.assertIsInstance(record["weight_kg"], int)

    def test_unknown_fields_stay_permissive(self) -> None:
        """结构从严、字段从宽：不强制字段白名单，否则通用工具没法用。"""
        _d, _c, record = validate(
            "2026-08-20", "daily", {"mood": "不错", "water_ml": 2000}, today=TODAY
        )
        self.assertEqual(record["mood"], "不错")
        self.assertEqual(record["water_ml"], 2000)

    def test_nutrition_total_kcal_uses_the_checkin_field_name(self) -> None:
        _d, _c, record = validate(
            "2026-08-20", "nutrition",
            {"carbs_g": 280, "protein_g": 172, "fat_g": 50, "total_kcal": 2258},
            today=TODAY,
        )
        self.assertEqual(record["calories_kcal"], 2258)
        self.assertNotIn("total_kcal", record)


class ProvenanceTest(unittest.TestCase):
    def test_agent_writes_are_marked(self) -> None:
        _d, _c, record = validate("2026-08-20", "daily", {"note": "x"}, today=TODAY)
        self.assertEqual(record["source"], "agent_tool")

    def test_existing_source_is_preserved(self) -> None:
        _d, _c, record = validate(
            "2026-08-20", "daily", {"note": "x", "source": "user_corrected"}, today=TODAY
        )
        self.assertEqual(record["source"], "user_corrected")


class ToolWiringTest(unittest.TestCase):
    """确认工具确实走了校验，而不是又留了一条旁路。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tools_source = (PACKAGE_DIR / "tools.py").read_text(encoding="utf-8")

    def test_tool_calls_the_validator(self) -> None:
        self.assertIn("validate_agent_daily_record(", self.tools_source)

    def test_old_permissive_checks_are_gone(self) -> None:
        self.assertNotIn('message="category 必须是非空字符串"', self.tools_source)
        self.assertNotIn("category.strip()", self.tools_source)

    def test_query_limit_is_capped(self) -> None:
        self.assertIn("min(limit, _QUERY_LIMIT_MAX)", self.tools_source)

    def test_tool_description_states_the_restriction(self) -> None:
        self.assertIn("不能用本工具保存训练记录", self.tools_source)

    def test_prompt_no_longer_contradicts_the_tool_layer(self) -> None:
        prompts = (PACKAGE_DIR / "prompts.py").read_text(encoding="utf-8")
        self.assertNotIn('当用户要求"记录/保存/新增"时，优先调用工具保存结构化数据。', prompts)
        self.assertIn("你没有保存训练的权限", prompts)


class ToolBehaviourTest(unittest.TestCase):
    """用桩 store 跑一遍工具，确认拒绝路径不落盘、成功路径落盘。"""

    def setUp(self) -> None:
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
        self.addCleanup(
            lambda: [
                sys.modules.pop(name, None)
                for name in ("fithealth_agent.tools", "fithealth_agent.daily_checkin",
                             "fithealth_agent.storage", "fithealth_agent")
            ]
        )
        load_module("fithealth_agent.daily_checkin", PACKAGE_DIR / "daily_checkin.py")
        load_module("fithealth_agent.storage", PACKAGE_DIR / "storage.py")
        self.tools = load_module("fithealth_agent.tools", PACKAGE_DIR / "tools.py")

        class StubStore:
            def __init__(self) -> None:
                self.saved: list[dict] = []

            def add_record(self, *, date, category, record):
                item = {"id": "stub", "date": date, "category": category, "record": record}
                self.saved.append(item)
                return item

            def upsert_dated_record(self, *, date, category, record, **_kwargs):
                item = {"id": "stub", "date": date, "category": category, "record": record}
                self.saved.append(item)
                return item, True, 0

        self.store = StubStore()
        self.tool = self.tools.SaveDailyRecordTool(self.store)

    def test_rejected_write_does_not_reach_the_store(self) -> None:
        response = self.tool.run({"date": "昨天", "category": "training", "record": {"a": 1}})
        self.assertEqual(self.store.saved, [], "校验失败时绝不能落盘")
        self.assertTrue(getattr(response, "error", None) or not getattr(response, "success", True))

    def test_valid_write_reaches_the_store(self) -> None:
        self.tool.run({"date": "2026-08-20", "category": "recovery", "record": {"fatigue_level": 6}})
        self.assertEqual(len(self.store.saved), 1)
        saved = self.store.saved[0]
        self.assertEqual(saved["date"], "2026-08-20")
        self.assertEqual(saved["category"], "recovery")
        self.assertEqual(saved["record"]["source"], "agent_tool")

    def test_nutrition_write_upserts_the_daily_checkin(self) -> None:
        self.tool.run({
            "date": "2026-08-20", "category": "nutrition",
            "record": {"carbs_g": 280, "protein_g": 172, "fat_g": 50, "total_kcal": 2258},
        })
        self.assertEqual(len(self.store.saved), 1)
        saved = self.store.saved[0]
        self.assertEqual(saved["category"], "daily_checkin")
        self.assertEqual(saved["record"]["calories_kcal"], 2258)
        self.assertEqual(saved["record"]["nutrition_source"], "agent_tool")

    def test_chinese_food_record_keeps_only_four_nutrient_totals(self) -> None:
        self.tool.run({
            "date": "2026-08-24", "category": "nutrition",
            "record": {
                "餐次": "晚餐", "食物": "紫薯", "重量_g": 230,
                "热量_kcal": 189, "蛋白质_g": 3.2, "碳水_g": 43.5,
                "脂肪_g": 0.5, "膳食纤维_g": 7, "备注": "估算值",
            },
        })
        record = self.store.saved[0]["record"]
        self.assertEqual(
            {key: record[key] for key in ("calories_kcal", "protein_g", "carbs_g", "fat_g")},
            {"calories_kcal": 189, "protein_g": 3.2, "carbs_g": 43.5, "fat_g": 0.5},
        )
        self.assertEqual(record["meal_estimates"][0]["source"], "text_nutrition")
        self.assertEqual(record["meal_estimates"][0]["items"][0]["name"], "文字记录")
        self.assertNotIn("紫薯", str(record))


if __name__ == "__main__":
    unittest.main()
