from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import main
from fastapi.testclient import TestClient
from fithealth_agent.backup_service import LocalBackupService
from fithealth_agent.external_model_settings import ExternalModelSettingsStore
from fithealth_agent import fit_parser
from fithealth_agent.health_importer import HealthImportService
from fithealth_agent.health_store import HealthStore
from fithealth_agent.muscle_map import MuscleHit
from fithealth_agent import muscle_map
from fithealth_agent.muscle_recovery import SorenessReport, build_recovery_snapshot
from fithealth_agent.soreness_store import SorenessStore
from fithealth_agent.runtime import deps


BJ = ZoneInfo("Asia/Shanghai")


class SeventhBatchDataSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    @staticmethod
    def sleep_csv() -> bytes:
        return """睡眠分数 1 天,
日期,2026-08-14
睡眠时长,6时 48分
睡眠分数,79
深度睡眠持续时间,1时 41分
轻度睡眠持续时间,3时 42分
快速眼动持续时间,1时 25分
清醒时间,43分
""".encode("utf-8")

    def test_broken_privacy_settings_fail_closed(self) -> None:
        path = self.root / "external_model_settings.json"
        path.write_text("{broken", encoding="utf-8")
        store = ExternalModelSettingsStore(path)
        self.assertFalse(store.get()["external_models_enabled"])
        self.assertFalse(store.storage_status()["available"])

    def test_raw_file_audit_lists_orphans_and_missing_references(self) -> None:
        store = HealthStore(self.root / "health.db", self.root / "health-imports")
        service = HealthImportService(store)
        imported = service.import_file("睡眠.csv", self.sleep_csv())
        self.assertTrue(imported["raw_path"].endswith(".csv"))
        referenced = store.resolve_raw_file(imported["raw_path"])
        self.assertIsNotNone(referenced)
        referenced.unlink()
        orphan = store.raw_dir / "orphan.zip"
        orphan.write_bytes(b"orphan")
        audit = store.audit_raw_files()
        self.assertEqual(audit["orphans"], ["orphan.zip"])
        self.assertEqual(audit["missing"], [imported["raw_path"]])
        self.assertTrue(store.delete_orphan_raw_file("orphan.zip"))

    def test_concurrent_duplicate_import_keeps_one_database_row_and_raw_file(self) -> None:
        store = HealthStore(self.root / "health.db", self.root / "health-imports")
        service = HealthImportService(store)
        content = self.sleep_csv()
        results: list[dict] = []
        threads = [threading.Thread(target=lambda: results.append(service.import_file("睡眠.csv", content))) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(store.list_imports()), 1)
        raw_path = store.resolve_raw_file(store.list_imports()[0]["raw_path"])
        self.assertTrue(raw_path.is_file())
        self.assertTrue(any(item["duplicate"] for item in results))

    def test_backup_round_trip_clears_quarantine_when_backup_has_none(self) -> None:
        for name, content in {
            "daily_records.json": b"[]", "user_profile.json": b"{}",
            "training_plans.json": b"[]", "info_store.json": b"[]",
        }.items():
            (self.root / name).write_bytes(content)
        service = LocalBackupService(self.root)
        backup = service.export_bytes()
        quarantine = self.root / "workout-quarantine"
        quarantine.mkdir()
        (quarantine / "pending_workout.corrupt-test.json").write_text("{}", encoding="utf-8")
        service.restore(backup)
        self.assertEqual(list(quarantine.iterdir()), [])

    def test_food_analysis_returns_server_signed_confidence(self) -> None:
        analysis = {
            "items": [], "total_kcal": 0, "protein_g": 0,
            "carbs_g": 0, "fat_g": 0, "range_low_kcal": 0,
            "range_high_kcal": 0, "confidence": "low", "assumptions": [],
        }
        with (
            patch.object(
                deps.external_model_settings_store,
                "get",
                return_value={"external_models_enabled": True},
            ),
            patch.object(deps, "analyze_food_image", return_value=analysis),
        ):
            response = TestClient(main.app).post(
                "/analyze_food",
                files={"file": ("meal.jpg", b"image", "image/jpeg")},
            )
        self.assertEqual(response.status_code, 200)
        token = response.json().get("analysis_token", "")
        self.assertTrue(token.startswith("low."))
        self.assertEqual(len(token.split(".", 1)[1]), 64)


