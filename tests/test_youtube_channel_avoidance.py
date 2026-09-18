from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]


class YouTubeChannelAvoidanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = importlib.import_module("fithealth_agent.youtube_tool")

    def test_filters_confirmed_disliked_channel_when_alternatives_exist(self) -> None:
        payload = {
            "query": "bench press tutorial",
            "results": [
                {"channel": "Disliked Channel", "title": "a", "url": "a"},
                {"channel": "Other Channel", "title": "b", "url": "b"},
            ],
            "total_found": 2,
        }
        tool = self.module.YouTubeSearchTool(avoid_channels=["disliked channel"])
        with patch.object(self.module, "search_videos", return_value=json.dumps(payload)) as search:
            response = tool.run({"query": "bench press tutorial", "max_results": 2})
        search.assert_called_once_with(query="bench press tutorial", max_results=5)
        self.assertEqual(response.data["results"][0]["channel"], "Other Channel")

    def test_decorated_channel_variants_are_filtered_consistently(self) -> None:
        payload = {
            "query": "bench press tutorial",
            "results": [
                {"channel": "ATHLEAN-X™", "title": "a", "url": "a"},
                {"channel": "Other Channel", "title": "b", "url": "b"},
            ],
            "total_found": 2,
        }
        for avoided in ("Athlean X", "那个AthleanX频道", "ATHLEAN-X™"):
            with self.subTest(avoided=avoided):
                tool = self.module.YouTubeSearchTool(avoid_channels=[avoided])
                with patch.object(self.module, "search_videos", return_value=json.dumps(payload)):
                    response = tool.run({"query": "bench press tutorial", "max_results": 2})
                self.assertEqual(
                    [item["channel"] for item in response.data["results"]],
                    ["Other Channel"],
                )

    def test_returns_disliked_channel_only_when_no_alternative_exists(self) -> None:
        payload = {"query": "bench press tutorial", "results": [{"channel": "Disliked Channel"}], "total_found": 1}
        tool = self.module.YouTubeSearchTool(avoid_channels=["Disliked Channel"])
        with patch.object(self.module, "search_videos", return_value=json.dumps(payload)):
            response = tool.run({"query": "bench press tutorial", "max_results": 2})
        self.assertTrue(response.data["avoidance_fallback"])
        self.assertEqual(response.data["results"][0]["channel"], "Disliked Channel")

    def test_reuses_normalized_query_within_tool_instance(self) -> None:
        payload = {
            "query": "bench press tutorial",
            "results": [{"channel": "Other Channel", "title": "Bench", "url": "https://example.test"}],
            "total_found": 1,
        }
        tool = self.module.YouTubeSearchTool()
        with patch.object(self.module, "search_videos", return_value=json.dumps(payload)) as search:
            first = tool.run({"query": "Bench Press Tutorial", "max_results": 1})
            first.data["results"][0]["title"] = "mutated by caller"
            second = tool.run({"query": " bench   press tutorial ", "max_results": 1})

        search.assert_called_once_with(query="Bench Press Tutorial", max_results=1)
        self.assertEqual(second.data["results"][0]["title"], "Bench")

    def test_cache_key_keeps_requested_result_count(self) -> None:
        payload = {"query": "bench press tutorial", "results": [], "total_found": 0}
        tool = self.module.YouTubeSearchTool()
        with patch.object(self.module, "search_videos", return_value=json.dumps(payload)) as search:
            tool.run({"query": "bench press tutorial", "max_results": 1})
            tool.run({"query": "bench press tutorial", "max_results": 2})

        self.assertEqual(search.call_count, 2)


if __name__ == "__main__":
    unittest.main()
