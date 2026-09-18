from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import types
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


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


class HealthImportTest(unittest.TestCase):
    def test_parse_failure_preserves_the_raw_source(self) -> None:
        from unittest import mock
        content = b"not-a-real-fit"
        with mock.patch("fithealth_agent.health_importer.parse_health_fit", side_effect=ValueError("bad fit")):
            with self.assertRaisesRegex(ValueError, "bad fit"):
                self.service.import_file("broken.fit", content)
        files = list(self.store.raw_dir.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), content)
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._cleanup)
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
        self.store_module = load_module(
            "fithealth_agent.health_store", PACKAGE_DIR / "health_store.py"
        )
        self.importer = load_module(
            "fithealth_agent.health_importer", PACKAGE_DIR / "health_importer.py"
        )
        self.health_tools = load_module(
            "fithealth_agent.health_tools", PACKAGE_DIR / "health_tools.py"
        )
        root = Path(self.temp_dir.name)
        self.store = self.store_module.HealthStore(root / "health.db", root / "raw")
        self.service = self.importer.HealthImportService(self.store)

    def _cleanup(self) -> None:
        self.temp_dir.cleanup()
        for name in (
            "fithealth_agent.health_importer",
            "fithealth_agent.health_tools",
            "fithealth_agent.health_store",
            "fithealth_agent",
        ):
            sys.modules.pop(name, None)

    @staticmethod
    def sleep_csv() -> bytes:
        return """睡眠分数 1 天,
日期,2026-08-14
睡眠时长,6时 48分
睡眠分数,79
质量,一般

睡眠分数因素,
压力 平均,11
深度睡眠持续时间,1时 41分
轻度睡眠持续时间,3时 42分
快速眼动持续时间,1时 25分
清醒时间,43分

睡眠时间线指标,
不安稳状态,39
夜间平均心率,60 bpm
静息心率,56 bpm
身体电量变化,'+79
平均 SpO₂,96%
最低 SpO2,79%
平均呼吸频率,14 brpm
最低呼吸频率,10 brpm
平均夜间 HRV,46 毫秒
7 天平均 HRV,平衡
""".encode("utf-8")

    def test_imports_sleep_csv_and_is_idempotent(self) -> None:
        first = self.service.import_file("睡眠.csv", self.sleep_csv())
        second = self.service.import_file("睡眠.csv", self.sleep_csv())

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.store.list_imports()), 1)
        sleep = self.store.get_sleep("2026-08-14")
        self.assertEqual(sleep["duration_min"], 408)
        self.assertEqual(sleep["deep_sleep_min"], 101)
        self.assertEqual(sleep["light_sleep_min"], 222)
        self.assertEqual(sleep["rem_sleep_min"], 85)
        self.assertEqual(sleep["night_avg_hr"], 60)
        self.assertEqual(sleep["body_battery_change"], 79)
        self.assertEqual(sleep["hrv_avg_ms"], 46)

        response = self.health_tools.QuerySleepTool(self.store).run({"date": "2026-08-14"})
        self.assertIn('"duration_min":408', response.text)
        self.assertIn('"score":79', response.text)
        self.assertIn('"deep_sleep_min":101', response.text)

    def test_rejects_unsafe_zip_path(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../outside_WELLNESS.fit", b"not-fit")
        with self.assertRaises(self.importer.HealthImportError):
            self.importer.parse_wellness_zip("2026-08-13.zip", buffer.getvalue())

    def test_damaged_fit_is_partial_not_total_failure(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("bad_WELLNESS.fit", b"not-a-fit-file")
        parsed = self.importer.parse_wellness_zip("2026-08-13.zip", buffer.getvalue())
        self.assertEqual(parsed["status"], "partial")
        self.assertEqual(len(parsed["sources"]), 1)
        self.assertTrue(parsed["sources"][0]["warnings"])
        self.assertEqual(parsed["skipped_files"][0]["filename"], "bad_WELLNESS.fit")

    def test_zip_without_fit_is_saved_as_partial_and_lists_every_skipped_file(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("notes/readme.txt", "not health data")
            archive.writestr("export/profile.json", "{}")
        result = self.service.import_file("mixed-export.zip", buffer.getvalue())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(
            [item["filename"] for item in result["skipped_files"]],
            ["notes/readme.txt", "export/profile.json"],
        )
        self.assertEqual(len(self.store.list_imports()), 1)

    def test_aggregates_fit_health_data_and_rebuilds_after_delete(self) -> None:
        import_id = str(uuid4())
        source_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        parsed = {
            "id": import_id,
            "sha256": "a" * 64,
            "filename": "2026-08-13.zip",
            "kind": "wellness_zip",
            "status": "imported",
            "date_hint": "2026-08-13",
            "warnings": [],
            "raw_path": None,
            "created_at": now,
            "sleep": None,
            "sources": [
                {
                    "id": source_id,
                    "filename": "sample_WELLNESS.fit",
                    "kind": "wellness",
                    "sha256": "b" * 64,
                    "earliest_utc": "2026-08-13T00:00:00+00:00",
                    "latest_utc": "2026-08-13T00:02:00+00:00",
                    "device_serial": "device",
                    "warnings": [],
                    "record_count": 12,
                    "message_counts": {"monitoring": 5, "stress_level": 2},
                    "data_types": ["heart_rate", "stress", "daily_activity", "sleep_stage"],
                    "heart_rates": [
                        {
                            "timestamp_utc": "2026-08-13T00:00:00+00:00",
                            "timestamp_local": "2026-08-13T08:00:00+08:00",
                            "local_date": "2026-08-13",
                            "bpm": 60,
                        },
                        {
                            "timestamp_utc": "2026-08-13T00:01:00+00:00",
                            "timestamp_local": "2026-08-13T08:01:00+08:00",
                            "local_date": "2026-08-13",
                            "bpm": 80,
                        },
                        {
                            "timestamp_utc": "2026-08-13T00:02:00+00:00",
                            "timestamp_local": "2026-08-13T08:02:00+08:00",
                            "local_date": "2026-08-13",
                            "bpm": 100,
                        },
                        *[
                            {
                                "timestamp_utc": f"2026-08-13T00:{minute:02d}:00+00:00",
                                "timestamp_local": f"2026-08-13T08:{minute:02d}:00+08:00",
                                "local_date": "2026-08-13",
                                "bpm": 80,
                            }
                            for minute in range(3, 10)
                        ],
                    ],
                    "metric_samples": [
                        {
                            "timestamp_utc": "2026-08-13T00:00:00+00:00",
                            "timestamp_local": "2026-08-13T08:00:00+08:00",
                            "local_date": "2026-08-13",
                            "metric": "stress",
                            "value": 20,
                        },
                        {
                            "timestamp_utc": "2026-08-13T00:01:00+00:00",
                            "timestamp_local": "2026-08-13T08:01:00+08:00",
                            "local_date": "2026-08-13",
                            "metric": "stress",
                            "value": 40,
                        },
                    ],
                    "activity_observations": [
                        {
                            "timestamp_utc": "2026-08-13T00:02:00+00:00",
                            "timestamp_local": "2026-08-13T08:02:00+08:00",
                            "local_date": "2026-08-13",
                            "activity_type": "walking",
                            "steps": 1000,
                            "distance_m": 800,
                            "active_calories": 50,
                            "active_time_s": 1200,
                        }
                    ],
                    "hrv_statuses": [
                        {
                            "timestamp_utc": "2026-08-13T00:03:00+00:00",
                            "timestamp_local": "2026-08-13T08:03:00+08:00",
                            "local_date": "2026-08-13",
                            "weekly_average": 44,
                            "last_night": 45,
                            "last_night_average": 46,
                            "baseline_low": 35,
                            "baseline_high": 50,
                            "status": "balanced",
                            "reading_count": 30,
                        }
                    ],
                    "sleep_stages": [
                        {
                            "sleep_date": "2026-08-13",
                            "timestamp_utc": "2026-08-12T17:00:00+00:00",
                            "timestamp_local": "2026-08-13T01:00:00+08:00",
                            "stage": "deep_sleep",
                            "duration_s": 600,
                        }
                    ],
                }
            ],
        }
        self.store.save_import(parsed)
        daily = self.store.get_daily_health("2026-08-13")
        self.assertEqual(daily["heart_rate"]["min"], 60)
        self.assertEqual(daily["heart_rate"]["max"], 100)
        self.assertEqual(daily["heart_rate"]["avg"], 80.0)
        self.assertEqual(daily["stress"]["avg"], 30.0)
        self.assertEqual(daily["activity"]["steps"], 1000)
        self.assertEqual(daily["hrv"]["last_night_average"], 46)
        self.assertEqual(daily["sleep"]["device_stage_deep_min"], 10.0)
        overview = self.store.get_daily_overview("2026-08-13")
        self.assertTrue(overview["has_data"])
        self.assertIn("sleep", overview["available_sections"])
        self.assertIn("heart_rate", overview["available_sections"])
        day_trend = self.store.get_metric_trend("heart_rate", "day", "2026-08-13")
        self.assertEqual(day_trend["items"], [{"label": "08:00", "value": 80.0, "samples": 10}])
        week_trend = self.store.get_metric_trend("stress", "week", "2026-08-13")
        self.assertEqual(week_trend["items"][0]["label"], "2026-08-13")
        self.assertEqual(week_trend["items"][0]["value"], 30.0)
        window = self.store.query_heart_rate_window(
            "2026-08-13T08:00:00+08:00", "2026-08-13T08:02:00+08:00"
        )
        self.assertEqual(window["sample_count"], 3)
        self.assertTrue(self.store.delete_import(import_id))
        self.assertIsNone(self.store.get_daily_health("2026-08-13")["heart_rate"])


if __name__ == "__main__":
    unittest.main()
