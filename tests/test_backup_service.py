from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fithealth_agent import backup_service


class BackupServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = backup_service

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        (self.root / "daily_records.json").write_text("[]", encoding="utf-8")
        (self.root / "user_profile.json").write_text(json.dumps({"goal": "maintain"}), encoding="utf-8")
        (self.root / "training_plans.json").write_text("[]", encoding="utf-8")
        (self.root / "info_store.json").write_text("[]", encoding="utf-8")
        self.service = self.module.LocalBackupService(self.root)

    def test_export_validate_and_restore_json_data(self) -> None:
        backup = self.service.export_bytes()
        validated = self.service.validate(backup)
        self.assertIn("user_profile.json", validated)
        (self.root / "user_profile.json").write_text(json.dumps({"goal": "changed"}), encoding="utf-8")
        result = self.service.restore(backup)
        self.assertEqual(result["restored_files"], 4)
        restored = json.loads((self.root / "user_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(restored["goal"], "maintain")

    def test_optional_soreness_file_is_backed_up_when_present(self) -> None:
        soreness = [{"id": "one", "region": "手臂", "level": "sore"}]
        (self.root / "muscle_soreness.json").write_text(
            json.dumps(soreness, ensure_ascii=False), encoding="utf-8"
        )
        backup = self.service.export_bytes()
        validated = self.service.validate(backup)
        self.assertIn("muscle_soreness.json", validated)
        (self.root / "muscle_soreness.json").write_text("[]", encoding="utf-8")
        self.service.restore(backup)
        self.assertEqual(
            json.loads((self.root / "muscle_soreness.json").read_text(encoding="utf-8")),
            soreness,
        )

    def test_rejects_invalid_backup(self) -> None:
        with self.assertRaises(ValueError):
            self.service.validate(b"not-a-zip")

    def test_export_rejects_oversized_sources_before_reading_members(self) -> None:
        with (
            patch.object(self.module, "MAX_UNCOMPRESSED_BYTES", 1),
            patch.object(self.service, "_collect_members") as collect,
        ):
            with self.assertRaisesRegex(ValueError, "备份解压上限"):
                self.service.export_bytes()
        collect.assert_not_called()

    def test_restore_rolls_back_all_replaced_files_when_switch_fails(self) -> None:
        backup = self.service.export_bytes()
        original_values = {
            "daily_records.json": b'["before"]',
            "user_profile.json": b'{"goal":"before"}',
            "training_plans.json": b'["before"]',
            "info_store.json": b'["before"]',
        }
        for name, value in original_values.items():
            (self.root / name).write_bytes(value)

        original_replace = self.module.os.replace

        def fail_during_staged_switch(source, target):
            if Path(source).parent.name == "staged" and Path(target).name == "training_plans.json":
                raise OSError("simulated replace failure")
            return original_replace(source, target)

        with patch.object(self.module.os, "replace", side_effect=fail_during_staged_switch):
            with self.assertRaisesRegex(OSError, "已回滚"):
                self.service.restore(backup)

        for name, value in original_values.items():
            self.assertEqual((self.root / name).read_bytes(), value)
        self.assertFalse(list(self.root.glob(".restore-*")))


if __name__ == "__main__":
    unittest.main()
