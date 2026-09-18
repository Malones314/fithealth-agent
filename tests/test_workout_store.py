from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier


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


class WorkoutStoreRestoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_cwd = Path.cwd()
        self.addCleanup(self._cleanup)
        import os

        self.previous_data_dir = os.environ.get("FITHEALTH_DATA_DIR")
        os.chdir(self.temp_dir.name)
        os.environ["FITHEALTH_DATA_DIR"] = str(Path(self.temp_dir.name) / "data")

        self._module_names = (
            "fithealth_agent.workout_store",
            "fithealth_agent.fit_parser",
            "fithealth_agent",
        )
        self._saved_modules = {name: sys.modules.get(name) for name in self._module_names}
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
        self.fit_parser = load_module(
            "fithealth_agent.fit_parser", PACKAGE_DIR / "fit_parser.py"
        )

    def _cleanup(self) -> None:
        import os

        if self.previous_data_dir is None:
            os.environ.pop("FITHEALTH_DATA_DIR", None)
        else:
            os.environ["FITHEALTH_DATA_DIR"] = self.previous_data_dir
        os.chdir(self.previous_cwd)
        self.temp_dir.cleanup()
        for name in self._module_names:
            original = self._saved_modules[name]
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def load_store(self):
        sys.modules.pop("fithealth_agent.workout_store", None)
        return load_module(
            "fithealth_agent.workout_store", PACKAGE_DIR / "workout_store.py"
        )

    def test_restores_pending_workout_and_hr_stream_after_restart(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
        segments = [
            self.fit_parser.ActivitySegment(
                1,
                "set_active",
                start,
                start + timedelta(seconds=10),
                10,
                category="深蹲",
                repetitions=5,
                weight_kg=60,
            ),
            self.fit_parser.ActivitySegment(
                2,
                "set_rest",
                start + timedelta(seconds=10),
                start + timedelta(seconds=15),
                5,
                category="组间休息",
                category_raw="rest",
                is_rest=True,
            ),
            self.fit_parser.ActivitySegment(
                3,
                "set_active",
                start + timedelta(seconds=15),
                start + timedelta(seconds=25),
                10,
                category="深蹲",
                repetitions=5,
                weight_kg=60,
            ),
        ]
        heart_rates = [
            self.fit_parser.HRRecord(start + timedelta(seconds=i), 100 + i)
            for i in range(26)
        ]
        activity = self.fit_parser.ParsedActivity(
            session=self.fit_parser.SessionSummary(
                "力量训练", "strength_training", start_time=start
            ),
            segments=segments,
            hr_records=heart_rates,
            source_file="sample.fit",
            parsed_at="2026-08-12T01:30:00+00:00",
        )

        store.set_current(activity)
        self.assertTrue(Path("data/pending_workout.json").exists())
        initial_confirmation = store.get_confirmation_context()
        self.assertEqual(initial_confirmation["version"], 1)

        restored_store = self.load_store()
        restored = restored_store.get_current()
        self.assertIsNotNone(restored)
        self.assertTrue(restored_store.was_restored_from_disk())
        self.assertEqual(restored.source_file, "sample.fit")
        self.assertEqual(len(restored.segments), 3)
        self.assertEqual(len(restored.hr_records), 26)
        self.assertEqual(restored_store.get_confirmation_context(), initial_confirmation)

        merged = restored_store.merge_sets([1, 3])
        self.assertEqual(merged["repetitions"], 10)
        self.assertEqual(merged["duration_s"], 25.0)
        self.assertEqual(merged["start_time"], start.isoformat())
        self.assertEqual(merged["end_time"], (start + timedelta(seconds=25)).isoformat())
        self.assertEqual(merged["avg_hr"], 112)
        self.assertEqual(merged["max_hr"], 125)
        self.assertEqual(merged["hr_note"], "已从原始心率数据重新计算")
        self.assertEqual(merged["absorbed_rest_indices"], [2])
        self.assertEqual(merged["absorbed_rest_duration_s"], 5.0)
        self.assertEqual(len(restored_store.get_current().segments), 1)
        self.assertFalse(any(segment.is_rest for segment in restored_store.get_current().segments))

        updated = restored_store.update_set(
            1, category="杠铃深蹲", weight_kg=62.5, repetitions=12
        )
        self.assertEqual(updated["category"], "杠铃深蹲")
        self.assertEqual(updated["weight_kg"], 62.5)
        self.assertEqual(updated["repetitions"], 12)

        rejected = restored_store.update_sets(
            [{"index": 1, "category": "", "weight_kg": 70, "repetitions": 8}]
        )
        self.assertIn("error", rejected)
        self.assertEqual(restored_store.get_current().segments[0].category, "杠铃深蹲")

        batch = restored_store.update_sets(
            [{"index": 1, "category": "高杠深蹲", "weight_kg": 65, "repetitions": 10}]
        )
        self.assertTrue(batch["updated"])
        self.assertEqual(restored_store.get_current().segments[0].category, "高杠深蹲")
        self.assertEqual(restored_store.get_current().segments[0].repetitions, 10)

        confirmation = restored_store.get_confirmation_context()
        result = restored_store.update_sets_and_confirm(
            [{"index": 1, "category": "低杠深蹲", "weight_kg": 67.5, "repetitions": 9}],
            "状态良好，最后一组较吃力",
            workout_id=confirmation["workout_id"],
            version=confirmation["version"],
            confirmation_token=confirmation["confirmation_token"],
        )
        self.assertTrue(result["saved"])
        records = json.loads(Path("data/daily_records.json").read_text(encoding="utf-8"))
        saved_segment = records[0]["record"]["segments"][0]
        self.assertEqual(saved_segment["category"], "低杠深蹲")
        self.assertEqual(saved_segment["weight_kg"], 67.5)
        self.assertEqual(saved_segment["repetitions"], 9)
        self.assertEqual(records[0]["date"], "2026-08-12")
        self.assertEqual(records[0]["record"]["note"], "状态良好，最后一组较吃力")
        self.assertEqual(
            records[0]["record"]["workout_start_time_beijing"],
            "2026-08-12T09:00:00+08:00",
        )
        self.assertIsNone(restored_store.get_current())
        self.assertFalse(Path("data/pending_workout.json").exists())

    def test_confirmation_rejects_stale_invalid_and_repeated_requests(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)
        store.set_current(
            self.fit_parser.ParsedActivity(
                self.fit_parser.SessionSummary(
                    "力量训练", "strength_training", start_time=start
                ),
                [
                    self.fit_parser.ActivitySegment(
                        1,
                        "set_active",
                        start,
                        start + timedelta(seconds=20),
                        20,
                        category="深蹲",
                        repetitions=5,
                        weight_kg=60,
                    )
                ],
                [],
                source_file="confirmation.fit",
            )
        )
        first = store.get_confirmation_context()
        self.assertEqual(first["version"], 1)

        store.update_set(1, weight_kg=62.5)
        current = store.get_confirmation_context()
        self.assertEqual(current["version"], 2)
        self.assertNotEqual(current["confirmation_token"], first["confirmation_token"])

        updates = [
            {"index": 1, "category": "深蹲", "weight_kg": 62.5, "repetitions": 5}
        ]
        stale = store.update_sets_and_confirm(
            updates,
            workout_id=first["workout_id"],
            version=first["version"],
            confirmation_token=first["confirmation_token"],
        )
        self.assertEqual(stale["code"], "STALE_WORKOUT_VERSION")
        self.assertIsNotNone(store.get_current())

        invalid = store.update_sets_and_confirm(
            updates,
            workout_id=current["workout_id"],
            version=current["version"],
            confirmation_token="x" * 43,
        )
        self.assertEqual(invalid["code"], "INVALID_CONFIRMATION_TOKEN")
        self.assertIsNotNone(store.get_current())

        barrier = Barrier(2)

        def confirm_once():
            barrier.wait()
            return store.update_sets_and_confirm(
                updates,
                workout_id=current["workout_id"],
                version=current["version"],
                confirmation_token=current["confirmation_token"],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: confirm_once(), range(2)))

        self.assertEqual(sum(bool(result.get("saved")) for result in results), 1)
        rejected = next(result for result in results if not result.get("saved"))
        self.assertEqual(rejected["code"], "NO_PENDING_WORKOUT")
        self.assertIsNone(store.get_current())
        records = json.loads(Path("data/daily_records.json").read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)

    def test_same_fit_hash_is_not_saved_twice_under_different_filenames(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)

        def activity(filename: str):
            return self.fit_parser.ParsedActivity(
                self.fit_parser.SessionSummary("跳绳", "jump_rope", start_time=start),
                [self.fit_parser.ActivitySegment(1, "lap", start, start + timedelta(seconds=60), 60)],
                [], source_file=filename, source_sha256="a" * 64,
            )

        store.set_current(activity("跳绳1.fit"))
        first_context = store.get_confirmation_context()
        first = store.confirm_workout(**first_context)
        self.assertTrue(first["saved"])
        self.assertFalse(first["duplicate_training"])

        store.set_current(activity("跳绳2.fit"))
        second_context = store.get_confirmation_context()
        second = store.confirm_workout(**second_context)
        self.assertTrue(second["saved"])
        self.assertTrue(second["duplicate_training"])
        records = json.loads(Path("data/daily_records.json").read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)

    def test_same_start_and_sport_is_not_saved_twice_without_matching_hash(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)

        def activity(filename: str, digest: str):
            return self.fit_parser.ParsedActivity(
                self.fit_parser.SessionSummary("跳绳", "jump_rope", start_time=start),
                [], [], source_file=filename, source_sha256=digest,
            )

        store.set_current(activity("跳绳1.fit", "a" * 64))
        first = store.confirm_workout(**store.get_confirmation_context())
        self.assertFalse(first["duplicate_training"])

        store.set_current(activity("跳绳2.fit", "b" * 64))
        second = store.confirm_workout(**store.get_confirmation_context())
        self.assertTrue(second["duplicate_training"])
        records = json.loads(Path("data/daily_records.json").read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)

    def test_overwrite_without_new_heart_rate_preserves_existing_stream(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)

        def activity(digest: str, *, with_hr: bool):
            return self.fit_parser.ParsedActivity(
                self.fit_parser.SessionSummary(
                    "力量训练", "strength_training", start_time=start
                ),
                [self.fit_parser.ActivitySegment(
                    1, "set_active", start, start + timedelta(seconds=10), 10,
                    category="深蹲", repetitions=5,
                )],
                [self.fit_parser.HRRecord(start, 120)] if with_hr else [],
                source_sha256=digest,
            )

        store.set_current(activity("a" * 64, with_hr=True))
        first = store.confirm_workout(**store.get_confirmation_context())
        records = json.loads(Path("data/daily_records.json").read_text(encoding="utf-8"))
        old_pointer = records[0]["record"]["hr_stream"]
        self.assertTrue(Path("data/hr_streams", f"{first['record_id']}.json").is_file())

        store.set_current(activity("b" * 64, with_hr=False))
        second = store.confirm_workout(
            **store.get_confirmation_context(), overwrite_duplicate=True
        )
        self.assertTrue(second["overwritten"])
        records = json.loads(Path("data/daily_records.json").read_text(encoding="utf-8"))
        self.assertEqual(records[0]["record"]["hr_stream"], old_pointer)

    def test_saved_name_uses_first_active_segment_start_time(self) -> None:
        store = self.load_store()
        session_start = datetime(2026, 8, 13, 0, 50, tzinfo=timezone.utc)
        first_active_start = datetime(2026, 8, 13, 1, 7, tzinfo=timezone.utc)
        workout = self.fit_parser.ParsedActivity(
            self.fit_parser.SessionSummary(
                "跳绳", "jump_rope", start_time=session_start
            ),
            [
                self.fit_parser.ActivitySegment(
                    1, "set_rest", session_start, first_active_start, 17,
                    category="组间休息", is_rest=True,
                ),
                self.fit_parser.ActivitySegment(
                    2, "lap", first_active_start,
                    first_active_start + timedelta(seconds=60), 60,
                ),
            ],
            [],
        )
        store.set_current(workout)
        result = store.confirm_workout(**store.get_confirmation_context())
        self.assertTrue(result["saved"])
        records = json.loads(Path("data/daily_records.json").read_text(encoding="utf-8"))
        self.assertEqual(records[0]["record"]["name"], "26-08-13-09-07-跳绳")

    def test_merge_rejects_unselected_active_set_inside_time_window(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
        segments = [
            self.fit_parser.ActivitySegment(
                index,
                "set_active",
                start + timedelta(seconds=(index - 1) * 10),
                start + timedelta(seconds=index * 10),
                10,
                category="深蹲",
                repetitions=5,
            )
            for index in (1, 2, 3)
        ]
        store.set_current(
            self.fit_parser.ParsedActivity(
                self.fit_parser.SessionSummary("力量训练", "strength_training"),
                segments,
                [],
            )
        )

        result = store.merge_sets([1, 3])
        self.assertIn("error", result)
        self.assertIn("2", result["error"])
        self.assertEqual(len(store.get_current().segments), 3)

    def test_merge_expands_to_full_intersecting_rest_boundaries(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
        segments = [
            self.fit_parser.ActivitySegment(1, "set_active", start + timedelta(seconds=20), start + timedelta(seconds=30), 10, category="深蹲", repetitions=5),
            self.fit_parser.ActivitySegment(2, "set_rest", start + timedelta(seconds=10), start + timedelta(seconds=25), 15, is_rest=True),
            self.fit_parser.ActivitySegment(3, "set_active", start + timedelta(seconds=40), start + timedelta(seconds=50), 10, category="深蹲", repetitions=5),
            self.fit_parser.ActivitySegment(4, "set_rest", start + timedelta(seconds=30), start + timedelta(seconds=40), 10, is_rest=True),
        ]
        store.set_current(self.fit_parser.ParsedActivity(
            self.fit_parser.SessionSummary("力量训练", "strength_training"), segments, [],
        ))
        result = store.merge_sets([1, 3])
        self.assertNotIn("error", result)
        merged = store.get_current().segments[0]
        self.assertEqual(merged.start_time, start + timedelta(seconds=10))
        self.assertEqual(merged.end_time, start + timedelta(seconds=50))
        self.assertEqual(merged.duration_s, 40)

    def test_undo_and_restore_source_survive_restart(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
        activity = self.fit_parser.ParsedActivity(
            self.fit_parser.SessionSummary("力量训练", "strength_training", start_time=start),
            [
                self.fit_parser.ActivitySegment(
                    index, "set_active", start + timedelta(seconds=(index - 1) * 10),
                    start + timedelta(seconds=index * 10), 10,
                    category="卧推", repetitions=10, weight_kg=60,
                )
                for index in (1, 2)
            ],
            [],
            source_file="original.fit",
        )
        store.set_current(activity)
        original_confirmation = store.get_confirmation_context()

        store.update_set(1, category="上斜卧推", weight_kg=70)
        persisted = json.loads(Path("data/pending_workout.json").read_text(encoding="utf-8"))
        self.assertIn("parsed_source", persisted)
        self.assertIn("last_edit_snapshot", persisted)
        self.assertTrue(store.get_state_snapshot()["edit_history"]["can_undo"])

        restarted = self.load_store()
        undo = restarted.undo_last_edit()
        self.assertTrue(undo["undone"])
        self.assertEqual(restarted.get_current().segments[0].category, "卧推")
        self.assertEqual(restarted.get_current().segments[0].weight_kg, 60)
        self.assertFalse(restarted.get_state_snapshot()["edit_history"]["can_undo"])
        self.assertNotEqual(restarted.get_confirmation_context(), original_confirmation)

        restarted.delete_set(2)
        restored_again = self.load_store()
        restored = restored_again.restore_parsed_source()
        self.assertTrue(restored["restored"])
        self.assertEqual(len(restored_again.get_current().segments), 2)
        self.assertEqual(restored_again.get_current().segments[0].category, "卧推")
        self.assertFalse(
            restored_again.get_state_snapshot()["edit_history"]["can_restore_parsed_source"]
        )

    def test_undo_is_unavailable_before_any_edit(self) -> None:
        store = self.load_store()
        start = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
        store.set_current(
            self.fit_parser.ParsedActivity(
                self.fit_parser.SessionSummary("力量训练", "strength_training", start_time=start),
                [self.fit_parser.ActivitySegment(
                    1, "set_active", start, start + timedelta(seconds=10), 10,
                    category="卧推", repetitions=10, weight_kg=60,
                )],
                [],
            )
        )
        self.assertIn("error", store.undo_last_edit())

    def test_restores_pending_workout_with_empty_segments(self) -> None:
        pending_path = Path("data/pending_workout.json")
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        original = ('{"version": 2, "sets": [], "session": '
                    '{"sport":"跑步","sport_raw":"running"}, "hr_records": []}')
        pending_path.write_text(original, encoding="utf-8")

        store = self.load_store()

        self.assertIsNotNone(store.get_current())
        self.assertEqual(store.get_current().segments, [])
        self.assertTrue(store.was_restored_from_disk())
        self.assertTrue(pending_path.exists())
        self.assertIsNone(store.get_restore_issue())

        reloaded_store = self.load_store()
        self.assertIsNotNone(reloaded_store.get_current())
        self.assertIsNone(reloaded_store.get_restore_issue())

    def test_quarantines_malformed_pending_workout_json(self) -> None:
        pending_path = Path("data/pending_workout.json")
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text('{"version": 2,', encoding="utf-8")

        store = self.load_store()

        self.assertIsNone(store.get_current())
        self.assertFalse(pending_path.exists())
        self.assertEqual(
            len(list((pending_path.parent / "workout-quarantine").glob("pending_workout.corrupt-*.json"))),
            1,
        )
        self.assertEqual(store.get_restore_issue()["action"], "quarantined")


if __name__ == "__main__":
    unittest.main()
