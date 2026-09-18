from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient

import main
from fithealth_agent.routes import settings as settings_routes
from fithealth_agent.workflows import chat_workflow
from fithealth_agent.muscle_recovery import MuscleLoad, MuscleRecoverySnapshot


class SessionIntroMuscleRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main.app)

    def test_intro_returns_read_only_recovery_checkin(self) -> None:
        response = self.client.get("/session/intro", params={"garmin_recovery_hours": "0"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["garmin_recovery_hours"], 0.0)
        self.assertTrue(payload["garmin_recovery_hours_valid"])
        self.assertIsInstance(payload["muscle_recovery_checkin"], list)
        self.assertIn("### 肌群恢复状态", payload["message"])
        self.assertIn("不是医学测量", payload["message"])
        self.assertIn("仅当前会话", payload["message"])

    def test_garmin_hours_are_request_scoped_and_not_persisted(self) -> None:
        trained_at = datetime(2026, 8, 25, 8, tzinfo=ZoneInfo("Asia/Shanghai"))

        def snapshot(hours: float) -> MuscleRecoverySnapshot:
            remaining = 10.0 + hours
            load = MuscleLoad(
                muscle_id="quadriceps", zh="股四头肌", region="腿部",
                last_trained_at=trained_at, weekday_zh="周二", exercises=("深蹲",),
                effective_sets=4.0, recovery_hours=24.0,
                recovered_at=trained_at + timedelta(hours=24),
                hours_remaining=remaining, raw_sets=4.0,
            )
            return MuscleRecoverySnapshot(
                loads=(load,), recovering=(load,), garmin_recovery_hours=hours
            )

        with mock.patch.object(settings_routes, "_current_recovery_snapshot", side_effect=snapshot):
            plain = self.client.get("/session/intro", params={"garmin_recovery_hours": "0"}).json()
            boosted = self.client.get("/session/intro", params={"garmin_recovery_hours": "12.5"}).json()
            reset = self.client.get("/session/intro", params={"garmin_recovery_hours": "0"}).json()
        self.assertEqual(boosted["garmin_recovery_hours"], 12.5)
        self.assertEqual(reset["garmin_recovery_hours"], 0.0)
        plain_by_id = {item["muscle_id"]: item for item in plain["muscle_recovery_checkin"]}
        boosted_by_id = {item["muscle_id"]: item for item in boosted["muscle_recovery_checkin"]}
        self.assertEqual(set(plain_by_id), {"quadriceps"}, "fixture 不能为空，否则差值循环会假绿")
        self.assertEqual(plain_by_id.keys(), boosted_by_id.keys())
        for muscle_id in plain_by_id:
            expected_delta = 12.5 if plain_by_id[muscle_id]["hours_remaining"] > 0 else 0.0
            self.assertAlmostEqual(
                boosted_by_id[muscle_id]["hours_remaining"]
                - plain_by_id[muscle_id]["hours_remaining"],
                expected_delta,
                places=2,
            )

    def test_invalid_garmin_hours_are_rejected(self) -> None:
        for value in ("96.1", "-1", "not-a-number"):
            response = self.client.get("/session/intro", params={"garmin_recovery_hours": value})
            self.assertEqual(response.status_code, 400, value)
            self.assertFalse(response.json()["garmin_recovery_hours_valid"])

    def test_upper_garmin_boundary_is_accepted(self) -> None:
        response = self.client.get("/session/intro", params={"garmin_recovery_hours": "96.0"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["garmin_recovery_hours"], 96.0)

    def test_recovering_muscles_are_listed_first_and_recovered_are_wrapped(self) -> None:
        recovered = SimpleNamespace(
            muscle_id="biceps", zh="肱二头肌", region="手臂", weekday_zh="周一",
            exercises=("哑铃弯举",), effective_sets=4.0, hours_remaining=0.0,
        )
        recovering = SimpleNamespace(
            muscle_id="quadriceps", zh="股四头肌", region="腿部", weekday_zh="周三",
            exercises=("高脚杯深蹲",), effective_sets=4.0, hours_remaining=18.2,
        )
        lines = main._format_muscle_recovery_lines((recovered, recovering))
        self.assertIn("股四头肌", lines[0])
        self.assertTrue(lines[1].startswith("- ~~"))
        self.assertTrue(lines[1].endswith("~~"))
        self.assertIn("肱二头肌", lines[1])

    def test_stage_three_feeds_garmin_value_into_chat(self) -> None:
        observed: list[float] = []

        def snapshot(hours: float) -> MuscleRecoverySnapshot:
            observed.append(hours)
            return MuscleRecoverySnapshot(garmin_recovery_hours=hours)

        with mock.patch.object(chat_workflow, "_current_recovery_snapshot", side_effect=snapshot):
            response = self.client.post(
                "/chat",
                json={"message": "你好", "history": [], "garmin_recovery_hours": 8},
            )
        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(observed[0], 8.0)


if __name__ == "__main__":
    unittest.main()
