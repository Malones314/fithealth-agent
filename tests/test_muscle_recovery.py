from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fithealth_agent.muscle_map import muscles_for_exercise, muscles_for_sport, resolve_muscles_for_exercise
from fithealth_agent.muscle_recovery import build_recovery_snapshot


BJ = ZoneInfo("Asia/Shanghai")


class MuscleMappingTest(unittest.TestCase):
    def test_real_saved_actions_are_all_mapped(self) -> None:
        names = [
            "哑铃平板卧推", "哑铃上斜卧推", "哑铃飞鸟", "俯卧撑",
            "哑铃颈后臂屈伸", "哑铃俯身臂屈伸", "右哑铃单臂划船", "左哑铃单臂划船",
            "哑铃凳胸部支撑划船", "哑铃弯举", "锤式弯举", "哑铃坐姿肩上推举",
            "哑铃侧平举", "高脚杯深蹲", "臀桥/臀推（负重）", "哑铃罗马尼亚硬拉",
        ]
        self.assertTrue(all(muscles_for_exercise(name) for name in names))

    def test_full_keyword_and_exclusion_prevent_core_collision(self) -> None:
        hits = muscles_for_exercise("哑铃平板卧推")
        self.assertIn("chest", {hit.muscle_id for hit in hits})
        self.assertNotIn("core", {hit.muscle_id for hit in hits})
        self.assertEqual(muscles_for_exercise("平板卧推"), hits)

    def test_sport_mapping_and_unknown_are_conservative(self) -> None:
        self.assertEqual({hit.muscle_id for hit in muscles_for_sport("跳绳")}, {"calves", "quadriceps"})
        self.assertEqual(muscles_for_sport("有氧运动"), [])
        self.assertEqual(muscles_for_exercise("周三那个动作"), [])

    def test_model_fallback_is_opt_in_and_validated(self) -> None:
        calls: list[str] = []

        def resolver(name: str):
            calls.append(name)
            return [
                {"muscle_id": "biceps", "role": "primary"},
                {"muscle_id": "not-a-muscle", "role": "primary"},
            ]

        self.assertEqual(resolve_muscles_for_exercise("未知动作", allow_external_models=False), [])
        hits = resolve_muscles_for_exercise("未知动作", allow_external_models=True, model_resolver=resolver)
        self.assertEqual([hit.muscle_id for hit in hits], ["biceps"])
        self.assertEqual(calls, ["未知动作"])


class MuscleRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        strength_start = datetime(2026, 8, 17, 8, tzinfo=BJ)
        exercises = [
            *("哑铃平板卧推" for _ in range(4)),
            *("哑铃上斜卧推" for _ in range(3)),
            *("哑铃飞鸟" for _ in range(3)),
            *("俯卧撑" for _ in range(3)),
        ]
        strength = {
            "date": "2026-08-17",
            "record": {
                "sport": "力量训练",
                "session": {"sport": "力量训练", "start_time": strength_start.isoformat()},
                "segments": [
                    {
                        "segment_type": "set_active",
                        "start_time": (strength_start + timedelta(minutes=index)).isoformat(),
                        "category": exercise,
                        "repetitions": 10,
                        "is_rest": False,
                    }
                    for index, exercise in enumerate(exercises)
                ],
            },
        }
        rope_start = datetime(2026, 8, 15, 8, tzinfo=BJ)
        rope = {
            "date": "2026-08-15",
            "record": {
                "sport": "跳绳",
                "session": {
                    "sport": "跳绳",
                    "start_time": rope_start.isoformat(),
                    "total_timer_s": 1800,
                },
                "segments": [],
            },
        }
        later_start = datetime(2026, 8, 18, 8, tzinfo=BJ)
        later = {
            "date": "2026-08-18",
            "record": {
                "sport": "力量训练",
                "session": {"sport": "力量训练", "start_time": later_start.isoformat()},
                "segments": [{
                    "segment_type": "set_active",
                    "start_time": later_start.isoformat(),
                    "category": "哑铃弯举",
                    "repetitions": 10,
                    "is_rest": False,
                }],
            },
        }
        self.records = [strength, rope, later]

    def test_real_record_volume_and_aerobic_cap(self) -> None:
        strength = next(item for item in self.records if item.get("date") == "2026-08-17")
        snapshot = build_recovery_snapshot(
            [strength], now=datetime(2026, 8, 18, 12, tzinfo=BJ), lookback_days=10
        )
        by_id = snapshot.by_muscle
        self.assertEqual(by_id["chest"].effective_sets, 13.0)
        self.assertGreater(by_id["triceps"].effective_sets, 0)
        self.assertNotIn("core", by_id)

        rope = next(item for item in self.records if item.get("date") == "2026-08-15")
        rope_snapshot = build_recovery_snapshot(
            [rope], now=datetime(2026, 8, 16, 12, tzinfo=BJ), lookback_days=10
        )
        self.assertTrue(rope_snapshot.loads)
        self.assertLessEqual(max(load.effective_sets for load in rope_snapshot.loads), 4.0)

    def test_capacity_adjustment_and_garmin_addition(self) -> None:
        start = datetime(2026, 8, 22, 8, tzinfo=BJ)
        record = {
            "date": "2026-08-22",
            "record": {
                "sport": "力量训练",
                "session": {"sport": "力量训练", "start_time": start.isoformat()},
                "segments": [
                    {
                        "segment_type": "set_active",
                        "start_time": (start + timedelta(minutes=i)).isoformat(),
                        "category": "哑铃弯举",
                        "repetitions": 10,
                        "is_rest": False,
                    }
                    for i in range(8)
                ],
            },
        }
        now = start + timedelta(minutes=30)
        plain = build_recovery_snapshot([record], now=now, garmin_recovery_hours=0)
        boosted = build_recovery_snapshot([record], now=now, garmin_recovery_hours=12)
        self.assertAlmostEqual(plain.by_muscle["biceps"].recovery_hours, 28.0)
        self.assertAlmostEqual(
            boosted.by_muscle["biceps"].hours_remaining
            - plain.by_muscle["biceps"].hours_remaining,
            12.0,
            places=3,
        )

    def test_garmin_does_not_resurrect_already_recovered_muscle(self) -> None:
        start = datetime(2026, 8, 18, 8, tzinfo=BJ)
        record = {
            "date": start.date().isoformat(),
            "record": {
                "sport": "力量训练",
                "session": {"sport": "力量训练", "start_time": start.isoformat()},
                "segments": [
                    {
                        "segment_type": "set_active",
                        "start_time": (start + timedelta(minutes=i)).isoformat(),
                        "category": "哑铃弯举",
                        "repetitions": 10,
                        "is_rest": False,
                    }
                    for i in range(8)
                ],
            },
        }
        recovered = build_recovery_snapshot(
            [record], now=start + timedelta(hours=48), garmin_recovery_hours=0.1
        ).by_muscle["biceps"]
        self.assertEqual(recovered.hours_remaining, 0.0)
        self.assertEqual(recovered.recovered_at, start + timedelta(hours=48))

        still_recovering = build_recovery_snapshot(
            [record], now=start + timedelta(hours=27, minutes=7), garmin_recovery_hours=0.1
        ).by_muscle["biceps"]
        self.assertAlmostEqual(still_recovering.hours_remaining, 1.1, places=3)

    def test_same_day_accumulates_and_deletion_changes_snapshot(self) -> None:
        source = next(item for item in self.records if item.get("date") == "2026-08-17")
        later = next(item for item in self.records if item.get("date") == "2026-08-18")
        now = datetime(2026, 8, 19, 12, tzinfo=BJ)
        all_snapshot = build_recovery_snapshot([source, later], now=now)
        deleted_snapshot = build_recovery_snapshot([later], now=now)
        self.assertNotEqual(all_snapshot.by_muscle["chest"].effective_sets, 0)
        self.assertNotIn("chest", deleted_snapshot.by_muscle)

    def test_duplicate_unknown_action_is_resolved_once_per_snapshot(self) -> None:
        start = datetime(2026, 8, 22, 8, tzinfo=BJ)
        record = {
            "date": "2026-08-22",
            "record": {
                "sport": "力量训练",
                "session": {"sport": "力量训练", "start_time": start.isoformat()},
                "segments": [
                    {
                        "segment_type": "set_active",
                        "start_time": start.isoformat(),
                        "category": "陌生动作",
                        "is_rest": False,
                    },
                    {
                        "segment_type": "set_active",
                        "start_time": (start + timedelta(minutes=1)).isoformat(),
                        "category": "陌生动作",
                        "is_rest": False,
                    },
                ],
            },
        }
        calls: list[str] = []

        def resolver(name: str):
            calls.append(name)
            return [{"muscle_id": "biceps", "role": "primary"}]

        snapshot = build_recovery_snapshot(
            [record], now=start + timedelta(minutes=2), allow_external_models=True, model_resolver=resolver
        )
        self.assertEqual(calls, ["陌生动作"])
        self.assertEqual(snapshot.by_muscle["biceps"].effective_sets, 2.0)


if __name__ == "__main__":
    unittest.main()
