from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import main
from fithealth_agent import information_router
from fithealth_agent.info_store import InfoStore
from fithealth_agent.workflows import chat_workflow


class SensitiveRoutingTest(unittest.TestCase):
    def test_assistant_medical_word_does_not_block_user_facts(self) -> None:
        messages = [
            {"role": "assistant", "text": "你有相关病史吗？"},
            {"role": "user", "text": "周一练胸；膝盖受伤时不要安排深蹲。"},
        ]
        decision = {
            "save": True, "reason": "explicit", "summary": "用户的周计划和伤病限制",
            "type": "constraint", "importance": 5,
            "facts": [
                {"namespace": "plan", "key": "weekly_schedule", "value": '{"mon":"胸部训练"}', "status": "active", "evidence": "周一练胸"},
                {"namespace": "training", "key": "avoid_exercise", "value": "深蹲", "status": "active", "evidence": "不要安排深蹲"},
            ],
        }
        with patch.object(information_router, "_level3_llm_decide", return_value=decision) as routed:
            result = information_router.route_information(messages)
        routed.assert_called_once()
        self.assertTrue(result["save"])

    def test_real_user_phone_number_is_still_blocked(self) -> None:
        result = information_router.route_information([
            {"role": "user", "text": "我的手机号是 13812345678，以后周一练胸。"},
        ])
        self.assertFalse(result["save"])
        self.assertEqual(result["pipeline_stage"], "level1")


class RejectedFactSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "info_store.json"
        self.store = InfoStore(self.path)
        self.expires = datetime.now(timezone.utc) + timedelta(days=30)

    def test_rejected_fact_suppresses_summary_but_keeps_confirmed_fact_lines(self) -> None:
        entry = self.store.add_entry(
            "摘要含硬拉、卧推和深蹲", {}, self.expires, user_confirmed=True,
            facts=[
                {"namespace": "training", "key": "avoid_exercise", "value": "硬拉", "status": "active"},
                {"namespace": "training", "key": "prefer_exercise", "value": "卧推", "status": "active"},
                {"namespace": "training", "key": "prefer_exercise", "value": "深蹲", "status": "active"},
            ],
        )
        self.store.reject_fact(entry["id"], fact_id=entry["facts"][0]["fact_id"])
        output = chat_workflow.format_cross_session_memories(self.store.get_context_memories())
        self.assertNotIn("摘要含", output)
        self.assertIn("卧推", output)
        self.assertIn("深蹲", output)

    def test_rejected_tombstone_survives_four_days(self) -> None:
        entry = self.store.add_entry(
            "拒绝硬拉", {}, self.expires,
            facts=[{"namespace": "training", "key": "avoid_exercise", "value": "硬拉", "status": "active"}],
        )
        self.store.reject_fact(entry["id"], fact_id=entry["facts"][0]["fact_id"])
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw[0]["facts"][0]["rejected_at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        raw[0]["facts"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.store.cleanup_expired()
        self.assertEqual(self.store.get_all()[0]["facts"][0]["status"], "rejected")


class ConfirmationVersionTest(unittest.TestCase):
    def test_entry_confirmation_updates_base_for_later_reconfirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = InfoStore(Path(directory) / "info_store.json")
            entry = store.add_entry(
                "每周计划", {}, datetime.now(timezone.utc) + timedelta(days=30),
                facts=[{"namespace": "plan", "key": "weekly_schedule", "value": '{"mon":"胸部训练"}', "status": "active"}],
            )
            store.confirm_entry(entry["id"])
            current = store.get_all()[0]["facts"][0]
            store.set_fact_confirmation(entry["id"], fact_id=current["fact_id"], confirmed=False)
            result = store.set_fact_confirmation(entry["id"], fact_id=current["fact_id"], confirmed=True)
            self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
