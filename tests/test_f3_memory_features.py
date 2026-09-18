from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import main
from fithealth_agent.workflows import chat_workflow
from fithealth_agent.chat_intent_router import ChatIntent
from fithealth_agent.info_store import InfoStore, fact_is_active, resolve_confirmed_memory_facts
from fithealth_agent.muscle_recovery import MuscleRecoverySnapshot
from fithealth_agent.runtime import deps


def fact(namespace: str, key: str, value: object, **extra: object) -> dict[str, object]:
    return {
        "namespace": namespace,
        "key": key,
        "value": value,
        "status": "active",
        "evidence": str(value),
        **extra,
    }


class F3InfoStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "info_store.json"
        self.store = InfoStore(self.path)

    def add(self, *facts: dict[str, object], confirmed: bool = True) -> dict:
        return self.store.add_entry(
            "test", {}, None, memory_type="constraint", importance=3,
            user_confirmed=confirmed, facts=list(facts),
        )

    def test_forget_by_slot_or_exact_value_and_preserve_audit(self) -> None:
        self.add(
            fact("training", "avoid_exercise", "深蹲"),
            fact("training", "avoid_exercise", "硬拉"),
        )
        result = self.store.forget_facts("training", "avoid_exercise", "深蹲")
        self.assertEqual(result["cleared"], 1)
        active = resolve_confirmed_memory_facts(self.store.get_all())
        self.assertEqual([item["value"] for item in active], ["硬拉"])

        result = self.store.forget_facts("training", "avoid_exercise")
        self.assertEqual(result["cleared"], 1)
        reloaded = InfoStore(self.path).get_all()[0]["facts"]
        cleared = {item["value"]: item for item in reloaded}
        self.assertEqual(cleared["深蹲"]["clear_reason"], "user_request")
        self.assertTrue(cleared["深蹲"]["history"])
        self.assertEqual(resolve_confirmed_memory_facts(InfoStore(self.path).get_all()), [])

    def test_edit_history_usage_and_rollback_survive_reload(self) -> None:
        entry = self.add(fact("training", "avoid_exercise", "深蹲"))
        original_id = entry["facts"][0]["fact_id"]
        edited = self.store.edit_fact(entry["id"], value="硬拉", fact_id=original_id)
        edited_id = edited["fact"]["fact_id"]
        self.store.log_fact_usage([edited_id], action="plan_validation", detail="阻止硬拉")
        reloaded = InfoStore(self.path)
        persisted = reloaded.get_all()[0]["facts"][0]
        self.assertEqual(persisted["previous_value"], "深蹲")
        self.assertEqual(persisted["usage_log"][-1]["detail"], "阻止硬拉")
        rolled = reloaded.rollback_fact(entry["id"], fact_id=edited_id)
        self.assertEqual(rolled["fact"]["value"], "深蹲")
        self.assertFalse(rolled["fact"]["user_confirmed"])

    def test_intent_relevance_and_date_range_expiry(self) -> None:
        self.add(fact("plan", "preference", "简洁"))
        safe = self.add(fact("training", "avoid_exercise", "深蹲"))
        ranked = self.store.get_context_memories(n=1, intent="create_training_plan")
        self.assertEqual(ranked[0]["id"], safe["id"])

        constraint = main.temporary_constraint_for_message(
            "这周都别安排腿", today=date(2026, 8, 26)
        )
        self.assertEqual(constraint["valid_until"], "2026-08-30")
        ranged = fact(
            "health", "injury_or_constraint", constraint["value"],
            scope=constraint["scope"], duration_type=constraint["duration_type"],
            valid_from=constraint["valid_from"], valid_until=constraint["valid_until"],
        )
        entry = self.add(ranged)
        stored = entry["facts"][0]
        self.assertEqual(stored["scope"], "date_range")
        self.assertTrue(fact_is_active(stored, main.datetime(2026, 8, 30, tzinfo=main.timezone.utc)))
        self.assertFalse(fact_is_active(stored, main.datetime(2026, 8, 31, tzinfo=main.timezone.utc)))

    def test_usage_attribution_excludes_unrelated_facts(self) -> None:
        entry = self.add(
            fact("training", "avoid_exercise", "深蹲"),
            fact("training", "avoid_exercise", "硬拉"),
        )
        usage = main.plan_validation_fact_usage(
            [entry], ["包含被禁止动作：深蹲"]
        )
        used_ids = {fact_id for fact_id, _detail in usage}
        by_value = {item["value"]: item["fact_id"] for item in entry["facts"]}
        self.assertIn(by_value["深蹲"], used_ids)
        self.assertNotIn(by_value["硬拉"], used_ids)


