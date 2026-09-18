"""Immediate pending-memory capture for explicit durable chat statements."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from fithealth_agent.info_store import InfoStore
from fithealth_agent.runtime import deps
from fithealth_agent.workflows import chat_workflow


class ImmediateMemoryCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.original_store = deps.info_store
        deps.info_store = InfoStore(Path(self.directory.name) / "info_store.json")
        self.addCleanup(setattr, deps, "info_store", self.original_store)

    @staticmethod
    def decision(*facts: dict) -> dict:
        return {
            "save": True,
            "summary": "用户确认长期训练偏好。",
            "type": "preference",
            "importance": 4,
            "facts": list(facts),
            "reason": "摘要模型明确请求保存",
            "pipeline_stage": "level3",
        }

    def test_explicit_durable_statement_creates_an_unconfirmed_candidate(self) -> None:
        decision = self.decision({
            "namespace": "training", "key": "avoid_exercise", "value": "跳绳",
            "status": "active", "evidence": "以后不要安排跳绳",
        })
        with patch.object(deps, "route_information", return_value=decision) as route:
            candidate = chat_workflow._immediate_memory_candidate(
                "以后不要安排跳绳", allow_external_models=True
            )
        self.assertIsNotNone(candidate)
        route.assert_called_once()
        entry = deps.info_store.get_all()[0]
        self.assertFalse(entry["user_confirmed"])
        self.assertFalse(entry["facts"][0]["user_confirmed"])
        self.assertEqual(entry["metadata"]["capture"], "chat_turn")
        self.assertEqual(deps.info_store.get_context_memories(), [])

    def test_retry_of_the_same_statement_does_not_create_duplicate_candidate(self) -> None:
        decision = self.decision({
            "namespace": "health", "key": "injury_or_constraint", "value": "盆骨前倾",
            "status": "active", "evidence": "我有盆骨前倾",
        })
        with patch.object(deps, "route_information", return_value=decision):
            first = chat_workflow._immediate_memory_candidate("我有盆骨前倾", allow_external_models=True)
            second = chat_workflow._immediate_memory_candidate("我有盆骨前倾", allow_external_models=True)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(deps.info_store.get_all()), 1)
        self.assertEqual(deps.info_store.get_all()[0]["facts"][0]["value"], "骨盆前倾")

    def test_ordinary_message_skips_the_external_memory_router(self) -> None:
        with patch.object(deps, "route_information") as route:
            candidate = chat_workflow._immediate_memory_candidate("今天练什么？", allow_external_models=True)
        self.assertIsNone(candidate)
        route.assert_not_called()

    def test_sedentary_statement_triggers_immediate_candidate_capture(self) -> None:
        decision = self.decision({
            "namespace": "health", "key": "injury_or_constraint", "value": "久坐",
            "status": "active", "evidence": "我平时久坐办公",
        })
        with patch.object(deps, "route_information", return_value=decision) as route:
            candidate = chat_workflow._immediate_memory_candidate(
                "我平时久坐办公", allow_external_models=True
            )
        self.assertIsNotNone(candidate)
        route.assert_called_once()
        self.assertEqual(candidate["facts"][0]["value"], "长期久坐")

    def test_temporary_recovery_fact_is_not_captured_as_long_term_memory(self) -> None:
        decision = self.decision({
            "namespace": "health", "key": "recovery_status", "value": "今天腰部不适",
            "status": "active", "evidence": "今天腰部不适",
        })
        with patch.object(deps, "route_information", return_value=decision):
            candidate = chat_workflow._immediate_memory_candidate("我有今天腰部不适", allow_external_models=True)
        self.assertIsNone(candidate)
        self.assertEqual(deps.info_store.get_all(), [])

    def test_urgent_injury_is_captured_without_an_external_model(self) -> None:
        finding = main.screen_health_risk("我腿断了")
        self.assertIsNotNone(finding)
        with patch.object(deps, "route_information") as route:
            candidate = chat_workflow._acute_injury_memory_candidate("我腿断了", finding)
        self.assertIsNotNone(candidate)
        route.assert_not_called()
        entry = deps.info_store.get_all()[0]
        self.assertEqual(entry["metadata"]["capture"], "chat_safety")
        self.assertEqual(entry["facts"][0]["key"], "recovery_status")
        self.assertEqual(entry["facts"][0]["value"], "腿部：疑似骨折或脱臼")
        self.assertEqual(entry["facts"][0]["evidence"], "我腿断了")
        self.assertFalse(entry["facts"][0]["user_confirmed"])
        self.assertIsNone(chat_workflow._acute_injury_memory_candidate("我腿断了", finding))

    def test_chat_response_exposes_a_created_candidate(self) -> None:
        from fastapi.testclient import TestClient
        from fithealth_agent.chat_intent_router import ChatIntent
        from fithealth_agent.muscle_recovery import MuscleRecoverySnapshot

        acute = {
            "entry_id": "acute",
            "facts": [{"namespace": "health", "key": "recovery_status", "value": "腿部：关节肿胀"}],
        }
        durable = {
            "entry_id": "durable",
            "facts": [{"namespace": "training", "key": "avoid_exercise", "value": "深蹲"}],
        }
        agent = unittest.mock.Mock()
        agent.run.return_value = "先停止下肢训练并观察肿胀情况。"
        profile = {
            "weekly_weight_kg": [70], "height_cm": 175, "birth_date": "1990-01-01",
            "sex": "male", "goal": "增肌", "equipment": ["哑铃"],
        }
        with (
            patch.object(deps, "classify_user_health_statement", return_value=True),
            patch.object(deps, "parse_soreness_reply", return_value=[]),
            patch.object(chat_workflow, "_acute_injury_memory_candidate", return_value=acute) as acute_capture,
            patch.object(chat_workflow, "_immediate_memory_candidate", return_value=durable) as durable_capture,
            patch.object(chat_workflow, "_current_recovery_snapshot", return_value=MuscleRecoverySnapshot()),
            patch.object(deps, "route_chat_intent", return_value=ChatIntent()),
            patch.object(deps, "build_current_week_reply", return_value=None),
            patch.object(deps, "create_fithealth_agent", return_value=agent),
            patch.object(deps.profile_store, "get_profile", return_value=profile),
            patch.object(deps.profile_store, "is_complete", return_value=True),
            patch.object(
                deps.external_model_settings_store,
                "get",
                return_value={"external_models_enabled": True},
            ),
        ):
            response = TestClient(main.app).post("/chat", json={
                "message": "我最近膝盖有点肿，以后别给我安排深蹲了",
                "history": [],
                "source": "chat",
            })

        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        acute_capture.assert_called_once()
        durable_capture.assert_called_once()
        self.assertEqual(body["memory_candidate"], acute)
        self.assertEqual(body["memory_candidates"], [acute, durable])


if __name__ == "__main__":
    unittest.main()
