from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import main
from fithealth_agent.workflows import chat_workflow
from fastapi.testclient import TestClient
from fithealth_agent.chat_intent_router import ChatIntent
from fithealth_agent.context_budget import ContextInputError, validate_chat_payload
from fithealth_agent.muscle_recovery import (
    MuscleLoad,
    MuscleRecoverySnapshot,
    SorenessReport,
    parse_soreness_reply,
    soreness_reply_needs_clarification,
)
from fithealth_agent.soreness_store import SorenessStore
from fithealth_agent.runtime import deps


BJ = ZoneInfo("Asia/Shanghai")


class SorenessParserTest(unittest.TestCase):
    def test_explicit_injury_region_is_recorded_even_when_not_prompted(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        reports = parse_soreness_reply(
            "我今天睡落枕了，脖子剧痛无比，完全动不了。",
            ["手臂", "腿部"],
            now=now,
        )
        self.assertEqual([(item.region, item.level) for item in reports], [("颈部", "painful")])
        self.assertEqual(reports[0].muscle_ids, ())
        self.assertFalse(soreness_reply_needs_clarification(
            "我今天睡落枕了，脖子剧痛无比，完全动不了。",
            ["手臂", "腿部"],
        ))

    def test_deterministic_reply_shapes(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        asked = ["手臂", "腿部"]
        self.assertEqual(
            [(item.region, item.level) for item in parse_soreness_reply("手臂有点酸", asked, now=now)],
            [("手臂", "sore")],
        )
        self.assertEqual(
            {item.region for item in parse_soreness_reply("都正常", asked, now=now)},
            {"手臂", "腿部"},
        )
        self.assertEqual(parse_soreness_reply("有点酸", asked, now=now), [])
        self.assertTrue(soreness_reply_needs_clarification("有点酸", asked))
        self.assertEqual(parse_soreness_reply("聊天继续", asked, now=now), [])
        mixed = parse_soreness_reply("手臂疼，腿正常", asked, now=now)
        self.assertEqual(
            [(item.region, item.level) for item in mixed],
            [("手臂", "painful"), ("腿部", "recovered")],
        )
        recovered = parse_soreness_reply("手臂疼痛已经恢复", asked, now=now)
        self.assertEqual([(item.region, item.level) for item in recovered], [("手臂", "recovered")])
        prompted_recovered = parse_soreness_reply("好了", ["手臂"], now=now)
        self.assertEqual([(item.region, item.level) for item in prompted_recovered], [("手臂", "recovered")])

    def test_inner_thigh_feedback_targets_only_adductors(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        sore = parse_soreness_reply("大腿内侧有点酸", [], now=now)
        recovered = parse_soreness_reply("大腿内侧已恢复", [], now=now)
        self.assertEqual([(item.region, item.level) for item in sore], [("腿部", "sore")])
        self.assertEqual(sore[0].muscle_ids, ("adductors",))
        self.assertEqual([(item.region, item.level) for item in recovered], [("腿部", "recovered")])
        self.assertEqual(recovered[0].muscle_ids, ("adductors",))

    def test_sour_pain_is_sore_but_explicit_pain_is_painful(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        for text in ("手臂酸痛", "手臂酸疼", "手臂有点酸痛"):
            with self.subTest(text=text):
                reports = parse_soreness_reply(text, ["手臂"], now=now)
                self.assertEqual([(item.region, item.level) for item in reports], [("手臂", "sore")])
        for text in ("手臂刺痛", "手臂疼得厉害", "手臂又酸又疼"):
            with self.subTest(text=text):
                reports = parse_soreness_reply(text, ["手臂"], now=now)
                self.assertEqual([(item.region, item.level) for item in reports], [("手臂", "painful")])

    def test_recovery_phrases_do_not_compete_with_old_symptom_words(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        for text in (
            "手臂疼痛已经恢复",
            "手臂疼痛，后来恢复了",
            "手臂没有酸痛了",
            "手臂已经完全恢复，没有疼痛",
            "手臂完全没有任何疼痛",
            "手臂没有一点酸痛",
            "手臂不酸不疼了",
        ):
            with self.subTest(text=text):
                reports = parse_soreness_reply(text, ["手臂"], now=now)
                self.assertEqual(
                    [(item.region, item.level) for item in reports],
                    [("手臂", "recovered")],
                )

    def test_new_symptom_after_recovery_takes_precedence(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        for text, expected in (
            ("手臂恢复了但现在又疼", "painful"),
            ("手臂已经恢复，不过今天还有点酸", "sore"),
            ("手臂还没有恢复", None),
            ("手臂恢复不了", None),
            ("手臂什么时候恢复训练", None),
            ("手臂还在恢复期", None),
            ("看看手臂恢复情况", None),
        ):
            with self.subTest(text=text):
                reports = parse_soreness_reply(text, ["手臂"], now=now)
                actual = reports[0].level if reports else None
                self.assertEqual(actual, expected)

    def test_third_party_soreness_is_not_saved_as_user_feedback(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        self.assertEqual(parse_soreness_reply("我朋友手臂酸痛", ["手臂"], now=now), [])
        reports = parse_soreness_reply("朋友问我手臂酸痛怎么办", ["手臂"], now=now)
        self.assertEqual([(item.region, item.level) for item in reports], [("手臂", "sore")])


class SorenessStoreTest(unittest.TestCase):
    def test_store_accepts_injury_region_without_training_muscle_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            now = datetime(2026, 8, 22, 12, tzinfo=BJ)
            saved = store.add_reports([SorenessReport(
                region="颈部", muscle_ids=(), level="painful",
                reported_at=now, expires_at=now + timedelta(hours=72),
                evidence="脖子剧痛",
            )])[0]
            self.assertEqual(saved.region, "颈部")
            self.assertEqual(saved.muscle_ids, ())

    def test_store_is_durable_visible_editable_and_expiring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "muscle_soreness.json"
            store = SorenessStore(path)
            now = datetime(2026, 8, 22, 12, tzinfo=BJ)
            saved = store.add_reports([SorenessReport(
                region="手臂", muscle_ids=("biceps", "triceps"), level="sore",
                reported_at=now, expires_at=now + timedelta(hours=72), evidence="手臂有点酸",
            )])[0]
            self.assertTrue(path.exists())
            self.assertEqual(SorenessStore(path).list_reports(active_only=True, now=now)[0].id, saved.id)
            updated = store.update_report(saved.id, region="腿部", level="recovered", now=now)
            self.assertEqual(updated.region, "腿部")
            self.assertGreater(len(updated.muscle_ids), 0)
            self.assertEqual(store.cleanup_expired(now=now + timedelta(hours=73)), 1)
            self.assertEqual(len(store.list_reports()), 1)
            self.assertEqual(store.list_reports(active_only=True, now=now + timedelta(hours=73)), [])

    def test_new_report_preserves_superseded_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            now = datetime(2026, 8, 22, 12, tzinfo=BJ)
            first = SorenessReport("手臂", ("biceps",), "painful", now, now + timedelta(hours=72))
            second = SorenessReport("手臂", ("biceps",), "recovered", now + timedelta(hours=1), now + timedelta(hours=73))
            store.add_reports([first])
            store.add_reports([second])
            self.assertEqual(len(store.list_reports()), 2)
            self.assertEqual([item.level for item in store.list_reports(active_only=True, now=now)], ["recovered"])

    def test_targeted_recovery_preserves_other_muscles_in_the_same_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            now = datetime(2026, 8, 22, 12, tzinfo=BJ)
            store.add_reports([SorenessReport(
                "腿部", ("adductors", "calves"), "sore", now, now + timedelta(hours=72)
            )])
            store.add_reports([SorenessReport(
                "腿部", ("adductors",), "recovered",
                now + timedelta(hours=1), now + timedelta(hours=73),
            )])
            active = store.list_reports(active_only=True, now=now + timedelta(hours=1))
            self.assertEqual(
                [(item.level, item.muscle_ids) for item in active],
                [("sore", ("calves",)), ("recovered", ("adductors",))],
            )


class RecoveryPriorityTest(unittest.TestCase):
    def _load(self, *, muscle_id="biceps", region="手臂", hours=12, level="sore"):
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        return MuscleLoad(
            muscle_id=muscle_id, zh="肱二头肌", region=region, last_trained_at=now,
            weekday_zh="周五", exercises=("哑铃弯举",), effective_sets=4,
            recovery_hours=24, recovered_at=now + timedelta(hours=hours), hours_remaining=hours,
            needs_reduction=level in {"sore", "painful"}, soreness_level=level,
        )

    def test_seven_level_context_and_explicit_instruction_override(self) -> None:
        load = self._load()
        snapshot = MuscleRecoverySnapshot(loads=(load,), recovering=(load,))
        context = chat_workflow.resolve_plan_context("今天就要练手臂", {"goal": "增肌"}, [], recovery=snapshot)
        self.assertEqual(context["muscle_recovery"]["reduce"], ["手臂"])
        self.assertEqual(context["muscle_recovery"]["explicitly_requested"], ["手臂"])
        self.assertEqual(context["recovery_conflicts"], ["手臂"])
        rendered = main.format_plan_context(context)
        self.assertIn("3. 肌群恢复状态", rendered)
        self.assertIn("7. 历史摘要", rendered)
        self.assertIn("自由裁量减量", rendered)

    def test_recovery_suppresses_matching_weekly_schedule(self) -> None:
        load = self._load()
        snapshot = MuscleRecoverySnapshot(loads=(load,), recovering=(load,))
        scheduled = (
            datetime(2026, 8, 22, tzinfo=BJ).date(),
            {"type": "training", "subject": "手臂训练"},
        )
        with mock.patch.object(
            chat_workflow, "scheduled_weekly_entry_for_message", return_value=scheduled
        ):
            context = chat_workflow.resolve_plan_context("帮我生成今天的训练计划", {"goal": "增肌"}, [], recovery=snapshot)
        self.assertEqual(context["decision"], "recovery_override")
        self.assertTrue(any(item["rule"] == "周计划：手臂训练" for item in context["suppressed_rules"]))

    def test_painful_region_cannot_be_overridden_by_explicit_instruction(self) -> None:
        load = self._load(level="painful")
        snapshot = MuscleRecoverySnapshot(loads=(load,), recovering=(load,))
        context = chat_workflow.resolve_plan_context("今天就要练手臂", {"goal": "增肌"}, [], recovery=snapshot)
        self.assertTrue(context["blocking_reasons"])
        self.assertIn("手臂疼痛", "；".join(context["active_safety_constraints"]))
        self.assertEqual(context["workflow_state"], "constraint_conflict")

    def test_safety_bypass_questions_and_negations_are_not_blocked(self) -> None:
        memories = [{
            "user_confirmed": True,
            "facts": [{
                "namespace": "health",
                "key": "injury_or_constraint",
                "value": "腰伤",
                "status": "active",
                "user_confirmed": True,
            }],
        }]
        for text in (
            "为什么不能忽略我的腰伤照常练？",
            "腰伤的人可以照常练吗？",
            "伤好了以后能照常训练吗",
            "请解释一下为什么不建议硬练",
            "我不想硬练，帮我安排轻一点的",
        ):
            with self.subTest(text=text):
                context = chat_workflow.resolve_plan_context(text, {"goal": "增肌"}, memories)
                self.assertEqual(context["blocking_reasons"], [])

        context = chat_workflow.resolve_plan_context(
            "忽略我的腰伤，照常给我安排腿部训练", {"goal": "增肌"}, memories
        )
        self.assertTrue(context["blocking_reasons"])


class ChatPayloadGarminTest(unittest.TestCase):
    def test_chat_payload_accepts_session_garmin_hours(self) -> None:
        payload = validate_chat_payload({"message": "你好", "garmin_recovery_hours": 8})
        self.assertEqual(payload["garmin_recovery_hours"], 8.0)
        self.assertEqual(
            validate_chat_payload({"message": "你好", "garmin_recovery_hours": 0.5})[
                "garmin_recovery_hours"
            ],
            0.5,
        )
        self.assertEqual(
            validate_chat_payload({"message": "你好", "garmin_recovery_hours": 96.0})[
                "garmin_recovery_hours"
            ],
            96.0,
        )
        with self.assertRaises(ContextInputError):
            validate_chat_payload({"message": "你好", "garmin_recovery_hours": 96.1})


class SorenessEndpointTest(unittest.TestCase):
    def test_inner_thigh_recovery_replaces_welcome_page_soreness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            now = datetime.now(BJ)
            store.add_reports([SorenessReport(
                region="腿部", muscle_ids=("adductors",), level="sore",
                reported_at=now, expires_at=now + timedelta(hours=72),
                evidence="大腿内侧有点酸",
            )])
            with (
                mock.patch.object(deps, "soreness_store", store),
                mock.patch.object(deps, "classify_user_health_statement", return_value=True),
            ):
                client = TestClient(main.app)
                response = client.post("/chat", json={
                    "message": "大腿内侧已恢复",
                    "history": [],
                    "source": "chat",
                    "soreness_prompt_regions": [],
                })
                intro = client.get("/session/intro").json()

            self.assertEqual(response.status_code, 200, response.json())
            self.assertEqual(response.json()["source"], "local_soreness_feedback")
            self.assertEqual(response.json()["soreness_reports"][0]["level"], "recovered")
            active = intro["active_soreness_reports"]
            self.assertEqual([(item["level"], item["muscle_ids"]) for item in active], [
                ("recovered", ["adductors"]),
            ])

    def test_unprompted_neck_injury_is_saved_and_not_misrouted_to_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            fake_info = mock.Mock()
            fake_info.get_context_memories.return_value = []
            with (
                mock.patch.object(deps, "soreness_store", store),
                mock.patch.object(deps, "info_store", fake_info),
                mock.patch.object(deps, "classify_user_health_statement", return_value=True),
                mock.patch.object(chat_workflow, "_acute_injury_memory_candidate", return_value=None),
                mock.patch.object(chat_workflow, "_current_recovery_snapshot", return_value=MuscleRecoverySnapshot()),
            ):
                response = TestClient(main.app).post("/chat", json={
                    "message": "我今天睡落枕了，脖子剧痛无比，完全动不了。",
                    "history": [],
                    "source": "chat",
                    "soreness_prompt_regions": ["手臂", "腿部"],
                })
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["source"], "local_soreness_feedback")
            self.assertEqual(payload["soreness_reports"][0]["region"], "颈部")
            self.assertEqual(payload["soreness_reports"][0]["level"], "painful")
            self.assertEqual(store.list_reports(active_only=True)[0].muscle_ids, ())

    def test_session_intro_reprompts_non_training_injury_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            now = datetime.now(BJ)
            store.add_reports([SorenessReport(
                region="颈部", muscle_ids=(), level="painful",
                reported_at=now, expires_at=now + timedelta(hours=72), evidence="脖子剧痛",
            )])
            with (
                mock.patch.object(deps, "soreness_store", store),
                mock.patch.object(chat_workflow, "_current_recovery_snapshot", return_value=MuscleRecoverySnapshot()),
            ):
                response = TestClient(main.app).get("/session/intro")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn("颈部", payload["soreness_prompt_regions"])
            self.assertIn("颈部此前报告过疼痛", payload["message"])

    def test_manual_soreness_create_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            with mock.patch.object(deps, "soreness_store", store):
                response = TestClient(main.app).post("/data/soreness", json={
                    "region": "手臂", "level": "sore", "evidence": "手动录入",
                })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["report"]["region"], "手臂")
            self.assertEqual(store.list_reports(active_only=True)[0].level, "sore")

    def test_soreness_feedback_does_not_swallow_weekly_summary_request(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        initial = MuscleLoad(
            muscle_id="biceps", zh="肱二头肌", region="手臂", last_trained_at=now,
            weekday_zh="周五", exercises=("哑铃弯举",), effective_sets=4,
            recovery_hours=24, recovered_at=now + timedelta(hours=12), hours_remaining=12,
        )
        sore = MuscleLoad(**{
            **initial.__dict__, "needs_reduction": True, "soreness_level": "sore"
        })
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            with (
                mock.patch.object(deps, "soreness_store", store),
                mock.patch.object(chat_workflow, "_current_recovery_snapshot", side_effect=[
                    MuscleRecoverySnapshot(loads=(initial,), recovering=(initial,)),
                    MuscleRecoverySnapshot(loads=(sore,), recovering=(sore,)),
                ]),
                mock.patch.object(deps, "route_chat_intent", return_value=ChatIntent()),
                mock.patch.object(deps, "build_current_week_reply", return_value="本周恢复汇总内容"),
                mock.patch.object(deps.profile_store, "get_profile", return_value={
                    "weekly_weight_kg": [70], "height_cm": 175, "birth_date": "1990-01-01",
                    "sex": "male", "goal": "增肌", "equipment": ["哑铃"],
                }),
                mock.patch.object(deps.profile_store, "is_complete", return_value=True),
            ):
                response = TestClient(main.app).post("/chat", json={
                    "message": "本周恢复情况怎么样？顺便问下，手臂酸，怎么办？",
                    "history": [], "source": "chat", "soreness_prompt_regions": ["手臂"],
                })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "local_weekly_summary")
        self.assertIn("已记录：手臂酸痛", payload["reply"])
        self.assertIn("本周恢复汇总内容", payload["reply"])
        self.assertEqual(payload["soreness_reports"][0]["level"], "sore")

    def test_safety_conflict_returns_409_only_for_plan_creation(self) -> None:
        memories = [{
            "user_confirmed": True,
            "facts": [{
                "namespace": "health", "key": "injury_or_constraint",
                "value": "腰伤", "status": "active", "user_confirmed": True,
            }],
        }]
        fake_info_store = mock.Mock()
        fake_info_store.get_context_memories.return_value = memories
        fake_info_store.get_all.return_value = memories
        fake_agent = mock.Mock()
        fake_agent.run.return_value = "可以讨论这条限制的原因。"
        message = "请讨论‘忽略我的腰伤照常练’这句话"
        with (
            mock.patch.object(deps, "info_store", fake_info_store),
            mock.patch.object(chat_workflow, "_current_recovery_snapshot", return_value=MuscleRecoverySnapshot()),
            mock.patch.object(chat_workflow, "_immediate_memory_candidate", return_value=None),
            mock.patch.object(deps.external_model_settings_store, "get", return_value={"external_models_enabled": True}),
            mock.patch.object(deps.profile_store, "get_profile", return_value={
                "weekly_weight_kg": [70], "height_cm": 175, "birth_date": "1990-01-01",
                "sex": "male", "goal": "增肌", "equipment": ["哑铃", "哑铃凳"],
            }),
            mock.patch.object(deps.profile_store, "missing_fields", return_value=[]),
            mock.patch.object(deps, "create_fithealth_agent", return_value=fake_agent),
            mock.patch.object(
                deps,
                "route_chat_intent",
                side_effect=[ChatIntent(), ChatIntent(
                    create_training_plan=True,
                    training_plan_subject="腿部训练",
                    training_plan_title="腿部训练计划",
                )],
            ),
        ):
            client = TestClient(main.app)
            discussion = client.post("/chat", json={"message": message, "history": [], "source": "chat"})
            plan = client.post("/chat", json={"message": message, "history": [], "source": "chat"})

        self.assertEqual(discussion.status_code, 200)
        self.assertNotEqual(discussion.json().get("source"), "plan_context_safety_block")
        self.assertEqual(plan.status_code, 409, plan.json())
        self.assertEqual(plan.json()["source"], "plan_context_safety_block")

    def test_chest_pain_stays_emergency_when_chest_is_in_recovery_snapshot(self) -> None:
        now = datetime(2026, 8, 22, 12, tzinfo=BJ)
        chest = MuscleLoad(
            muscle_id="pectoralis", zh="胸大肌", region="胸部", last_trained_at=now,
            weekday_zh="周五", exercises=("卧推",), effective_sets=4,
            recovery_hours=48, recovered_at=now + timedelta(hours=12),
            hours_remaining=12, needs_reduction=False,
        )
        snapshot = MuscleRecoverySnapshot(loads=(chest,), recovering=(chest,))
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            with (
                mock.patch.object(deps, "soreness_store", store),
                mock.patch.object(chat_workflow, "_current_recovery_snapshot", return_value=snapshot),
            ):
                response = TestClient(main.app).post("/chat", json={
                    "message": "胸口痛",
                    "history": [],
                    "source": "chat",
                    "garmin_recovery_hours": 0,
                })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "health_risk_block")
        self.assertEqual(payload["health_risk"]["level"], "emergency")

    def test_emergency_keeps_soreness_saved_without_diluting_the_reply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SorenessStore(Path(directory) / "muscle_soreness.json")
            with (
                mock.patch.object(deps, "soreness_store", store),
                mock.patch.object(deps, "classify_user_health_statement", return_value=True),
                mock.patch.object(
                    chat_workflow,
                    "_current_recovery_snapshot",
                    return_value=MuscleRecoverySnapshot(),
                ),
            ):
                response = TestClient(main.app).post("/chat", json={
                    "message": "我胸口痛，而且腿部非常酸",
                    "history": [],
                    "source": "chat",
                })

        payload = response.json()
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["source"], "health_risk_block")
        self.assertTrue(payload["soreness_saved"])
        self.assertIn("腿部", {item["region"] for item in payload["soreness_reports"]})
        self.assertNotIn("已记录", payload["reply"])

    def test_chat_writes_local_soreness_and_management_can_edit_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original_store = deps.soreness_store
            deps.soreness_store = SorenessStore(Path(directory) / "muscle_soreness.json")
            now = datetime(2026, 8, 22, 12, tzinfo=BJ)
            initial = MuscleLoad(
                muscle_id="biceps", zh="肱二头肌", region="手臂", last_trained_at=now,
                weekday_zh="周五", exercises=("哑铃弯举",), effective_sets=4,
                recovery_hours=24, recovered_at=now + timedelta(hours=12), hours_remaining=12,
            )
            sore = MuscleLoad(
                muscle_id="biceps", zh="肱二头肌", region="手臂", last_trained_at=now,
                weekday_zh="周五", exercises=("哑铃弯举",), effective_sets=4,
                recovery_hours=24, recovered_at=now + timedelta(hours=12), hours_remaining=12,
                needs_reduction=True, soreness_level="sore",
            )
            try:
                with mock.patch.object(
                    chat_workflow,
                    "_current_recovery_snapshot",
                    side_effect=[
                        MuscleRecoverySnapshot(loads=(initial,), recovering=(initial,)),
                        MuscleRecoverySnapshot(loads=(sore,), recovering=(sore,)),
                    ],
                ):
                    client = TestClient(main.app)
                    response = client.post("/chat", json={
                        "message": "手臂有点酸",
                        "history": [],
                        "source": "chat",
                        "garmin_recovery_hours": 0,
                        "soreness_prompt_regions": ["手臂"],
                    })
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["source"], "local_soreness_feedback")
                self.assertIn("已记录", payload["reply"])
                self.assertEqual(payload["plan_context"]["muscle_recovery"]["reduce"], ["手臂"])
                report_id = payload["soreness_reports"][0]["id"]

                overview = client.get("/data/overview").json()
                self.assertEqual(overview["soreness_reports"][0]["id"], report_id)
                edited = client.patch(f"/data/soreness/{report_id}", json={
                    "region": "腿部", "level": "recovered", "evidence": "腿都正常",
                })
                self.assertEqual(edited.status_code, 200)
                self.assertEqual(edited.json()["report"]["region"], "腿部")
                removed = client.delete(f"/data/soreness/{report_id}")
                self.assertEqual(removed.status_code, 200)
                self.assertEqual(deps.soreness_store.list_reports(), [])
            finally:
                deps.soreness_store = original_store


if __name__ == "__main__":
    unittest.main()
