from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fithealth_agent import external_model_settings


class ExternalModelSettingsStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = external_model_settings

    def test_defaults_to_enabled_and_persists_opt_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "settings.json"
            store = self.module.ExternalModelSettingsStore(path)
            self.assertTrue(store.get()["external_models_enabled"])
            self.assertFalse(store.set_external_models_enabled(False)["external_models_enabled"])
            self.assertFalse(self.module.ExternalModelSettingsStore(path).get()["external_models_enabled"])

    def test_rejects_non_boolean_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = self.module.ExternalModelSettingsStore(
                Path(temporary_directory) / "settings.json"
            )
            with self.assertRaises(ValueError):
                store.set_external_models_enabled("false")


if __name__ == "__main__":
    unittest.main()