class F3ApiAndChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.original_store = deps.info_store
        deps.info_store = InfoStore(Path(self.directory.name) / "info_store.json")
        self.addCleanup(setattr, deps, "info_store", self.original_store)
        self.client = TestClient(main.app)

    def add(self, *facts: dict[str, object], confirmed: bool = True) -> dict:
        return deps.info_store.add_entry(
            "test", {}, None, memory_type="constraint", importance=3,
            user_confirmed=confirmed, facts=list(facts),
        )

    def test_forget_and_rollback_endpoints(self) -> None:
        entry = self.add(fact("training", "avoid_exercise", "深蹲"))
        old_id = entry["facts"][0]["fact_id"]
        edited = deps.info_store.edit_fact(entry["id"], value="硬拉", fact_id=old_id)
        response = self.client.post(
            f"/data/memories/{entry['id']}/facts/{edited['fact']['fact_id']}/rollback",
            json={"history_index": -1},
        )
        self.assertEqual(response.status_code, 200, response.json())
        deps.info_store.confirm_entry(entry["id"])
        response = self.client.post("/data/memories/forget", json={
            "namespace": "training", "key": "avoid_exercise", "value": "深蹲",
        })
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["cleared"], 1)

    def test_chat_confirmation_requires_submitted_candidate_id(self) -> None:
        entry = self.add(fact("training", "avoid_exercise", "深蹲"), confirmed=False)
        profile = {"goal": "增肌"}
        with (
            patch.object(deps.profile_store, "get_profile", return_value=profile),
            patch.object(deps.external_model_settings_store, "get", return_value={"external_models_enabled": False}),
        ):
            plain = self.client.post("/chat", json={"message": "好的", "history": []})
            self.assertNotEqual(plain.json().get("source"), "memory_confirmation")
            accepted = self.client.post("/chat", json={
                "message": "记住", "history": [], "pending_memory_entry_ids": [entry["id"]],
            })
        self.assertEqual(accepted.status_code, 200, accepted.json())
        self.assertTrue(accepted.json()["memory_confirmation"]["accepted"])
        self.assertTrue(deps.info_store.get_all()[0]["facts"][0]["user_confirmed"])

    def test_chat_can_reject_the_candidate_from_the_previous_turn(self) -> None:
        entry = self.add(fact("training", "avoid_exercise", "深蹲"), confirmed=False)
        with (
            patch.object(deps.profile_store, "get_profile", return_value={"goal": "增肌"}),
            patch.object(deps.external_model_settings_store, "get", return_value={"external_models_enabled": False}),
        ):
            response = self.client.post("/chat", json={
                "message": "不用", "history": [], "pending_memory_entry_ids": [entry["id"]],
            })
        self.assertEqual(response.status_code, 200, response.json())
        self.assertFalse(response.json()["memory_confirmation"]["accepted"])
        self.assertEqual(deps.info_store.get_all()[0]["facts"][0]["status"], "rejected")

    def test_plan_auto_correction_runs_once_and_returns_corrected_artifact(self) -> None:
        first = "训练计划\n深蹲：4组\n" + "训练说明。" * 80
        corrected = "训练计划\n臀桥：3组\n热身：5分钟\n拉伸：5分钟\n" + "训练说明。" * 80
        first_agent = Mock()
        first_agent.run.return_value = first
        correction_agent = Mock()
        correction_agent.run.return_value = corrected
        profile = {
            "weekly_weight_kg": [70], "height_cm": 175, "birth_date": "1990-01-01",
            "sex": "male", "goal": "增肌", "equipment": ["哑铃"],
        }
        intent = ChatIntent(create_training_plan=True, training_plan_subject="臀部")
        with (
            patch.object(deps, "classify_user_health_statement", return_value=False),
            patch.object(chat_workflow, "_current_recovery_snapshot", return_value=MuscleRecoverySnapshot()),
            patch.object(deps, "route_chat_intent", return_value=intent),
            patch.object(deps, "build_current_week_reply", return_value=None),
            patch.object(
                deps,
                "validate_plan_goal_alignment",
                return_value={"passed": True, "missing_subjects": [], "stage": "test"},
            ),
            patch.object(
                deps,
                "create_fithealth_agent",
                side_effect=[first_agent, correction_agent],
            ) as create_agent,
            patch.object(chat_workflow, "looks_like_complete_training_plan", return_value=True),
            patch.object(chat_workflow, "validate_generated_training_plan", side_effect=[["包含被禁止动作：深蹲"], []]),
            patch.object(deps.profile_store, "get_profile", return_value=profile),
            patch.object(deps.profile_store, "is_complete", return_value=True),
            patch.object(deps.external_model_settings_store, "get", return_value={"external_models_enabled": True}),
        ):
            response = self.client.post("/chat", json={"message": "生成臀部训练计划", "history": []})
        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(create_agent.call_count, 2)
        first_agent.run.assert_called_once()
        correction_agent.run.assert_called_once()
        self.assertEqual(body["artifact"]["source"], "agent_auto_corrected")
        self.assertTrue(body["plan_auto_correction"]["passed"])

    def test_plan_auto_correction_stops_after_second_failure(self) -> None:
        plan = "训练计划\n深蹲：4组\n热身：5分钟\n拉伸：5分钟\n" + "训练说明。" * 80
        first_agent = Mock()
        first_agent.run.return_value = plan
        correction_agent = Mock()
        correction_agent.run.return_value = plan
        profile = {
            "weekly_weight_kg": [70], "height_cm": 175, "birth_date": "1990-01-01",
            "sex": "male", "goal": "增肌", "equipment": ["哑铃"],
        }
        intent = ChatIntent(create_training_plan=True, training_plan_subject="腿部")
        with (
            patch.object(deps, "classify_user_health_statement", return_value=False),
            patch.object(chat_workflow, "_current_recovery_snapshot", return_value=MuscleRecoverySnapshot()),
            patch.object(deps, "route_chat_intent", return_value=intent),
            patch.object(deps, "build_current_week_reply", return_value=None),
            patch.object(
                deps,
                "create_fithealth_agent",
                side_effect=[first_agent, correction_agent],
            ) as create_agent,
            patch.object(chat_workflow, "looks_like_complete_training_plan", return_value=True),
            patch.object(chat_workflow, "validate_generated_training_plan", side_effect=[["违规一"], ["违规二"]]),
            patch.object(deps.profile_store, "get_profile", return_value=profile),
            patch.object(deps.profile_store, "is_complete", return_value=True),
            patch.object(deps.external_model_settings_store, "get", return_value={"external_models_enabled": True}),
        ):
            response = self.client.post("/chat", json={"message": "生成腿部训练计划", "history": []})
        body = response.json()
        self.assertEqual(create_agent.call_count, 2)
        first_agent.run.assert_called_once()
        correction_agent.run.assert_called_once()
        self.assertIsNone(body["artifact"])
        self.assertEqual(body["plan_auto_correction"]["final_violations"], ["违规二"])

    def test_explicit_cardio_subject_wins_over_the_weekly_schedule(self) -> None:
        plan = (
            "# 有氧训练计划\n\n热身：动态活动 5 分钟\n"
            "跑步：3组 x 10分钟\n拉伸：5分钟\n" + "训练说明。" * 80
        )
        agent = Mock()
        agent.run.return_value = plan
        profile = {
            "weekly_weight_kg": [70], "height_cm": 175, "birth_date": "1990-01-01",
            "sex": "male", "goal": "增肌", "equipment": ["哑铃"],
        }
        intent = ChatIntent(
            create_training_plan=True,
            training_plan_subject="有氧训练",
            training_plan_title="今日有氧",
            # 模拟轻量路由器误判：明确当前科目却仍返回 follow。
            schedule_decision="follow",
        )
        scheduled = (
            __import__("datetime").date(2026, 8, 28),
            {"type": "training", "subject": "背部训练"},
        )
        observed_subjects: list[str] = []

        def validate(_answer, _memories, expected_subject, **_kwargs):
            observed_subjects.append(expected_subject)
            return []

        with (
            patch.object(deps, "classify_user_health_statement", return_value=False),
            patch.object(chat_workflow, "_current_recovery_snapshot", return_value=MuscleRecoverySnapshot()),
            patch.object(deps, "route_chat_intent", return_value=intent),
            patch.object(deps, "build_current_week_reply", return_value=None),
            patch.object(deps, "create_fithealth_agent", return_value=agent) as create_agent,
            patch.object(chat_workflow, "looks_like_complete_training_plan", return_value=True),
            patch.object(chat_workflow, "validate_generated_training_plan", side_effect=validate),
            patch.object(chat_workflow, "scheduled_weekly_entry_for_message", return_value=scheduled),
            patch.object(deps.profile_store, "get_profile", return_value=profile),
            patch.object(deps.profile_store, "is_complete", return_value=True),
            patch.object(deps.external_model_settings_store, "get", return_value={"external_models_enabled": True}),
        ):
            response = self.client.post(
                "/chat", json={"message": "今天想有氧", "history": []}
            )

        body = response.json()
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["artifact"]["subject"], "有氧训练")
        self.assertEqual(body["plan_context"]["decision"], "override_today")
        self.assertEqual(body["plan_context"]["effective_subject"], "有氧训练")
        self.assertEqual(observed_subjects, ["有氧训练"])
        create_agent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
