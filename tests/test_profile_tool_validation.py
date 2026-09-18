from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.source_tools import load_symbol, load_symbols


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_validator():
    return load_symbol("validate_profile_tool_updates")


def load_equipment_helpers():
    return load_symbols({"merge_profile_updates_with_existing", "equipment_change_preview"})


class ProfileToolValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validate = staticmethod(load_validator())
        helpers = load_equipment_helpers()
        cls.merge_equipment = staticmethod(helpers["merge_profile_updates_with_existing"])
        cls.preview_equipment = staticmethod(helpers["equipment_change_preview"])

    def test_accepts_valid_function_arguments(self) -> None:
        updates, fields = self.validate(
            {
                "equipment_change": {"mode": "add", "items": ["跳绳"]},
                "height_cm": 184,
            }
        )
        self.assertEqual(updates["equipment"], {"mode": "add", "items": ["跳绳"]})
        self.assertEqual(updates["height_cm"], 184.0)
        self.assertEqual(fields, ["height_cm", "equipment"])

    def test_rejects_invalid_or_untrusted_function_arguments(self) -> None:
        updates, fields = self.validate(
            {
                "equipment_change": {"mode": "replace", "items": ["哪些" * 20]},
                "height_cm": 999,
                "unknown": "value",
            }
        )
        self.assertEqual(updates, {})
        self.assertEqual(fields, [])

    def test_questions_cannot_propose_profile_updates(self) -> None:
        updates, fields = self.validate(
            {"equipment_change": {"mode": "replace", "items": ["哑铃", "跳绳"]}},
            "我的器械是不是只有哑铃和跳绳？",
        )
        self.assertEqual(updates, {})
        self.assertEqual(fields, [])

    def test_equipment_changes_have_explicit_add_remove_replace_semantics(self) -> None:
        profile = {"equipment": ["哑铃", "哑铃凳", "跳绳"]}
        for mode, items, expected in (
            ("add", ["壶铃"], ["哑铃", "哑铃凳", "跳绳", "壶铃"]),
            ("remove", ["跳绳"], ["哑铃", "哑铃凳"]),
            ("replace", ["杠铃"], ["杠铃"]),
        ):
            with self.subTest(mode=mode):
                updates = {"equipment": {"mode": mode, "items": items}}
                merged = self.merge_equipment(profile, updates)
                self.assertEqual(merged["equipment"], expected)

    def test_equipment_preview_reports_additions_and_removals(self) -> None:
        preview = self.preview_equipment(
            {"equipment": ["哑铃", "哑铃凳"]},
            {"equipment": {"mode": "replace", "items": ["哑铃", "壶铃"]}},
        )
        self.assertEqual(preview["added"], ["壶铃"])
        self.assertEqual(preview["removed"], ["哑铃凳"])

    def test_remove_can_leave_an_explicitly_empty_equipment_list(self) -> None:
        merged = self.merge_equipment(
            {"equipment": ["哑铃"]},
            {"equipment": {"mode": "remove", "items": ["哑铃"]}},
        )
        self.assertEqual(merged["equipment"], [])


if __name__ == "__main__":
    unittest.main()
