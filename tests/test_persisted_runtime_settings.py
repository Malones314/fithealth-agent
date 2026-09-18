from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fithealth_agent.external_model_settings import ExternalModelSettingsStore


class PersistedRuntimeSettingsTest(unittest.TestCase):
    def test_old_privacy_file_gets_environment_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = ExternalModelSettingsStore(path)
            with mock.patch.dict(
                "os.environ",
                {"FITHEALTH_AGENT_MAX_STEPS": "21", "LLM_TIMEOUT": "135"},
                clear=True,
            ):
                values = store.runtime_settings()
            self.assertEqual(values["agent_max_steps"], 21)
            self.assertEqual(values["llm_timeout_seconds"], 135)
            self.assertEqual(values["chat_timeout_seconds"], 600)

    def test_all_runtime_settings_are_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = ExternalModelSettingsStore(path)
            expected = {
                "agent_max_steps": 20,
                "llm_temperature": 0.3,
                "llm_max_tokens": 4096,
                "llm_timeout_seconds": 120,
                "llm_max_retries": 2,
                "chat_timeout_seconds": 900,
            }
            self.assertEqual(store.set_runtime_settings(expected), expected)
            self.assertEqual(ExternalModelSettingsStore(path).runtime_settings(), expected)
            self.assertEqual(store.agent_runtime_settings().max_steps, 20)

    def test_invalid_chat_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ExternalModelSettingsStore(Path(directory) / "settings.json")
            with self.assertRaisesRegex(ValueError, "30..3600"):
                store.set_runtime_settings(
                    {
                        "agent_max_steps": 15,
                        "llm_temperature": 0.7,
                        "llm_max_tokens": None,
                        "llm_timeout_seconds": 90,
                        "llm_max_retries": 0,
                        "chat_timeout_seconds": 10,
                    }
                )


if __name__ == "__main__":
    unittest.main()
