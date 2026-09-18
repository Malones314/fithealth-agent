from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.module_map import consumer_home, function_home
from tests.source_tools import function_node, load_symbols


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"

#: 合并逻辑需要用到的模块级常量（DATA-13 的字段分类表）。
WANTED_CONSTANTS = (
    "_ADDITIVE_SEGMENT_FIELDS",
    "_PEAK_SEGMENT_FIELDS",
    "_WEIGHTED_SEGMENT_FIELDS",
)
WANTED_FUNCTIONS = (
    "_active_saved_segments",
    "_validate_saved_training_updates",
    "_weighted_average",
    "_segment_numbers",
    "_merged_segment_hr",
    "_merged_segment_numbers",
    "_merge_saved_training_segments",
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_functions() -> dict:
    """把合并逻辑单独抽出来执行（路径按 tests/module_map.py 查表）。

    刻意不 import main：那会把整条 LLM 依赖链和真实 data/ 目录一起拉进来。
    fit_parser 里的 HRRecord / compute_hr_in_window 是真货——心率重算的口径
    必须和待确认训练那条路径完全一致，不能用替身糊过去。
    """
    fit_parser = load_module("fit_parser_for_saved_merge", PACKAGE_DIR / "fit_parser.py")
    return load_symbols(
        WANTED_FUNCTIONS,
        constants=WANTED_CONSTANTS,
        namespace={
            "Counter": Counter,
            "HRRecord": fit_parser.HRRecord,
            "compute_hr_in_window": fit_parser.compute_hr_in_window,
        },
    )


class SavedTrainingRecordEditingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        functions = load_functions()
        cls.validate_updates = staticmethod(functions["_validate_saved_training_updates"])
        cls.merge_segments = staticmethod(functions["_merge_saved_training_segments"])

    @staticmethod
    def record(**overrides):
        """两组深蹲 + 中间一段休息。时长 30s / 40s 刻意不等，用来钉住加权。"""
        first = {
            "index": 1, "segment_type": "set_active", "is_rest": False,
            "start_time": "2026-08-13T09:00:00+08:00",
            "end_time": "2026-08-13T09:00:30+08:00",
            "duration_s": 30, "category": "深蹲", "repetitions": 5,
            "weight_kg": 60, "avg_hr": 120, "max_hr": 130,
        }
        third = {
            "index": 3, "segment_type": "set_active", "is_rest": False,
            "start_time": "2026-08-13T09:01:00+08:00",
            "end_time": "2026-08-13T09:01:40+08:00",
            "duration_s": 40, "category": "深蹲", "repetitions": 6,
            "weight_kg": 65, "avg_hr": 125, "max_hr": 138,
        }
        first.update(overrides.get("first") or {})
        third.update(overrides.get("third") or {})
        for key in overrides.get("drop") or ():
            first.pop(key, None)
            third.pop(key, None)
        return {
            "segments": [
                first,
                {
                    "index": 2, "segment_type": "set_rest", "is_rest": True,
                    "start_time": "2026-08-13T09:00:30+08:00",
                    "end_time": "2026-08-13T09:01:00+08:00",
                    "duration_s": 30, "category": "组间休息",
                },
                third,
            ],
            "total_sets": 2,
            "total_reps": 11,
        }

    def test_requires_updates_for_every_active_set(self) -> None:
        error = self.validate_updates(
            self.record(),
            [{"index": 1, "category": "深蹲", "weight_kg": 60, "repetitions": 5}],
        )
        self.assertEqual(error, "训练组提交不完整或包含无效序号")

    # ------------------------------------------------------------------
    # DATA-13：合并不再丢弃心率
    # ------------------------------------------------------------------

    def test_merge_keeps_the_structural_aggregates(self) -> None:
        record = self.record()
        merged, _ = self.merge_segments(record, [1, 3])
        self.assertTrue(merged)
        self.assertEqual(len(record["segments"]), 1)
        segment = record["segments"][0]
        self.assertEqual(segment["repetitions"], 11)
        self.assertEqual(segment["weight_kg"], 65)
        self.assertEqual(segment["duration_s"], 100.0)
        self.assertEqual(record["total_sets"], 1)

    def test_missing_stream_falls_back_to_duration_weighted_heart_rate(self) -> None:
        """原实现在这里硬写 None，把段里本来就有的心率白白丢掉。"""
        record = self.record()
        merged, notice = self.merge_segments(record, [1, 3])
        self.assertTrue(merged)
        segment = record["segments"][0]
        # (120*30 + 125*40) / 70 = 122.857… → 123；算术平均会给出 122.5→122
        self.assertEqual(segment["avg_hr"], 123)
        self.assertEqual(segment["max_hr"], 138)
        self.assertIn("加权还原", notice)
        self.assertNotIn("无法重新计算", notice)

    def test_raw_stream_is_used_for_an_exact_recomputation(self) -> None:
        """DATA-05 起心率流按记录 id 旁挂保存，能精确算就不该用近似。"""
        start = datetime(2026, 8, 13, 9, 0, tzinfo=timezone(timedelta(hours=8)))
        samples = [
            {"timestamp": (start + timedelta(seconds=offset)).isoformat(), "heart_rate": beats}
            # 覆盖整个 100 秒窗口（含被吸收的休息段），值刻意和段内 avg_hr 不同
            for offset, beats in [(0, 100), (25, 140), (45, 150), (70, 160), (99, 170)]
        ]
        record = self.record()
        merged, notice = self.merge_segments(record, [1, 3], hr_samples=samples)
        self.assertTrue(merged)
        segment = record["segments"][0]
        self.assertEqual(segment["avg_hr"], 144)  # mean(100,140,150,160,170) = 144
        self.assertEqual(segment["max_hr"], 170)
        self.assertIn("精确重算", notice)

    def test_stream_outside_the_window_does_not_win_over_the_weighted_fallback(self) -> None:
        """流里没有落在新窗口内的采样点时，不能因此把心率清空。"""
        record = self.record()
        samples = [{"timestamp": "2026-08-13T20:00:00+08:00", "heart_rate": 88}]
        merged, notice = self.merge_segments(record, [1, 3], hr_samples=samples)
        self.assertTrue(merged)
        self.assertEqual(record["segments"][0]["avg_hr"], 123)
        self.assertIn("加权还原", notice)

    def test_heart_rate_is_none_only_when_nothing_is_available(self) -> None:
        record = self.record(drop=("avg_hr", "max_hr"))
        merged, notice = self.merge_segments(record, [1, 3])
        self.assertTrue(merged)
        segment = record["segments"][0]
        self.assertIsNone(segment["avg_hr"])
        self.assertIsNone(segment["max_hr"])
        self.assertIn("都没有心率数据", notice)

    def test_partial_heart_rate_still_produces_a_value(self) -> None:
        """只有一组有心率时，用那一组，而不是整体作废。"""
        record = self.record(third={"avg_hr": None, "max_hr": None})
        merged, _ = self.merge_segments(record, [1, 3])
        self.assertTrue(merged)
        segment = record["segments"][0]
        self.assertEqual(segment["avg_hr"], 120)
        self.assertEqual(segment["max_hr"], 130)


class MergedNumericFieldsTest(unittest.TestCase):
    """DATA-13：可加性字段必须求和，均值字段必须加权，不能沿用第一段。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.merge_segments = staticmethod(load_functions()["_merge_saved_training_segments"])

    @staticmethod
    def record(first_extra: dict, third_extra: dict):
        return {
            "segments": [
                {
                    "index": 1, "segment_type": "set_active", "is_rest": False,
                    "start_time": "2026-08-13T09:00:00+08:00",
                    "end_time": "2026-08-13T09:00:30+08:00",
                    "duration_s": 30, "category": "跳绳", "repetitions": 0,
                    "weight_kg": 0, "avg_hr": 120, "max_hr": 130, **first_extra,
                },
                {
                    "index": 2, "segment_type": "set_active", "is_rest": False,
                    "start_time": "2026-08-13T09:00:30+08:00",
                    "end_time": "2026-08-13T09:01:10+08:00",
                    "duration_s": 40, "category": "跳绳", "repetitions": 0,
                    "weight_kg": 0, "avg_hr": 125, "max_hr": 138, **third_extra,
                },
            ],
            "total_sets": 2,
            "total_reps": 0,
        }

    def test_additive_fields_are_summed_not_inherited(self) -> None:
        record = self.record(
            {"distance_m": 120.0, "calories": 25},
            {"distance_m": 180.0, "calories": 31},
        )
        merged, _ = self.merge_segments(record, [1, 2])
        self.assertTrue(merged)
        segment = record["segments"][0]
        self.assertEqual(segment["distance_m"], 300.0)
        self.assertEqual(segment["calories"], 56)

    def test_peak_and_weighted_fields_use_the_right_aggregate(self) -> None:
        record = self.record(
            {"max_speed_mps": 3.0, "avg_cadence": 150, "avg_power_w": 100},
            {"max_speed_mps": 4.5, "avg_cadence": 170, "avg_power_w": 200},
        )
        merged, _ = self.merge_segments(record, [1, 2])
        segment = record["segments"][0]
        self.assertEqual(segment["max_speed_mps"], 4.5)
        # (150*30 + 170*40) / 70 = 161.43 → 161；沿用第一段会得到 150
        self.assertEqual(segment["avg_cadence"], 161)
        self.assertEqual(segment["avg_power_w"], 157)

    def test_average_speed_is_recomputed_from_total_distance(self) -> None:
        record = self.record(
            {"distance_m": 120.0, "avg_speed_mps": 4.0},
            {"distance_m": 180.0, "avg_speed_mps": 4.5},
        )
        merged, _ = self.merge_segments(record, [1, 2])
        segment = record["segments"][0]
        # 合并窗口 70s，总距离 300m → 4.286 m/s（按定义算，而不是沿用 4.0）
        self.assertEqual(segment["duration_s"], 70.0)
        self.assertAlmostEqual(segment["avg_speed_mps"], 4.286, places=3)

    def test_absent_aerobic_fields_are_not_invented(self) -> None:
        """力量组本来没有这些字段，不该凭空多出一堆看起来像"真测到 0"的值。"""
        record = self.record({}, {})
        merged, _ = self.merge_segments(record, [1, 2])
        segment = record["segments"][0]
        for field in ("distance_m", "calories", "avg_cadence", "avg_power_w", "avg_speed_mps"):
            with self.subTest(field=field):
                self.assertNotIn(field, segment)


class SourceInvariantTest(unittest.TestCase):
    def test_merge_no_longer_hardcodes_none_heart_rate(self) -> None:
        merge = function_node(
            function_home("_merge_saved_training_segments"), "_merge_saved_training_segments"
        )
        body = ast.unparse(merge)
        self.assertNotIn("'avg_hr': None", body)
        self.assertNotIn("'max_hr': None", body)
        self.assertIn("_merged_segment_hr(", body)
        self.assertIn("_merged_segment_numbers(", body)

    def test_endpoint_feeds_the_sidecar_stream_into_the_merge(self) -> None:
        # 不接线就等于只留了个加权近似，DATA-05 存下来的原始流白存了。
        endpoint = function_node(
            consumer_home("update_training_record"), "update_training_record"
        )
        self.assertIn("hr_samples=deps.hr_stream_store.load(record_id)", ast.unparse(endpoint))

if __name__ == "__main__":
    unittest.main()
