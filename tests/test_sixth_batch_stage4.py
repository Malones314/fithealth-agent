from __future__ import annotations

import ast
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main
from fithealth_agent.context_budget import ContextInputError, validate_chat_payload
from fithealth_agent.info_store import InfoStore
from fithealth_agent.muscle_map import (
    MUSCLE_RULES,
    REGION_ALIASES,
    REGION_LEXICON,
    REGIONS,
    regions_for_text,
)
from fithealth_agent.muscle_recovery import (
    parse_soreness_reply,
    normalise_garmin_hours,
)
from fithealth_agent.plan_store import TrainingPlanStore
from fithealth_agent.settings import data_dir
from tests.module_map import consumer_home, function_home
from tests.source_tools import module_source, module_tree
from fithealth_agent.soreness_store import SorenessStore


ROOT = Path(__file__).resolve().parents[1]


def _imported_from_muscle_map(path) -> set[str]:
    """模块从 muscle_map 里 import 了哪些名字。"""
    return {
        alias.name
        for node in ast.walk(module_tree(path))
        if isinstance(node, ast.ImportFrom)
        and node.module in {"fithealth_agent.muscle_map", "muscle_map"}
        for alias in node.names
    }


class RegionLexiconTest(unittest.TestCase):
    def test_every_muscle_region_is_represented_and_lexicon_regions_are_valid(self) -> None:
        represented = {
            region for mappings in REGION_LEXICON.values() for region, _role in mappings
        }
        self.assertTrue({rule.region for rule in MUSCLE_RULES} <= represented)
        self.assertTrue(represented <= REGIONS)

    def test_waist_soreness_records_core_only(self) -> None:
        reports = parse_soreness_reply(
            "腰还酸", [], now=datetime(2026, 8, 26, tzinfo=timezone.utc)
        )
        self.assertEqual([(item.region, item.level) for item in reports], [("核心", "sore")])

    def test_waist_constraint_includes_core_and_back(self) -> None:
        self.assertEqual(
            regions_for_text("腰伤，别安排大重量", include_secondary=True),
            {"核心", "背部"},
        )
        self.assertEqual(main.constraint_regions("腰伤，别安排大重量"), {"核心", "背部"})

    def test_region_aliases_are_primary_only(self) -> None:
        self.assertIn("腰", REGION_ALIASES["核心"])
        self.assertNotIn("腰", REGION_ALIASES["背部"])

    def test_main_and_recovery_import_the_shared_lexicon(self) -> None:
        for path in (
            function_home("constraint_regions"),
            ROOT / "fithealth_agent/muscle_recovery.py",
        ):
            self.assertIn("REGION_LEXICON", _imported_from_muscle_map(path), path.name)


class GarminNormalizationTest(unittest.TestCase):
    def test_shared_normalizer_has_one_contract(self) -> None:
        for value, expected in ((None, 0.0), ("", 0.0), (12.54, 12.5), (96, 96.0)):
            with self.subTest(value=value):
                self.assertEqual(normalise_garmin_hours(value), expected)
        for value in (True, "bad", -0.1, 96.1, float("nan")):
            with self.subTest(value=value):
                self.assertIsNone(normalise_garmin_hours(value))

    def test_each_entrypoint_translates_none_to_its_error_contract(self) -> None:
        with self.assertRaises(ContextInputError):
            validate_chat_payload({"message": "你好", "garmin_recovery_hours": "bad"})
        with self.assertRaises(ValueError):
            main.parse_garmin_recovery_hours("bad")

    def test_document_says_recovered_muscles_are_not_reactivated(self) -> None:
        document = (ROOT / "肌群恢复实现计划.md").read_text(encoding="utf-8")
        self.assertIn("不能把已经", document)
        self.assertIn("完成恢复的肌群重新激活", document)
        self.assertIn("基础上已经恢复的肌群保持 0", document)


class JsonStoreRLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def _run_without_deadlock(self, action) -> None:
        errors: list[BaseException] = []
        worker = threading.Thread(target=lambda: self._capture(action, errors), daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "公开方法发生文件锁嵌套死锁")
        self.assertEqual(errors, [])

    @staticmethod
    def _capture(action, errors: list[BaseException]) -> None:
        try:
            action()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def test_public_read_write_sequences_do_not_deadlock(self) -> None:
        plans = TrainingPlanStore(self.root / "plans.json")
        soreness = SorenessStore(self.root / "soreness.json")
        memories = InfoStore(self.root / "memories.json")

        def exercise() -> None:
            plan = plans.add(
                date="2026-08-26", subject="胸部", title="胸部训练",
                content="卧推 4 组", source="agent_generated",
            )
            plans.get(plan["id"])
            plans.list_plans()
            memories.add_entry(
                "测试", {}, datetime.now(timezone.utc) + timedelta(days=1)
            )
            memories.storage_status()
            memories.get_all()
            soreness.list_reports()
            soreness.cleanup_expired()

        self._run_without_deadlock(exercise)

    def test_concurrent_reads_and_writes_leave_valid_json(self) -> None:
        store = TrainingPlanStore(self.root / "plans-concurrent.json")
        errors: list[BaseException] = []

        def write(index: int) -> None:
            self._capture(lambda: store.add(
                date="2026-08-26", subject=f"科目{index}", title=f"计划{index}",
                content=f"动作 {index}", source="agent_generated",
            ), errors)

        workers = [threading.Thread(target=write, args=(index,)) for index in range(12)]
        workers += [threading.Thread(target=lambda: self._capture(store.list_plans, errors)) for _ in range(12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(json.loads(store.path.read_text(encoding="utf-8"))), 12)


class CopyAndPathTest(unittest.TestCase):
    def test_intro_explains_effect_and_correction(self) -> None:
        source = module_source(consumer_home("session_intro_copy"))
        self.assertNotIn("本阶段先展示状态，不改变训练计划", source)
        self.assertIn("这些恢复状态会参与训练裁决", source)
        self.assertIn("数据管理 → 肌群酸痛记录", source)

    def test_default_data_dir_is_stable_across_cwd(self) -> None:
        previous = os.environ.pop("FITHEALTH_DATA_DIR", None)
        original_cwd = Path.cwd()
        try:
            expected = ROOT / "data"
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
                os.chdir(directory)
                self.assertEqual(data_dir(), expected)
        finally:
            os.chdir(original_cwd)
            if previous is not None:
                os.environ["FITHEALTH_DATA_DIR"] = previous


if __name__ == "__main__":
    unittest.main()
