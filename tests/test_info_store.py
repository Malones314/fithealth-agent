from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fithealth_agent import info_store


class InfoStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = info_store

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "info_store.json"
        self.store = self.module.InfoStore(self.path)
        self.expires = datetime.now(timezone.utc) + timedelta(days=1)

    def test_pending_memories_are_not_injected_until_confirmed(self) -> None:
        pending = self.store.add_entry(
            "avoid heavy squats while knee recovers", {}, self.expires,
            memory_type="constraint", importance=5,
        )
        self.assertEqual(self.store.get_context_memories(), [])
        with self.assertRaisesRegex(ValueError, "没有可确认的事实"):
            self.store.confirm_entry(pending["id"])
        self.assertEqual(self.store.get_context_memories(), [])

    def test_summary_only_memory_is_injected_once_confirmed(self) -> None:
        """BUG-13：以前 get_context_memories 硬性要求 facts 非空，
        导致只有自然语言摘要、没有结构化事实的记忆永远进不了上下文。"""
        entry = self.store.add_entry(
            "膝盖恢复期避免大重量深蹲", {}, self.expires,
            memory_type="constraint", importance=5, user_confirmed=True,
        )
        self.assertEqual(self.store.get_context_memories(), [])

    def test_unconfirmed_summary_only_memory_stays_out(self) -> None:
        self.store.add_entry("待确认的摘要", {}, self.expires, user_confirmed=False)
        self.assertEqual(self.store.get_context_memories(), [])

    def test_entry_with_only_rejected_facts_is_not_injected(self) -> None:
        """安全边界：不能因为条目级已确认，就把用户明确拒绝掉的事实
        所对应的摘要原文重新灌回上下文。"""
        entry = self.store.add_entry(
            "我不喜欢深蹲", {}, self.expires, user_confirmed=True,
            facts=[{"namespace": "training", "key": "prefer_exercise", "value": "深蹲", "status": "active"}],
        )
        self.assertIsNotNone(self.store.reject_fact(entry["id"], 0))
        self.assertEqual(self.store.get_context_memories(), [])

    def test_partially_confirmed_entry_carries_only_confirmed_facts(self) -> None:
        entry = self.store.add_entry(
            "两条事实", {}, self.expires, user_confirmed=False,
            facts=[
                {"namespace": "plan", "key": "preference", "value": "A", "status": "active"},
                {"namespace": "plan", "key": "preference", "value": "B", "status": "active"},
            ],
        )
        self.assertIsNotNone(self.store.set_fact_confirmation(entry["id"], 0, True))
        context = self.store.get_context_memories()
        self.assertEqual([fact["value"] for fact in context[0]["facts"]], ["A"])

    def test_context_memories_are_ranked_by_importance(self) -> None:
        self.store.add_entry(
            "prefers dumbbells", {}, self.expires,
            memory_type="preference", importance=2, user_confirmed=True,
        )
        self.store.add_entry(
            "agreed to reduce leg volume", {}, self.expires,
            memory_type="plan_decision", importance=4, user_confirmed=True,
        )
        self.assertEqual(self.store.get_context_memories(), [])

    def test_legacy_entries_migrate_to_confirmed_defaults(self) -> None:
        legacy = [{
            "id": "legacy", "summary": "old summary", "metadata": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": self.expires.isoformat(),
        }]
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        entry = self.store.get_all()[0]
        self.assertEqual(entry["type"], "training_feedback")
        self.assertEqual(entry["importance"], 1)
        self.assertTrue(entry["user_confirmed"])
        self.assertEqual(entry["facts"], [])

    def test_fact_level_confirmation_edit_and_rejection(self) -> None:
        pending = self.store.add_entry(
            "多事实", {}, self.expires, user_confirmed=False,
            facts=[
                {"namespace": "plan", "key": "preference", "value": "胸部低强度", "status": "active"},
                {"namespace": "health", "key": "injury_or_constraint", "value": "久坐", "status": "active"},
                {"namespace": "training", "key": "prefer_exercise", "value": "错误动作", "status": "active"},
            ],
        )
        self.assertEqual(self.module.resolve_confirmed_memory_facts(self.store.get_all()), [])
        self.assertIsNotNone(self.store.set_fact_confirmation(pending["id"], 0, True))
        facts = self.module.resolve_confirmed_memory_facts(self.store.get_all())
        self.assertEqual([fact["value"] for fact in facts], ["胸部低强度"])
        self.assertIsNotNone(self.store.edit_fact(pending["id"], 1, "长期久坐"))
        self.assertIsNotNone(self.store.set_fact_confirmation(pending["id"], 1, True))
        self.assertIsNotNone(self.store.reject_fact(pending["id"], 2))
        values = {fact["value"] for fact in self.module.resolve_confirmed_memory_facts(self.store.get_all())}
        self.assertEqual(values, {"胸部低强度", "长期久坐"})
    def test_resolves_confirmed_facts_and_newer_clear_removes_old_fact(self) -> None:
        self.store.add_entry(
            "不推荐 Jeff Nippard", {}, self.expires, user_confirmed=True,
            facts=[{"namespace": "youtube", "key": "avoid_channel", "value": "Jeff Nippard", "status": "active"}],
        )
        self.store.add_entry(
            "可以再次推荐 Jeff Nippard", {}, self.expires, user_confirmed=True,
            facts=[{"namespace": "youtube", "key": "avoid_channel", "value": "Jeff Nippard", "status": "cleared"}],
        )
        self.assertEqual(self.module.resolve_confirmed_memory_facts(self.store.get_all()), [])

    def test_invalid_or_unconfirmed_facts_do_not_resolve(self) -> None:
        pending = self.store.add_entry(
            "膝盖不适", {}, self.expires,
            facts=[{"namespace": "health", "key": "injury_or_constraint", "value": "膝盖不适", "status": "active"}],
        )
        self.assertEqual(self.module.resolve_confirmed_memory_facts(self.store.get_all()), [])
        self.assertIsNotNone(self.store.confirm_entry(pending["id"]))
        facts = self.module.resolve_confirmed_memory_facts(self.store.get_all())
        self.assertEqual(facts[0]["key"], "injury_or_constraint")
        self.assertEqual(
            self.module.normalize_memory_facts([{"namespace": "unknown", "key": "anything", "value": "x"}]),
            [],
        )

    def test_weekly_schedule_is_validated_and_newer_schedule_replaces_old_one(self) -> None:
        self.store.add_entry(
            "旧周计划", {}, self.expires, user_confirmed=True,
            facts=[{"namespace": "plan", "key": "weekly_schedule", "value": '{"tue":"胸部训练"}', "status": "active"}],
        )
        self.store.add_entry(
            "新周计划", {}, self.expires, user_confirmed=True,
            facts=[{"namespace": "plan", "key": "weekly_schedule", "value": '{"mon":"胸部训练","tue":"背部训练"}', "status": "active"}],
        )
        facts = self.module.resolve_confirmed_memory_facts(self.store.get_all())
        self.assertEqual(len(facts), 1)
        self.assertIn('"tue":{"subject":"背部训练","type":"training"}', facts[0]["value"])
        self.assertEqual(self.module.normalize_memory_facts([{
            "namespace": "plan", "key": "weekly_schedule", "value": '{"tuesday":"背部训练"}', "status": "active",
        }]), [])


if __name__ == "__main__":
    unittest.main()
