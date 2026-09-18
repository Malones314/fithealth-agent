from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fithealth_agent.info_store import (
    apply_fact_retention,
    BODY_POSTURE_SYNONYMS,
    canonicalize_body_posture_value,
    entry_expiry_for_facts,
    fact_is_active,
    normalize_memory_facts,
    resolve_confirmed_memory_facts,
)


class MemoryValidityTest(unittest.TestCase):
    def test_temporary_recovery_fact_gets_default_seven_day_window(self) -> None:
        facts = normalize_memory_facts([{
            "namespace": "health",
            "key": "recovery_status",
            "value": "膝盖疼痛",
            "status": "active",
        }])
        enriched = apply_fact_retention(
            facts,
            datetime(2026, 8, 19, tzinfo=timezone.utc),
        )[0]
        self.assertEqual(enriched["duration_type"], "temporary")
        self.assertEqual(enriched["valid_from"], "2026-08-19")
        self.assertEqual(enriched["valid_until"], "2026-08-26")

    def test_fact_is_active_respects_inclusive_validity_dates(self) -> None:
        fact = {
            "valid_from": "2026-08-19",
            "valid_until": "2026-09-02",
            "expires_at": None,
        }
        self.assertFalse(fact_is_active(fact, datetime(2026, 8, 18, tzinfo=timezone.utc)))
        self.assertTrue(fact_is_active(fact, datetime(2026, 9, 2, 12, tzinfo=timezone.utc)))
        self.assertFalse(fact_is_active(fact, datetime(2026, 9, 3, tzinfo=timezone.utc)))

    def test_entry_expiry_does_not_cut_off_longer_temporary_window(self) -> None:
        created = datetime(2026, 8, 19, tzinfo=timezone.utc)
        facts = apply_fact_retention(normalize_memory_facts([{
            "namespace": "health", "key": "recovery_status",
            "value": "膝盖疼痛", "status": "active",
            "duration_type": "temporary", "valid_from": "2026-08-19",
            "valid_until": "2026-09-02",
        }]), created)
        expiry = entry_expiry_for_facts(created, "training_feedback", facts)
        self.assertIsNotNone(expiry)
        self.assertEqual(expiry.date().isoformat(), "2026-09-03")

    def test_long_term_health_fact_does_not_keep_temporary_end_date(self) -> None:
        fact = normalize_memory_facts([{
            "namespace": "health",
            "key": "injury_or_constraint",
            "value": "骨盆前倾",
            "status": "active",
            "duration_type": "long_term",
            "valid_from": "2026-08-19",
            "valid_until": "2026-09-02",
        }])[0]
        self.assertEqual(fact["duration_type"], "long_term")
        self.assertNotIn("valid_until", fact)

    def test_invalid_or_reversed_dates_are_rejected(self) -> None:
        invalid = normalize_memory_facts([{
            "namespace": "health", "key": "recovery_status",
            "value": "腰部不适", "status": "active",
            "duration_type": "temporary", "valid_from": "2026-09-02",
            "valid_until": "2026-08-19",
        }])
        self.assertEqual(invalid, [])

    def test_negative_exercise_phrase_is_never_a_positive_preference(self) -> None:
        facts = normalize_memory_facts([
            {
                "namespace": "training",
                "key": "prefer_exercise",
                "value": "我不喜欢深蹲",
                "status": "active",
            },
            {
                "namespace": "training",
                "key": "prefer_exercise",
                "value": "硬拉",
                "status": "active",
            },
        ])
        self.assertEqual([(fact["key"], fact["value"]) for fact in facts], [("prefer_exercise", "硬拉")])

    def test_every_configured_posture_synonym_uses_its_canonical_label(self) -> None:
        for canonical, aliases in BODY_POSTURE_SYNONYMS.items():
            for alias in (canonical, *aliases):
                with self.subTest(canonical=canonical, alias=alias):
                    self.assertEqual(canonicalize_body_posture_value(alias), [canonical])
                    facts = normalize_memory_facts([{
                        "namespace": "health",
                        "key": "injury_or_constraint",
                        "value": alias,
                        "status": "active",
                    }])
                    self.assertEqual(facts[0]["value"], canonical)

    def test_legacy_synonyms_resolve_as_one_confirmed_fact(self) -> None:
        memories = [
            {
                "created_at": "2026-08-19T00:00:00+00:00",
                "user_confirmed": True,
                "facts": [{
                    "namespace": "health", "key": "injury_or_constraint",
                    "value": "盆骨前倾", "status": "active",
                }],
            },
            {
                "created_at": "2026-08-20T00:00:00+00:00",
                "user_confirmed": True,
                "facts": [{
                    "namespace": "health", "key": "injury_or_constraint",
                    "value": "假翘臀", "status": "active",
                }],
            },
        ]
        facts = resolve_confirmed_memory_facts(memories)
        self.assertEqual([(fact["key"], fact["value"]) for fact in facts], [("injury_or_constraint", "骨盆前倾")])

    def test_multiple_different_conditions_expand_to_independent_facts(self) -> None:
        self.assertEqual(
            canonicalize_body_posture_value("盆骨前倾且核心无力"),
            ["骨盆前倾", "腹部核心薄弱"],
        )
        facts = normalize_memory_facts([{
            "namespace": "health",
            "key": "injury_or_constraint",
            "value": "盆骨前倾且核心无力",
            "status": "active",
        }])
        self.assertEqual(
            [fact["value"] for fact in facts],
            ["骨盆前倾", "腹部核心薄弱"],
        )
        self.assertEqual(len({fact["fact_id"] for fact in facts}), 2)

    def test_sedentary_chinese_variants_share_one_canonical_value(self) -> None:
        for value in ("久坐", "长期久坐", "久坐人群"):
            with self.subTest(value=value):
                self.assertEqual(canonicalize_body_posture_value(value), ["长期久坐"])

    def test_recovery_updates_replace_matching_body_part_without_touching_sleep(self) -> None:
        values = (
            ("大腿内侧酸痛", "active"),
            ("大腿内侧酸痛（已减轻）", "active"),
            ("大腿内侧酸痛已明显缓解，发力时仅有轻微酸痛感", "active"),
            ("睡眠不足/没睡好，疲劳", "active"),
        )
        memories = [
            {
                "created_at": f"2026-08-{24 + index:02d}T0{index}:00:00+00:00",
                "user_confirmed": True,
                "facts": [{
                    "namespace": "health", "key": "recovery_status",
                    "value": value, "status": status, "user_confirmed": True,
                }],
            }
            for index, (value, status) in enumerate(values)
        ]
        current = resolve_confirmed_memory_facts(memories)
        self.assertEqual(
            {str(fact["value"]) for fact in current},
            {"大腿内侧酸痛已明显缓解，发力时仅有轻微酸痛感", "睡眠不足/没睡好，疲劳"},
        )

        memories.append({
            "created_at": "2026-08-28T00:00:00+00:00",
            "user_confirmed": True,
            "facts": [{
                "namespace": "health", "key": "recovery_status",
                "value": "大腿已恢复", "evidence": "大腿已恢复",
                "status": "cleared", "user_confirmed": True,
            }],
        })
        resolved = resolve_confirmed_memory_facts(memories)
        self.assertEqual([fact["value"] for fact in resolved], ["睡眠不足/没睡好，疲劳"])


if __name__ == "__main__":
    unittest.main()
