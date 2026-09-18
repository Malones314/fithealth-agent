from __future__ import annotations

import importlib.util
import unittest
from functools import partial
from pathlib import Path

from tests.source_tools import load_symbol


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_functions():
    store_path = REPO_ROOT / "fithealth_agent" / "info_store.py"
    spec = importlib.util.spec_from_file_location("structured_memory_store", store_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load info_store")
    store = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(store)
    function = load_symbol(
        "youtube_channels_to_avoid",
        namespace={"resolve_confirmed_memory_facts": store.resolve_confirmed_memory_facts},
    )
    return partial(function, resolve_facts_fn=store.resolve_confirmed_memory_facts)


class StructuredMemoryYoutubeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.channels_to_avoid = staticmethod(load_functions())

    def test_uses_confirmed_structured_channel_fact(self) -> None:
        memories = [{
            "user_confirmed": True,
            "created_at": "2026-08-18T00:00:00+00:00",
            "facts": [{
                "namespace": "youtube", "key": "avoid_channel", "value": "Jeff Nippard", "status": "active",
            }],
            "metadata": {"avoid_youtube_channels": ["Legacy channel should not apply"]},
        }]
        self.assertEqual(self.channels_to_avoid(memories), ["Jeff Nippard"])

    def test_extracts_a_chinese_channel_candidate_from_the_current_message(self) -> None:
        self.assertEqual(
            self.channels_to_avoid([], "以后别再推荐健身科学频道"),
            ["健身科学"],
        )

    def test_ignores_unconfirmed_fact(self) -> None:
        memories = [{
            "user_confirmed": False,
            "facts": [{
                "namespace": "youtube", "key": "avoid_channel", "value": "Jeff Nippard", "status": "active",
            }],
        }]
        self.assertEqual(self.channels_to_avoid(memories), [])


if __name__ == "__main__":
    unittest.main()
