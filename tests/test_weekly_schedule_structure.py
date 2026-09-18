"""Structured weekly-schedule parsing and effective-date behavior."""

from __future__ import annotations

from datetime import date
import json
import unittest

from fithealth_agent.info_store import (
    normalize_weekly_schedule,
    parse_weekly_schedule,
    weekly_schedule_entry_for_date,
)


class WeeklyScheduleStructureTest(unittest.TestCase):
    def test_legacy_schedule_accepts_a_null_rest_day(self) -> None:
        schedule = parse_weekly_schedule('{"mon":"胸部训练","wed":null}')
        self.assertEqual(schedule["days"]["mon"], {"type": "training", "subject": "胸部训练"})
        self.assertEqual(schedule["days"]["wed"], {"type": "rest"})

    def test_structured_schedule_supports_aerobic_dates_and_enabled_flag(self) -> None:
        value = json.dumps({
            "enabled": True,
            "effective_from": "2026-08-17",
            "effective_until": "2026-08-30",
            "days": {
                "wed": {"type": "rest"},
                "fri": {"type": "aerobic", "subject": "跑步"},
            },
        })
        self.assertIsNone(weekly_schedule_entry_for_date(value, date(2026, 8, 14)))
        self.assertEqual(
            weekly_schedule_entry_for_date(value, date(2026, 8, 19)),
            {"type": "rest"},
        )
        self.assertEqual(
            weekly_schedule_entry_for_date(value, date(2026, 8, 21)),
            {"type": "aerobic", "subject": "跑步"},
        )
        disabled = value.replace('"enabled": true', '"enabled": false')
        self.assertIsNone(weekly_schedule_entry_for_date(disabled, date(2026, 8, 21)))

    def test_aerobic_day_can_omit_its_subject(self) -> None:
        schedule = parse_weekly_schedule('{"days":{"fri":{"type":"aerobic"}}}')
        self.assertEqual(schedule["days"]["fri"], {"type": "aerobic", "subject": "有氧训练"})

    def test_mixed_legacy_keys_keep_valid_days_and_report_skipped_keys(self) -> None:
        schedule = parse_weekly_schedule({"mon": "胸部训练", "周二": "背部训练"})
        self.assertEqual(
            schedule["days"],
            {"mon": {"type": "training", "subject": "胸部训练"}},
        )
        self.assertEqual(schedule["invalid_days"], ["周二"])
        normalized = normalize_weekly_schedule({"mon": "胸部训练", "周二": "背部训练"})
        self.assertIn('"invalid_days":["周二"]', normalized)
        self.assertEqual(parse_weekly_schedule(normalized)["invalid_days"], ["周二"])

    def test_schedule_with_only_invalid_days_is_rejected(self) -> None:
        self.assertIsNone(parse_weekly_schedule({"周二": "背部训练"}))

    def test_invalid_structure_and_date_ranges_are_rejected(self) -> None:
        for value in (
            '{"days":{"wed":{"type":"rest","unexpected":true}}}',
            '{"effective_from":"2026-09-01","effective_until":"2026-08-31","days":{"wed":null}}',
            '{"days":{"wed":{"type":"unsupported"}}}',
            '{"enabled":"yes","days":{"wed":null}}',
        ):
            with self.subTest(value=value):
                self.assertIsNone(normalize_weekly_schedule(value))


if __name__ == "__main__":
    unittest.main()
