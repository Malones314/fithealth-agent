from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fithealth_agent import plan_store


class TrainingPlanStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = plan_store.TrainingPlanStore(Path(self.temp_dir.name) / "plans.json")

    def test_deduplicates_content_and_numbers_filename_conflicts(self) -> None:
        first = self.store.add(
            date="2026-08-13",
            subject="背部训练",
            title="计划一",
            content="# 背部训练\n\n哑铃划船 4 组。",
            source="uploaded_optimized",
        )
        duplicate = self.store.add(
            date="2026-08-14",
            subject="其他训练",
            title="重复内容",
            content="# 背部训练\r\n\r\n哑铃划船 4 组。",
            source="uploaded_optimized",
        )
        second = self.store.add(
            date="2026-08-13",
            subject="背部训练",
            title="计划二",
            content="# 背部训练\n\n单臂划船 3 组。",
            source="agent_generated",
        )

        self.assertFalse(first["duplicate"])
        self.assertNotIn("saved", first)
        self.assertFalse(duplicate["duplicate"])
        self.assertNotEqual(duplicate["id"], first["id"])
        self.assertEqual(first["filename"], "26-08-13-背部训练.md")
        self.assertEqual(second["filename"], "26-08-13-背部训练-2.md")

    def test_same_date_duplicate_updates_nonempty_memo(self) -> None:
        first = self.store.add(
            date="2026-08-13", subject="背部训练", title="计划一",
            content="# 背部训练\n划船 4 组", source="agent_generated", memo="",
        )
        duplicate = self.store.add(
            date="2026-08-13", subject="背部训练", title="另一个标题",
            content="# 背部训练\r\n划船 4 组", source="agent_generated", memo="下次增加重量",
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertTrue(duplicate["memo_updated"])
        self.assertEqual(duplicate["id"], first["id"])
        self.assertEqual(self.store.get(first["id"])["memo"], "下次增加重量")

    def test_updates_metadata_and_supports_batch_delete(self) -> None:
        first = self.store.add(
            date="2026-08-13", subject="腿部", title="腿日", content="计划 A", source="agent_generated"
        )
        second = self.store.add(
            date="2026-08-13", subject="肩部", title="肩日", content="计划 B", source="agent_generated"
        )
        updated = self.store.update(
            first["id"], date="2026-08-14", subject="腿部+核心", title="腿部核心日"
        )
        self.assertEqual(updated["filename"], "26-08-14-腿部+核心训练.md")
        self.assertEqual(updated["title"], "腿部核心日")
        self.assertEqual(self.store.delete_many([first["id"], second["id"]]), 2)
        self.assertEqual(self.store.list_plans(), [])

        self.store.add(
            date="2026-08-15", subject="核心", title="核心日", content="计划 C", source="agent_generated"
        )
        self.assertEqual(self.store.clear(), 1)

    def test_persists_plan_memo(self) -> None:
        plan = self.store.add(
            date="2026-08-16", subject="背部", title="背部计划", content="# 背部",
            source="agent_generated", memo="喜欢 Jeff Nippard 的动作教学：https://youtu.be/example",
        )
        self.assertIn("Jeff Nippard", plan["memo"])
        updated = self.store.update(
            plan["id"], date="2026-08-16", subject="背部", title="背部计划",
            memo="划船安排很有效，近期继续保留",
        )
        self.assertEqual(updated["memo"], "划船安排很有效，近期继续保留")

    def test_rejects_invalid_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "日期"):
            self.store.add(date="26-08-13", subject="背部", title="计划", content="x", source="agent_generated")
        with self.assertRaisesRegex(ValueError, "来源"):
            self.store.add(date="2026-08-13", subject="背部", title="计划", content="x", source="uploaded_original")


    def test_accepts_auto_corrected_source(self) -> None:
        plan = self.store.add(
            date="2026-08-28",
            subject="auto",
            title="auto corrected plan",
            content="# cardio\nrun 3 sets",
            source="agent_auto_corrected",
        )
        self.assertEqual(plan["source"], "agent_auto_corrected")


if __name__ == "__main__":
    unittest.main()