class SeventhBatchRecoveryAndAgentTest(unittest.TestCase):
    def test_model_mapping_cache_invalidates_with_record_mtime(self) -> None:
        hit = MuscleHit("quadriceps", "股四头肌", "腿部", "primary")
        muscle_map._MODEL_RESOLUTION_CACHE.clear()
        with (
            patch.object(muscle_map, "_records_mtime_ns", side_effect=[1, 1, 2]),
            patch.object(muscle_map, "query_muscles_with_lite_model", return_value=[hit]) as query,
        ):
            muscle_map.cached_model_muscle_resolution("自定义动作")
            muscle_map.cached_model_muscle_resolution("自定义动作")
            muscle_map.cached_model_muscle_resolution("自定义动作")
        self.assertEqual(query.call_count, 2)

    def test_snapshot_exposes_history_and_future_skips(self) -> None:
        now = datetime(2026, 8, 26, 12, tzinfo=BJ)
        records = []
        for day in (24, 25):
            records.append({
                "date": f"2026-08-{day}", "category": "training", "record": {
                    "segments": [{
                        "segment_type": "set_active", "category": "深蹲",
                        "start_time": f"2026-08-{day}T10:00:00+08:00",
                    }],
                },
            })
        records.append({
            "date": "2026-08-27", "category": "training", "record": {
                "segments": [
                    {
                        "segment_type": "set_active", "category": "深蹲",
                        "start_time": "2026-08-27T10:00:00+08:00",
                    },
                    {
                        "segment_type": "set_active", "category": "深蹲",
                        "start_time": "2026-08-27T10:02:00+08:00",
                    },
                ],
            },
        })
        snapshot = build_recovery_snapshot(records, now=now, allow_external_models=False)
        quadriceps = snapshot.by_muscle["quadriceps"]
        self.assertEqual(len(quadriceps.history), 2)
        self.assertEqual(snapshot.skipped_future, 1)
        warnings = [
            item for item in main._recovery_checkin_items(snapshot)
            if item.get("warning") == "future_timestamp"
        ]
        self.assertEqual(warnings[0]["skipped_records"], 1)

    def test_validation_uses_injected_resolver(self) -> None:
        called: list[str] = []
        resolver = lambda name: called.append(name) or [MuscleHit("quadriceps", "股四头肌", "腿部", "primary")]
        violations = main.validate_generated_training_plan(
            "神秘动作：3组", [], safety_constraints=["腿部受伤"], resolver=resolver,
        )
        self.assertTrue(called)
        self.assertTrue(any("健康限制" in item for item in violations))

    def test_compound_subject_returns_every_region(self) -> None:
        self.assertEqual(main._subject_recovery_regions("背部+手臂训练"), {"背部", "手臂"})


