from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.module_map import consumer_home
from tests.source_tools import module_source


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


class AgentTrainingEditToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = importlib.import_module("fithealth_agent.fit_tools")
        cls.agent_source = (PACKAGE_DIR / "agent.py").read_text(encoding="utf-8")
        cls.prompt_source = (PACKAGE_DIR / "prompts.py").read_text(encoding="utf-8")
        cls.main_source = module_source(consumer_home("workout_state_routes"))

    def test_delete_tool_calls_store(self) -> None:
        with patch.object(self.tools.workout_store, "delete_set", return_value={"deleted": True}) as delete:
            response = self.tools.DeleteSetTool().run({"index": 2})
        delete.assert_called_once_with(2)
        self.assertEqual(response.data, {"deleted": True})

    def test_recovery_tools_call_store(self) -> None:
        with patch.object(self.tools.workout_store, "undo_last_edit", return_value={"undone": True}) as undo:
            response = self.tools.UndoLastEditTool().run({})
        undo.assert_called_once_with()
        self.assertEqual(response.data, {"undone": True})

        with patch.object(self.tools.workout_store, "restore_parsed_source", return_value={"restored": True}) as restore:
            response = self.tools.RestoreParsedSourceTool().run({})
        restore.assert_called_once_with()
        self.assertEqual(response.data, {"restored": True})

    def test_agent_registration_and_prompt_rules_stay_aligned(self) -> None:
        for name in ("DeleteSetTool", "UndoLastEditTool", "RestoreParsedSourceTool"):
            self.assertIn(name, self.agent_source)
        for name in ("delete_set", "undo_last_edit", "restore_parsed_source"):
            self.assertIn(f"`{name}`", self.prompt_source)

    def test_recovery_actions_are_exposed_by_the_pending_workout_endpoint(self) -> None:
        self.assertIn('action == "undo_last_edit"', self.main_source)
        self.assertIn('action == "restore_parsed_source"', self.main_source)


if __name__ == "__main__":
    unittest.main()
