from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import main
from fithealth_agent.hr_stream_store import HRStreamStore
from fithealth_agent.muscle_recovery import (
    LoadWarning,
    MuscleLoad,
    MuscleRecoverySnapshot,
    SorenessReport,
    build_recovery_snapshot,
)
from fithealth_agent.weekly_summary import build_current_week_reply


BJ = ZoneInfo("Asia/Shanghai")


def training_record(day: int, sets: int) -> dict:
    return {
        "date": f"2026-08-{day:02d}",
        "category": "training",
        "record": {
            "segments": [
                {
                    "segment_type": "set_active",
                    "category": "深蹲",
                    "start_time": f"2026-08-{day:02d}T10:{index:02d}:00+08:00",
                }
                for index in range(sets)
            ]
        },
    }


class HeartRateStreamAuditTest(unittest.TestCase):
    def test_lists_missing_and_orphan_streams_and_only_deletes_orphans(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = HRStreamStore(Path(directory))
            store.save("kept", [{"timestamp": "2026-08-26T10:00:00+08:00", "heart_rate": 120}])
            store.save("orphan", [{"timestamp": "2026-08-26T10:00:00+08:00", "heart_rate": 125}])
            records = [
                {"id": "kept", "record": {"hr_stream": {"file": "kept.json"}}},
                {"id": "missing", "record": {"hr_stream": {"file": "missing.json"}}},
            ]
            audit = store.audit(records)
            self.assertEqual(audit["orphans"], ["orphan.json"])
            self.assertEqual(
                audit["missing"],
                [{"file": "missing.json", "record_ids": ["missing"]}],
            )
            self.assertFalse(store.delete_orphan("kept.json", records))
            self.assertTrue(store.delete_orphan("orphan.json", records))


class TrainingLoadWarningTest(unittest.TestCase):
    def test_three_consecutive_days_create_cumulative_load_warning(self) -> None:
        snapshot = build_recovery_snapshot(
            [training_record(day, 4) for day in (24, 25, 26)],
            now=datetime(2026, 8, 26, 12, tzinfo=BJ),
            allow_external_models=False,
        )
        quadriceps = [
            warning for warning in snapshot.load_warnings
            if warning.muscle_id == "quadriceps"
            and warning.kind == "consecutive_days"
        ]
        self.assertEqual(quadriceps[0].consecutive_days, 3)
        self.assertIn("连续 3 天", quadriceps[0].message)
        self.assertTrue(snapshot.by_muscle["quadriceps"].needs_reduction)

    def test_volume_spike_uses_previous_seven_day_training_average(self) -> None:
        snapshot = build_recovery_snapshot(
            [training_record(19, 4), training_record(22, 4), training_record(26, 8)],
            now=datetime(2026, 8, 26, 12, tzinfo=BJ),
            allow_external_models=False,
        )
        warning = next(
            item for item in snapshot.load_warnings
            if item.muscle_id == "quadriceps" and item.kind == "volume_spike"
        )
        self.assertEqual(warning.latest_sets, 8)
        self.assertEqual(warning.baseline_sets, 4)
        self.assertEqual(warning.ratio, 2)

    def test_warning_rejects_normal_volume_but_allows_two_recovery_sets(self) -> None:
        now = datetime(2026, 8, 26, 12, tzinfo=BJ)
        load = MuscleLoad(
            "quadriceps", "股四头肌", "腿部", now, "周三", ("深蹲",),
            8, 48, now + timedelta(hours=24), 24, needs_reduction=True,
        )
        warning = LoadWarning(
            "quadriceps", "股四头肌", "腿部", "volume_spike",
            "股四头肌训练量突增", 8, baseline_sets=4, ratio=2,
        )
        snapshot = MuscleRecoverySnapshot(
            loads=(load,), recovering=(load,), load_warnings=(warning,)
        )
        self.assertTrue(any(
            "低容量恢复训练" in item
            for item in main.validate_generated_training_plan(
                "深蹲：4组", [], recovery=snapshot
            )
        ))
        self.assertFalse(any(
            "低容量恢复训练" in item
            for item in main.validate_generated_training_plan(
                "深蹲：2组", [], recovery=snapshot
            )
        ))


class F4WeeklySummaryTest(unittest.TestCase):
    def test_weekly_summary_includes_capacity_recovery_sleep_score_and_both_nutrition_sources(self) -> None:
        now = datetime(2026, 8, 26, 10, tzinfo=BJ)
        load = MuscleLoad(
            "quadriceps", "股四头肌", "腿部", now, "周三", ("深蹲",),
            3, 48, now + timedelta(hours=20), 20,
            history=((now, 3, ("深蹲",)),),
        )
        recovery = MuscleRecoverySnapshot(loads=(load,), recovering=(load,))
        soreness = SorenessReport(
            "手臂", ("biceps",), "sore", now, now + timedelta(hours=72)
        )

        class Health:
            def get_health_range(self, start_date, end_date):
                return [{
                    "date": "2026-08-26", "heart_rate": None, "stress": None,
                    "activity": None, "hrv": None,
                    "sleep": {"duration_min": 420, "score": 82},
                }]

        class Records:
            def list_records(self):
                return [training_record(26, 3), {
                    "date": "2026-08-26", "category": "daily_checkin",
                    "record": {
                        "calories_kcal": 700, "protein_g": 50,
                        "carbs_g": 90, "fat_g": 20,
                        "meal_estimates": [
                            {
                                "source": "food_photo_estimate", "total_kcal": 500,
                                "protein_g": 35, "carbs_g": 65, "fat_g": 15,
                            },
                            {
                                "source": "text_nutrition", "total_kcal": 200,
                                "protein_g": 15, "carbs_g": 25, "fat_g": 5,
                            },
                        ],
                    },
                }]

        reply = build_current_week_reply(
            "查看本周数据", health_store=Health(), daily_record_store=Records(),
            profile={}, today=date(2026, 8, 26), recovery=recovery,
            soreness_reports=[soreness],
        )
        self.assertIn("睡眠分数", reply)
        self.assertIn("平均睡眠分数：82", reply)
        self.assertIn("共 3 个动作组", reply)
        self.assertIn("区域有效容量：腿部 3 组", reply)
        self.assertIn("股四头肌还需 20 小时", reply)
        self.assertIn("手臂酸痛", reply)
        self.assertIn("图片估算 1 餐/1 天", reply)
        self.assertIn("文字/手动营养记录 1 天", reply)


if __name__ == "__main__":
    unittest.main()
