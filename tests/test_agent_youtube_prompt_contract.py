from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


class AgentYouTubePromptContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.prompts = (PACKAGE_DIR / "prompts.py").read_text(encoding="utf-8")
        self.youtube_tool = (PACKAGE_DIR / "youtube_tool.py").read_text(encoding="utf-8")

    def test_prompt_uses_registered_youtube_tool_name(self) -> None:
        self.assertIn("`search_youtube_video`", self.prompts)
        self.assertNotIn("`youtube` 工具", self.prompts)

    def test_prompt_searches_each_distinct_plan_exercise(self) -> None:
        self.assertIn("【每一个单独的动作】", self.prompts)
        self.assertIn("重复动作不逐项检索", self.prompts)
        self.assertIn("`max_results=2`", self.prompts)
        self.assertNotIn("为计划中每一个动作各调一次", self.prompts)
        self.assertIn("search only 1-3 unique core lifts", self.youtube_tool)
        self.assertIn("do not search", self.youtube_tool)
        self.assertIn("warm-ups, cool-downs, or repeated exercises", self.youtube_tool)

    def test_training_save_rule_has_one_path(self) -> None:
        self.assertIn("每日健康数据", self.prompts)
        self.assertIn("训练记录不在此列", self.prompts)
        self.assertIn("确认并保存训练", self.prompts)
        self.assertNotIn("优先调用工具保存结构化数据", self.prompts)


if __name__ == "__main__":
    unittest.main()
