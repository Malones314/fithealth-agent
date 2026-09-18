from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fithealth_agent import info_store, storage


class DataDeletionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.storage = storage
        self.info = info_store

    def test_migrates_legacy_records_and_deletes_by_stable_id(self) -> None:
        path = self.root / "records.json"
        path.write_text(
            json.dumps([
                {"date": "2026-08-12", "category": "training", "record": {}},
                {"date": "2026-08-13", "category": "running", "record": {}},
            ]),
            encoding="utf-8",
        )
        store = self.storage.DailyRecordStore(path)
        records = store.list_records()
        self.assertTrue(all(item.get("id") for item in records))
        self.assertTrue(all(item.get("created_at") for item in records))

        removed_id = records[0]["id"]
        self.assertTrue(store.delete_record(removed_id))
        remaining = store.list_records()
        self.assertEqual(len(remaining), 1)
        self.assertNotEqual(remaining[0]["id"], removed_id)
        self.assertFalse(store.delete_record("missing"))

    def test_batch_delete_profile_reset_and_memory_clear(self) -> None:
        record_store = self.storage.DailyRecordStore(self.root / "records.json")
        first = record_store.add_record("2026-08-11", "training", {})
        second = record_store.add_record("2026-08-12", "training", {})
        third = record_store.add_record("2026-08-13", "running", {})
        self.assertEqual(record_store.delete_records([first["id"], third["id"]]), 2)
        self.assertEqual(record_store.list_records()[0]["id"], second["id"])

        profile_store = self.storage.UserProfileStore(self.root / "profile.json")
        profile_store.update_profile({"height_cm": 184, "goal": "减脂", "weekly_weight_kg": [80]})
        reset = profile_store.reset()
        self.assertIsNone(reset["height_cm"])
        self.assertEqual(reset["weekly_weight_kg"], [])

        info_store = self.info.InfoStore(self.root / "memories.json")
        expiry = datetime.now(timezone.utc) + timedelta(days=3)
        one = info_store.add_entry("one", {}, expiry)
        info_store.add_entry("two", {}, expiry)
        self.assertTrue(info_store.delete_entry(one["id"]))
        self.assertEqual(len(info_store.get_all()), 1)
        self.assertEqual(info_store.clear(), 1)
        self.assertEqual(info_store.get_all(), [])

    def test_daily_record_idempotency_key_prevents_duplicate_write(self) -> None:
        store = self.storage.DailyRecordStore(self.root / "records.json")
        first = store.add_record(
            "2026-08-14",
            "training",
            {"sets": 3},
            idempotency_key="pending-workout-1",
        )
        replay = store.add_record(
            "2026-08-14",
            "training",
            {"sets": 3},
            idempotency_key="pending-workout-1",
        )

        self.assertEqual(first["id"], replay["id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(store.list_records()), 1)


if __name__ == "__main__":
    unittest.main()
