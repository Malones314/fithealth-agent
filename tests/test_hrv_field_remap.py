"""DATA-18：HRV 状态字段按 FIT SDK 语义重映射的回归测试。

钉住三件事：

1. **原始值 → 毫秒的对应关系**。用 `data/health-imports/8fbb4a72c5af1a56-2026-08-21.zip`
   里 `12723504216_HRV_STATUS.fit` 的真实原始值做样本（清单 DATA-18 记录的那一组）：
   `weekly_average=5888, last_night=5632, last_night_average=8192, baseline_low=4480,
   baseline_high=4736, baseline_balanced_low=6272`。
   fitfile 的名字从字段 1 起整体偏一位，所以 `last_night`(44 ms) 才是**昨夜平均**，
   `last_night_average`(64 ms) 是**昨夜 5 分钟峰值**。
2. **两条独立的一致性证据**（这是当初判定错位的依据，必须一起钉住，否则下一个人
   照 fitfile 的名字改回去时测试仍然是绿的）：
   - 昨夜平均要和同日 `hrv_value` 采样自算的均值接近（真实数据 44 对 44.58）；
   - 七日平均要落在平衡区 `[balanced_lower, balanced_upper]` 内，与设备自报的
     `status=balanced` 自洽（真实数据 46 落在 [37, 49]）。
3. **越界值被拒绝且留痕**，以及语义未知的 `baseline_balanced_high` 原值直存、
   不套毫秒闸门（它的实测取值 28299~43194 会变，不是常量哨兵）。
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_extended_health_fields import (  # noqa: E402
    _Message,
    load_health_modules,
    restore_modules,
)

#: `12723504216_HRV_STATUS.fit` 的真实原始值（2026-08-21 那份包）。
REAL_RAW = {
    "weekly_average": 5888,
    "last_night": 5632,
    "last_night_average": 8192,
    "baseline_low": 4480,
    "baseline_high": 4736,
    "baseline_balanced_low": 6272,
    "baseline_balanced_high": 40960,
    "status": 4,
    "reading_count": 31,
}
#: 同一天由 `hrv_value` 采样算出的均值，用来钉住"哪个字段才是昨夜平均"。
REAL_SAMPLE_MEAN_MS = 44.58

BASE = datetime(2026, 8, 21, 4, 0, tzinfo=timezone.utc)


class HrvFieldRemapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store_module, self.importer, saved = load_health_modules()
        self.addCleanup(restore_modules, saved)

    def parse(self, raw: dict) -> dict:
        message = _Message("hrv_status_summary", {"timestamp": BASE, **raw})
        fake = types.SimpleNamespace(
            type=types.SimpleNamespace(name="monitoring_b"),
            serial_number=1,
            messages=[message],
        )
        with mock.patch.object(self.importer.fitfile.file, "File", lambda path: fake):
            return self.importer._parse_fit_source("sample_WELLNESS.fit", b"x" * 32)

    def status(self, raw: dict | None = None) -> dict:
        parsed = self.parse(REAL_RAW if raw is None else raw)
        self.assertEqual(len(parsed["hrv_statuses"]), 1, parsed["warnings"])
        return parsed["hrv_statuses"][0]

    # ---- 1. 原始值 → 毫秒 ----
    def test_raw_values_map_to_sdk_semantics(self) -> None:
        item = self.status()
        self.assertEqual(item["weekly_average_ms"], 46.0)
        self.assertEqual(item["last_night_average_ms"], 44.0)
        self.assertEqual(item["last_night_5min_high_ms"], 64.0)
        self.assertEqual(item["baseline_low_upper_ms"], 35.0)
        self.assertEqual(item["baseline_balanced_lower_ms"], 37.0)
        self.assertEqual(item["baseline_balanced_upper_ms"], 49.0)

    def test_fitfile_named_columns_are_kept_untouched_for_old_rows(self) -> None:
        """旧列保持 fitfile 口径不变——原地改语义比现状更糟（清单原话）。"""
        item = self.status()
        self.assertEqual(item["last_night"], 44.0)
        self.assertEqual(item["last_night_average"], 64.0)
        self.assertEqual(item["baseline_high"], 37.0)

    # ---- 2. 两条独立一致性证据 ----
    def test_last_night_average_matches_the_independent_sample_mean(self) -> None:
        """昨夜平均必须贴近采样均值；若有人把它换回 fitfile 的同名字段就会差 20 ms。"""
        item = self.status()
        self.assertLess(
            abs(item["last_night_average_ms"] - REAL_SAMPLE_MEAN_MS), 2.0,
            "昨夜平均应来自 fitfile 的 last_night（字段 1），不是 last_night_average",
        )
        self.assertGreater(
            abs(item["last_night_5min_high_ms"] - REAL_SAMPLE_MEAN_MS), 10.0,
            "5 分钟峰值本就该显著高于均值",
        )

    def test_weekly_average_sits_inside_the_balanced_band(self) -> None:
        """与设备自报的 status=balanced 自洽；按旧名字读则 46 会"高于上限 37"。"""
        item = self.status()
        self.assertEqual(item["status"], "balanced")
        self.assertLessEqual(item["baseline_balanced_lower_ms"], item["weekly_average_ms"])
        self.assertLessEqual(item["weekly_average_ms"], item["baseline_balanced_upper_ms"])

    def test_the_three_baselines_are_monotonic(self) -> None:
        item = self.status()
        self.assertLess(item["baseline_low_upper_ms"], item["baseline_balanced_lower_ms"])
        self.assertLess(item["baseline_balanced_lower_ms"], item["baseline_balanced_upper_ms"])

    # ---- 3. 闸门与未知字段 ----
    def test_out_of_range_values_are_dropped_with_a_warning(self) -> None:
        parsed = self.parse({**REAL_RAW, "last_night": 40960})  # 320 ms，越界
        item = parsed["hrv_statuses"][0]
        self.assertIsNone(item["last_night_average_ms"])
        self.assertTrue(
            any("last_night_average_ms" in text for text in parsed["warnings"]),
            f"越界值必须留痕，实际 warnings={parsed['warnings']}",
        )

    def test_a_missing_field_is_not_reported_as_rejected(self) -> None:
        raw = {key: value for key, value in REAL_RAW.items() if key != "baseline_balanced_low"}
        parsed = self.parse(raw)
        self.assertIsNone(parsed["hrv_statuses"][0]["baseline_balanced_upper_ms"])
        self.assertEqual(parsed["warnings"], [])

    def test_semantically_unknown_field_is_stored_raw_without_the_ms_gate(self) -> None:
        """40960/128 = 320 ms 会被毫秒闸门拒掉；它不该走那道闸门，而要原值留存。"""
        item = self.status()
        self.assertEqual(item["unmapped_balanced_high_raw"], 40960.0)
        # 实测取值会变（28299~43194），所以另一个值也要照样存下来，不能当哨兵丢掉。
        other = self.status({**REAL_RAW, "baseline_balanced_high": 28299})
        self.assertEqual(other["unmapped_balanced_high_raw"], 28299.0)

    def test_mapping_table_is_the_single_source_of_truth(self) -> None:
        """映射只允许有一处定义（清单 DATA-18：不要在导入侧和汇总侧各写一遍）。"""
        pairs = dict(self.importer._HRV_STATUS_FIELDS)
        self.assertEqual(pairs["last_night_average_ms"], "last_night")
        self.assertEqual(pairs["last_night_5min_high_ms"], "last_night_average")
        self.assertEqual(pairs["baseline_balanced_lower_ms"], "baseline_high")
        self.assertEqual(pairs["baseline_balanced_upper_ms"], "baseline_balanced_low")
        # 字段 0 没有偏移
        self.assertEqual(pairs["weekly_average_ms"], "weekly_average")


class HrvSummaryColumnsTest(unittest.TestCase):
    """汇总层：`daily_health_summary` 的 `hrv_*_ms` 取的是重映射后的值。"""

    DAY = "2026-08-21"

    def setUp(self) -> None:
        self.store_module, self.importer, saved = load_health_modules()
        self.addCleanup(restore_modules, saved)
        # Windows 上 sqlite 句柄未必立刻释放，照 test_extended_health_fields 的做法
        # 忽略清理错误，否则测试会因为删不掉临时文件而红。
        self.tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmpdir.cleanup)
        self.store = self.store_module.HealthStore(Path(self.tmpdir.name) / "health.db")

    def save(self, **overrides) -> None:
        stamp = {
            "timestamp_utc": BASE.isoformat(),
            "timestamp_local": "2026-08-21T12:00:00+08:00",
            "local_date": self.DAY,
        }
        status = {
            **stamp,
            "weekly_average": 46.0, "last_night": 44.0, "last_night_average": 64.0,
            "baseline_low": 35.0, "baseline_high": 37.0,
            "weekly_average_ms": 46.0, "last_night_average_ms": 44.0,
            "last_night_5min_high_ms": 64.0, "baseline_low_upper_ms": 35.0,
            "baseline_balanced_lower_ms": 37.0, "baseline_balanced_upper_ms": 49.0,
            "unmapped_balanced_high_raw": 40960.0,
            "status": "balanced", "reading_count": 31,
            **overrides,
        }
        self.store.save_import({
            "id": str(uuid4()),
            "sha256": uuid4().hex * 2,
            "filename": f"hrv-{self.DAY}.zip",
            "kind": "wellness_zip",
            "status": "imported",
            "date_hint": self.DAY,
            "warnings": [],
            "raw_path": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sleep": None,
            "sources": [{
                "id": str(uuid4()),
                "filename": "hrv_WELLNESS.fit",
                "kind": "wellness",
                "sha256": uuid4().hex * 2,
                "earliest_utc": None, "latest_utc": None,
                "device_serial": "device", "warnings": [],
                "record_count": 1, "message_counts": {}, "data_types": [],
                "heart_rates": [], "metric_samples": [],
                "activity_observations": [], "device_metrics": [],
                "intensity_observations": [], "sleep_stages": [],
                "hrv_statuses": [status],
            }],
        })

    def test_summary_columns_use_the_remapped_values(self) -> None:
        self.save()
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT * FROM daily_health_summary WHERE date = ?", (self.DAY,)
            ).fetchone()
        self.assertEqual(row["hrv_last_night_average_ms"], 44.0)
        self.assertEqual(row["hrv_last_night_5min_high_ms"], 64.0)
        self.assertEqual(row["hrv_baseline_balanced_lower_ms"], 37.0)
        self.assertEqual(row["hrv_baseline_balanced_upper_ms"], 49.0)
        self.assertEqual(row["hrv_weekly_average_ms"], 46.0)
        # 旧列保持 fitfile 口径
        self.assertEqual(row["hrv_last_night_average"], 64.0)

    def test_get_daily_health_exposes_the_ms_fields(self) -> None:
        self.save()
        hrv = self.store.get_daily_health(self.DAY)["hrv"]
        self.assertEqual(hrv["last_night_average_ms"], 44.0)
        self.assertEqual(hrv["baseline_balanced_upper_ms"], 49.0)
        self.assertEqual(hrv["status"], "balanced")

    def test_rebuild_daily_summaries_is_callable_for_backfill(self) -> None:
        self.save()
        self.assertGreaterEqual(self.store.rebuild_daily_summaries([self.DAY]), 1)
        self.assertEqual(
            self.store.get_daily_health(self.DAY)["hrv"]["last_night_average_ms"], 44.0
        )


if __name__ == "__main__":
    unittest.main()