class SeventhBatchMultisportTest(unittest.TestCase):
    def test_multisport_aggregates_fields_and_assigns_each_message_once(self) -> None:
        start = datetime(2026, 8, 26, 1, tzinfo=timezone.utc)
        summaries = [
            fit_parser.SessionSummary(
                "跑步", "running", start_time=start, total_elapsed_s=60,
                total_timer_s=55, total_distance_m=1000, total_calories=80,
                avg_hr=100, max_hr=130, avg_speed_mps=2, max_speed_mps=3,
                avg_cadence=80, total_ascent_m=5, total_descent_m=4,
                avg_power_w=100, training_effect=2, anaerobic_effect=1,
            ),
            fit_parser.SessionSummary(
                "骑行", "cycling", start_time=None, total_elapsed_s=120,
                total_timer_s=110, total_distance_m=2000, total_calories=120,
                avg_hr=160, max_hr=180, avg_speed_mps=4, max_speed_mps=6,
                avg_cadence=90, total_ascent_m=10, total_descent_m=8,
                avg_power_w=200, training_effect=3, anaerobic_effect=2,
            ),
        ]
        raw = {
            "record": [],
            "lap": [
                {"marker": "timed", "start_time": start},
                {"marker": "untimed"},
            ],
            "set": [],
        }
        seen: list[str] = []

        class RecordingParser:
            def parse(self, local, _hr_records, summary):
                result = []
                for item in [*local.get("lap", []), *local.get("set", [])]:
                    seen.append(item["marker"])
                    segment_start = item.get("start_time") or start + timedelta(seconds=180)
                    result.append(fit_parser.ActivitySegment(
                        1, "lap", segment_start,
                        segment_start + timedelta(seconds=10), 10,
                    ))
                return result

        with patch.object(fit_parser, "_pick_parser", return_value=RecordingParser()):
            session, segments, note = fit_parser._parse_multisport(raw, [], summaries)

        self.assertEqual(seen, ["timed", "untimed"])
        self.assertEqual(len(segments), 2)
        self.assertEqual(session.sport, "多运动（跑步+骑行）")
        self.assertEqual(session.total_distance_m, 3000)
        self.assertEqual(session.total_ascent_m, 15)
        self.assertEqual(session.avg_hr, 140)
        self.assertAlmostEqual(session.avg_speed_mps, 10 / 3)
        self.assertEqual(session.avg_power_w, 167)
        self.assertEqual(session.training_effect, 3)
        self.assertEqual(note, "")

    def test_multisport_reports_messages_that_cannot_be_attributed(self) -> None:
        start = datetime(2026, 8, 26, 1, tzinfo=timezone.utc)
        summaries = [
            fit_parser.SessionSummary("跑步", "running", start_time=start, total_elapsed_s=60),
            fit_parser.SessionSummary(
                "骑行", "cycling", start_time=start + timedelta(seconds=60),
                total_elapsed_s=60,
            ),
        ]
        raw = {"record": [], "lap": [], "set": [{"marker": "untimed"}]}
        with patch.object(fit_parser, "_pick_parser") as picker:
            picker.return_value.parse.return_value = []
            _session, _segments, note = fit_parser._parse_multisport(raw, [], summaries)
        self.assertIn("set 1 条", note)


class SeventhBatchSorenessCorruptionTest(unittest.TestCase):
    def test_bad_row_is_skipped_and_session_intro_stays_available(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = Path(directory) / "muscle_soreness.json"
            now = datetime.now(BJ)
            valid = SorenessStore._serialize(SorenessReport(
                "手臂", ("biceps",), "sore", now, now + timedelta(hours=72),
                evidence="手臂酸",
            ))
            path.write_text(
                json.dumps([valid, {"region": "坏区域", "level": "???"}], ensure_ascii=False),
                encoding="utf-8",
            )
            store = SorenessStore(path)
            self.assertEqual(len(store.list_reports(active_only=True, now=now)), 1)
            with patch.object(deps, "soreness_store", store):
                response = TestClient(main.app).get("/session/intro")
            self.assertEqual(response.status_code, 200)

    def test_same_level_edit_preserves_ttl_and_region_edit_supersedes_duplicate(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            now = datetime(2026, 8, 26, 12, tzinfo=BJ)
            first = store.add_reports([SorenessReport(
                "手臂", ("biceps",), "sore", now, now + timedelta(hours=72),
            )])[0]
            edited = store.update_report(
                first.id, region="手臂", level="sore", evidence="仍有轻微酸痛",
                now=now + timedelta(hours=24),
            )
            self.assertEqual(edited.reported_at, now)
            self.assertEqual(edited.expires_at, now + timedelta(hours=72))

            store.add_reports([SorenessReport(
                "腿部", ("quadriceps",), "sore", now, now + timedelta(hours=72),
            )])
            moved = store.update_report(
                first.id, region="腿部", level="painful", now=now + timedelta(hours=25),
            )
            active_leg = [
                item for item in store.list_reports(
                    active_only=True, now=now + timedelta(hours=25)
                )
                if item.region == "腿部"
            ]
            self.assertEqual([item.id for item in active_leg], [moved.id])


if __name__ == "__main__":
    unittest.main()
