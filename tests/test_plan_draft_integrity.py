"""BUG-05 回归测试：不能保存被截断的训练计划。

原状：`most_recent_complete_training_plan(history)` 从聊天历史回捞计划正文，
而 history 已被 `context_budget._normalize_history` 两级截断（单条 4000 字符、
总量 12000 字符，并追加截断标记）。一份正常计划轻松超过 4000 字符，而完整性
判断只要 ≥300 字符就通过 —— 于是带着「[历史消息已截断]」的残片原样落盘，
且 plan_store 按 content_hash 去重，重存也修不回来。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import load_symbols, module_source


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


context_budget = load_module("context_budget_bug05", PACKAGE_DIR / "context_budget.py")
plan_draft_cache = load_module("plan_draft_cache_bug05", PACKAGE_DIR / "plan_draft_cache.py")


def full_plan(padding: int = 0) -> str:
    """一份足以通过完整性判断的计划正文。"""
    body = (
        "# 胸部训练计划\n"
        "## 热身\n动感单车 5 分钟\n"
        "## 主项\n平板卧推：4组 x 8次\n哑铃飞鸟：3组 x 12次\n上斜卧推：3组 x 10次\n"
        "## 拉伸\n胸大肌拉伸 2 分钟\n"
    )
    return body + ("补充说明。" * padding if padding else "详细说明。" * 60)


class TruncationMarkerTest(unittest.TestCase):
    def test_markers_are_detected(self) -> None:
        for marker in context_budget.TRUNCATION_MARKERS:
            with self.subTest(marker=marker):
                self.assertTrue(context_budget.contains_truncation_marker(f"计划正文…{marker}"))

    def test_clean_text_is_not_flagged(self) -> None:
        self.assertFalse(context_budget.contains_truncation_marker(full_plan()))
        self.assertFalse(context_budget.contains_truncation_marker(""))
        self.assertFalse(context_budget.contains_truncation_marker(None))

    def test_normalize_history_actually_stamps_the_marker(self) -> None:
        """确认标记常量与实际写入的一致——两边分头改就会悄悄失效。"""
        long_text = "计划" * 5000
        normalized = context_budget._normalize_history([{"role": "assistant", "text": long_text}])
        self.assertTrue(
            context_budget.contains_truncation_marker(normalized[0]["text"]),
            "超长历史消息应被打上截断标记",
        )


class CompletePlanDetectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # TRAINING_SUBJECT_RULES 不需要注入：它在 muscle_map.py 里，两个函数各自
        # 在函数体内 import。contains_truncation_marker 注入真实实现而不是桩，
        # 避免测试与实现各写一套截断判断而悄悄漂移。
        ns = load_symbols(
            {"looks_like_complete_training_plan", "most_recent_complete_training_plan"},
            namespace={"contains_truncation_marker": context_budget.contains_truncation_marker},
        )
        cls.looks_complete = staticmethod(ns["looks_like_complete_training_plan"])
        cls.most_recent = staticmethod(ns["most_recent_complete_training_plan"])

    def test_intact_plan_is_recognized(self) -> None:
        self.assertTrue(self.looks_complete(full_plan()))

    def test_truncated_plan_is_rejected(self) -> None:
        """核心回归：残片长度仍有几千字符，长度/关键词条件全都满足。"""
        truncated = full_plan() + "\n[历史消息已截断]"
        self.assertGreater(len(truncated), 300)
        self.assertFalse(self.looks_complete(truncated))

    def test_budget_exhausted_marker_is_rejected(self) -> None:
        self.assertFalse(self.looks_complete(full_plan() + "\n[历史上下文预算已用尽]"))

    def test_history_scan_skips_truncated_entries(self) -> None:
        history = [
            {"role": "assistant", "text": full_plan() + "\n[历史消息已截断]"},
            {"role": "user", "text": "保存刚才的计划"},
        ]
        self.assertEqual(self.most_recent(history), "", "残片不能被当成可保存的计划")

    def test_history_scan_still_finds_intact_plan(self) -> None:
        intact = full_plan()
        history = [
            {"role": "assistant", "text": intact},
            {"role": "user", "text": "保存刚才的计划"},
        ]
        self.assertEqual(self.most_recent(history), intact)


class PlanDraftCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = plan_draft_cache.PlanDraftCache(max_items=3, ttl_seconds=3600)

    def test_remember_and_latest_round_trip(self) -> None:
        content = full_plan()
        draft = self.cache.remember(content=content, subject="胸部训练", title="胸部计划")
        latest = self.cache.latest()
        self.assertEqual(latest.id, draft.id)
        self.assertEqual(latest.content, content)
        self.assertEqual(latest.subject, "胸部训练")

    def test_latest_returns_the_newest(self) -> None:
        self.cache.remember(content="第一份", subject="A")
        second = self.cache.remember(content="第二份", subject="B")
        self.assertEqual(self.cache.latest().id, second.id)

    def test_get_by_id(self) -> None:
        first = self.cache.remember(content="第一份")
        self.cache.remember(content="第二份")
        self.assertEqual(self.cache.get(first.id).content, "第一份")
        self.assertIsNone(self.cache.get("不存在的 id"))
        self.assertIsNone(self.cache.get(""))
        self.assertIsNone(self.cache.get(None))

    def test_max_items_is_enforced(self) -> None:
        for i in range(6):
            self.cache.remember(content=f"计划{i}")
        self.assertEqual(len(self.cache), 3)
        self.assertEqual(self.cache.latest().content, "计划5")

    def test_expired_drafts_are_dropped(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        self.cache.remember(content="过期的", now=old)
        self.assertIsNone(self.cache.latest())
        self.assertEqual(len(self.cache), 0)

    def test_unexpired_draft_survives(self) -> None:
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.cache.remember(content="还新鲜", now=recent)
        self.assertIsNotNone(self.cache.latest())

    def test_cache_is_thread_safe(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        cache = plan_draft_cache.PlanDraftCache(max_items=100, ttl_seconds=3600)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: cache.remember(content=f"计划{i}"), range(80)))
        self.assertEqual(len(cache), 80)


class SaveContentResolutionTest(unittest.TestCase):
    """真正调用 resolve_plan_save_content，而不是对 main.py 做字符串断言。

    原先这里全是 `assertIn("...", MAIN_SOURCE)`——重构改个变量名，测试就会
    绿着失效。把顺序敏感的裁决抽成纯函数之后就能直接调用了。
    """

    def setUp(self) -> None:
        self.cache = plan_draft_cache.PlanDraftCache()
        self.truncated = "背部训练计划" + "x" * 400 + "\n[历史消息已截断]"

    def resolve(self, payload: dict) -> tuple[str, str | None]:
        return plan_draft_cache.resolve_plan_save_content(
            payload, self.cache, is_truncated=context_budget.contains_truncation_marker
        )

    def test_draft_id_wins_over_the_frontend_content(self) -> None:
        draft = self.cache.remember(content="完整正文", subject="背部训练")
        content, error = self.resolve(
            {"draft_id": draft.id, "content": "前端传来的另一份"}
        )
        self.assertIsNone(error)
        self.assertEqual(content, "完整正文")

    def test_draft_id_wins_even_when_frontend_content_is_truncated(self) -> None:
        # 这正是第一版修复的顺序错误：校验排在取草稿之前，带合法 draft_id
        # 的请求会因为前端那份是残片而先被 400 掉。
        draft = self.cache.remember(content="完整正文", subject="背部训练")
        content, error = self.resolve(
            {"draft_id": draft.id, "content": self.truncated}
        )
        self.assertIsNone(error)
        self.assertEqual(content, "完整正文")

    def test_truncated_content_is_refused_when_there_is_no_draft(self) -> None:
        content, error = self.resolve({"content": self.truncated})
        self.assertEqual(content, "")
        self.assertEqual(error, plan_draft_cache.TRUNCATED_CONTENT_ERROR)

    def test_unknown_or_expired_draft_id_falls_back_to_content(self) -> None:
        content, error = self.resolve(
            {"draft_id": "does-not-exist", "content": "完整的前端正文"}
        )
        self.assertIsNone(error)
        self.assertEqual(content, "完整的前端正文")

    def test_empty_draft_content_does_not_shadow_the_frontend(self) -> None:
        draft = self.cache.remember(content="", subject="背部训练")
        content, error = self.resolve(
            {"draft_id": draft.id, "content": "完整的前端正文"}
        )
        self.assertIsNone(error)
        self.assertEqual(content, "完整的前端正文")


class DraftLookupTest(unittest.TestCase):
    """find() 按线索挑草稿，避免一个会话里多份计划时存错。"""

    def setUp(self) -> None:
        self.cache = plan_draft_cache.PlanDraftCache()
        self.back = self.cache.remember(
            content="背部计划正文", subject="背部训练", suggested_date="2026-08-18"
        )
        self.legs = self.cache.remember(
            content="腿部计划正文", subject="腿部训练", suggested_date="2026-08-19"
        )

    def test_subject_and_date_match_wins(self) -> None:
        found = self.cache.find(subject="背部训练", suggested_date="2026-08-18")
        self.assertEqual(found.id, self.back.id)

    def test_subject_alone_is_enough(self) -> None:
        # latest() 会返回腿部那份——这就是原来会静默存错的地方
        self.assertEqual(self.cache.latest().id, self.legs.id)
        self.assertEqual(self.cache.find(subject="背部训练").id, self.back.id)

    def test_date_alone_is_enough(self) -> None:
        self.assertEqual(self.cache.find(suggested_date="2026-08-18").id, self.back.id)

    def test_no_clue_falls_back_to_the_latest(self) -> None:
        self.assertEqual(self.cache.find().id, self.legs.id)

    def test_unmatched_clue_still_falls_back_rather_than_failing(self) -> None:
        self.assertEqual(self.cache.find(subject="肩部训练").id, self.legs.id)

    def test_empty_cache_returns_none(self) -> None:
        self.cache.clear()
        self.assertIsNone(self.cache.find(subject="背部训练"))


class WiringTest(unittest.TestCase):
    """剩下的源码断言只用于那些没法在沙箱里调用的接线点（route / 前端）。"""

    def test_save_branch_prefers_the_cache_over_history(self) -> None:
        chat_source = module_source(consumer_home("chat"))
        self.assertIn("draft = deps.plan_draft_cache.find(", chat_source)
        # history 仍保留为兜底，但必须排在缓存之后
        cache_at = chat_source.index("draft = deps.plan_draft_cache.find(")
        history_at = chat_source.index("most_recent_complete_training_plan(history)", cache_at)
        self.assertLess(cache_at, history_at)

    def test_save_branch_passes_the_intent_clues_to_the_cache(self) -> None:
        chat_source = module_source(consumer_home("chat"))
        self.assertIn("subject=chat_intent.saved_plan_subject", chat_source)
        self.assertIn("suggested_date=chat_intent.saved_plan_date", chat_source)

    def test_cached_subject_wins_over_generic_save_intent(self) -> None:
        chat_source = module_source(consumer_home("chat"))
        save_branch = chat_source.split("if chat_intent is not None and chat_intent.save_existing_training_plan:", 1)[1]
        subject_assignment = save_branch.split("subject = (", 1)[1].split("\n        )", 1)[0]
        self.assertLess(
            subject_assignment.index("draft.subject"),
            subject_assignment.index("chat_intent.saved_plan_subject"),
        )

    def test_generated_plans_are_cached_after_validation(self) -> None:
        chat_source = module_source(consumer_home("chat"))
        self.assertIn("deps.plan_draft_cache.remember(", chat_source)
        self.assertIn('artifact["draft_id"] = draft.id', chat_source)

    def test_save_endpoint_delegates_to_the_pure_resolver(self) -> None:
        save_source = module_source(consumer_home("save_plan"))
        self.assertIn("resolve_plan_save_content(", save_source)
        self.assertIn("is_truncated=contains_truncation_marker", save_source)

    def test_local_plan_save_artifact_carries_the_draft_id(self) -> None:
        # 否则这条路径退回"靠前端传值"，只是碰巧内容完整才没出错
        self.assertIn('"draft_id": draft.id', module_source(consumer_home("chat")))

if __name__ == "__main__":
    unittest.main()
