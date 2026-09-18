"""BUG-07 回归测试：消息里的 ISO 日期不能一律当成"要为这一天出计划"。

原缺陷有两条：
1. `scheduled_plan_for_message` 抓消息里**第一个** `20\\d{2}-\\d{2}-\\d{2}` 就当计划
   日期，不看语境。"我在 2026-08-01 拉伤了腰，今天能练什么？"会匹配到 08-01
   （周六），注入硬约束"当天科目必须为 X"，还可能把计划存到错误日期上。
2. 两处正则不一致：计划侧用严格补零的 `20\\d{2}-\\d{2}-\\d{2}`，查记录侧用宽松的
   `\\d{4}-\\d{1,2}-\\d{1,2}`。于是 "2026-8-9" 只被后者识别——查记录正常，生成
   计划时周计划约束却**静默**不生效。
"""

from __future__ import annotations

import re
import unittest
from datetime import date
from functools import partial
from pathlib import Path

from fithealth_agent.health_safety import clause_bounds
from fithealth_agent.info_store import parse_weekly_schedule, weekly_schedule_entry_for_date
from tests.module_map import consumer_home, function_home
from tests.source_tools import load_symbols, module_source


REPO_ROOT = Path(__file__).resolve().parents[1]


# 只报符号名、不碰文件路径：这些函数搬到 domain/ 之后只需改 tests/module_map.py。
# 抽出来单独执行是为了避免 import main 触发整条 LLM 依赖链（ARCH-02）。
NS = load_symbols(
    {
        "extract_iso_dates",
        "requested_plan_date",
        "scheduled_plan_for_message",
        "scheduled_weekly_entry_for_message",
        "confirmed_weekly_schedule",
        "confirmed_weekly_schedule_entry",
        "_requested_record_date",
        "current_instruction_override",
        "daily_schedule_constraint",
    },
    namespace={"clause_bounds": clause_bounds},
)
NS["parse_weekly_schedule"] = parse_weekly_schedule
NS["weekly_schedule_entry_for_date"] = weekly_schedule_entry_for_date
NS["resolve_confirmed_memory_facts"] = lambda memories: [
    fact
    for memory in (memories or [])
    if isinstance(memory, dict) and memory.get("user_confirmed")
    for fact in memory.get("facts", [])
    if isinstance(fact, dict)
]
NS["daily_schedule_constraint"] = partial(
    NS["daily_schedule_constraint"],
    current_instruction_override_fn=NS["current_instruction_override"],
    scheduled_weekly_entry_for_message_fn=NS["scheduled_weekly_entry_for_message"],
)

# 2026-08-01 是周六，2026-08-18 是周二，2026-08-19 是周三
WEEKLY = [{
    "user_confirmed": True,
    "facts": [{
        "namespace": "plan", "key": "weekly_schedule",
        "value": '{"sat":"全身训练","tue":"背部训练","wed":"腿部训练"}',
    }],
}]


class DateParsingConsistencyTest(unittest.TestCase):
    """不变量 2：计划侧与查记录侧共用同一个解析器。"""

    def test_non_padded_dates_are_parsed(self) -> None:
        found = NS["extract_iso_dates"]("查询 2026-8-9 的情况")
        self.assertEqual([item[0] for item in found], [date(2026, 8, 9)])

    def test_both_paths_agree_on_the_same_string(self) -> None:
        # 修之前："2026-8-9" 只被查记录侧识别，计划侧静默失效
        message = "2026-8-9 的训练计划"
        self.assertEqual(NS["requested_plan_date"](message), date(2026, 8, 9))
        self.assertEqual(NS["_requested_record_date"]("查 2026-8-9 的训练记录"), "2026-08-09")

    def test_invalid_and_out_of_range_dates_are_dropped(self) -> None:
        for text in ("2026-13-01 的计划", "2026-02-30 的计划", "1899-01-01 的计划"):
            with self.subTest(text=text):
                self.assertEqual(NS["extract_iso_dates"](text), [])

    def test_digits_around_the_date_do_not_match(self) -> None:
        self.assertEqual(NS["extract_iso_dates"]("12026-08-18"), [])
        self.assertEqual(NS["extract_iso_dates"]("2026-08-188"), [])

    def test_all_dates_are_returned_with_positions(self) -> None:
        found = NS["extract_iso_dates"]("从 2026-08-18 到 2026-08-19")
        self.assertEqual([item[0] for item in found], [date(2026, 8, 18), date(2026, 8, 19)])
        self.assertLess(found[0][1], found[1][1])

    def test_non_string_input_is_tolerated(self) -> None:
        self.assertEqual(NS["extract_iso_dates"](None), [])
        self.assertIsNone(NS["requested_plan_date"](None))


class PlanDateContextGateTest(unittest.TestCase):
    """不变量 1：只有语境确实是"为这一天出计划"时才生效。"""

    def test_the_reported_false_positive_is_gone(self) -> None:
        # 这就是清单里的原例：08-01 是周六，原实现会注入"当天必须全身训练"
        message = "我在 2026-08-01 拉伤了腰，今天能练什么？"
        self.assertIsNone(NS["requested_plan_date"](message))
        self.assertIsNone(NS["scheduled_plan_for_message"](message, WEEKLY))
        self.assertEqual(NS["daily_schedule_constraint"](message, WEEKLY), "")

    def test_past_event_and_history_contexts_are_vetoed(self) -> None:
        for message in (
            "我在 2026-08-01 扭伤了脚踝，还能训练吗",
            "2026-08-01 体检查出血压偏高，训练要注意什么",
            "上次 2026-08-01 的训练计划是什么",
            "帮我查询 2026-08-18 的训练记录",
            "回顾一下 2026-08-18 的训练",
            "2026-08-01 那天发烧了，之后怎么安排",
        ):
            with self.subTest(message=message):
                self.assertIsNone(NS["requested_plan_date"](message))

    def test_genuine_plan_requests_still_work(self) -> None:
        for message, expected in (
            ("今天是2026-08-18，生成今天计划", date(2026, 8, 18)),
            ("生成 2026-08-18 的训练计划", date(2026, 8, 18)),
            ("2026-08-19 帮我安排一下", date(2026, 8, 19)),
            ("2026-08-18 练什么", date(2026, 8, 18)),
            ("请制定 2026-08-19 的计划", date(2026, 8, 19)),
            ("把训练挪到 2026-08-19", date(2026, 8, 19)),
        ):
            with self.subTest(message=message):
                self.assertEqual(NS["requested_plan_date"](message), expected)

    def test_today_plan_request_resolves_in_shanghai_timezone(self) -> None:
        expected = __import__("datetime").datetime.now(
            __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
        ).date()
        for message in (
            "生成今天的训练计划",
            "生成今日训练",
            "安排今天训练",
        ):
            with self.subTest(message=message):
                self.assertEqual(NS["requested_plan_date"](message), expected)

    def test_today_plan_uses_the_confirmed_weekly_subject(self) -> None:
        today = __import__("datetime").datetime.now(
            __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
        ).date()
        weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[today.weekday()]
        memories = [{
            "user_confirmed": True,
            "facts": [{
                "namespace": "plan",
                "key": "weekly_schedule",
                "value": __import__("json").dumps({weekday: "背部训练"}),
            }],
        }]
        self.assertEqual(
            NS["scheduled_plan_for_message"]("生成今天的训练计划", memories),
            (today, "背部训练"),
        )
        self.assertEqual(
            NS["scheduled_plan_for_message"]("生成今日训练", memories),
            (today, "背部训练"),
        )

    def test_a_bare_date_needs_no_intent_word(self) -> None:
        # 整条消息就是一个日期，本身就是明确指定
        self.assertEqual(NS["requested_plan_date"]("2026-08-18"), date(2026, 8, 18))
        self.assertEqual(NS["requested_plan_date"](" 2026-8-18 "), date(2026, 8, 18))

    def test_a_date_with_no_clue_at_all_is_ignored(self) -> None:
        # 没有意图词就不生效：代价是日期约束不施加，退回原有行为，不会产生错数据
        self.assertIsNone(NS["requested_plan_date"]("2026-08-18 天气不错"))

    def test_the_first_usable_date_wins_not_the_first_date(self) -> None:
        # 原实现取"第一个日期"；现在取"第一个语境通过的日期"
        message = "我在 2026-08-01 拉伤了腰，2026-08-19 能练什么"
        self.assertEqual(NS["requested_plan_date"](message), date(2026, 8, 19))
        scheduled = NS["scheduled_plan_for_message"](message, WEEKLY)
        self.assertEqual(scheduled[0], date(2026, 8, 19))
        self.assertEqual(scheduled[1], "腿部训练")

    def test_schedule_still_requires_a_confirmed_subject(self) -> None:
        # 周计划里没排周四，即使日期语境成立也不该编一个科目出来
        self.assertIsNone(NS["scheduled_plan_for_message"]("2026-08-20 练什么", WEEKLY))

    def test_rest_day_is_resolved_but_never_becomes_a_training_subject(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule",
            "value": '{"days":{"wed":{"type":"rest"},"fri":{"type":"aerobic","subject":"跑步"}}}',
        }]}]
        message = "生成 2026-08-19 的训练计划"
        self.assertEqual(
            NS["scheduled_weekly_entry_for_message"](message, memories),
            (date(2026, 8, 19), {"type": "rest"}),
        )
        self.assertIsNone(NS["scheduled_plan_for_message"](message, memories))
        self.assertIn("休息日", NS["daily_schedule_constraint"](message, memories))
        self.assertEqual(
            NS["scheduled_plan_for_message"]("生成 2026-08-21 的训练计划", memories),
            (date(2026, 8, 21), "跑步"),
        )

    def test_explicit_override_still_suppresses_the_hard_constraint(self) -> None:
        # daily_schedule_constraint 原有的"当前指令优先"行为不受影响
        message = "今天不想练背，2026-08-18 的计划改一下"
        self.assertTrue(NS["current_instruction_override"](message))
        self.assertEqual(NS["daily_schedule_constraint"](message, WEEKLY), "")

    def test_constraint_text_is_emitted_for_a_real_request(self) -> None:
        text = NS["daily_schedule_constraint"]("生成 2026-08-18 的训练计划", WEEKLY)
        self.assertIn("2026-08-18", text)
        self.assertIn("背部训练", text)
        self.assertIn("周二", text)


class RecordDateIsNotGatedTest(unittest.TestCase):
    """查记录路径刻意不做语境门控。"""

    def test_record_lookup_accepts_a_plain_date(self) -> None:
        # 这条路径只在"查看训练记录"意图命中后才走到，用户意图本身就是查历史；
        # 再要求意图词只会把最常见的问法挡掉。
        self.assertEqual(NS["_requested_record_date"]("查询 2026-08-18 的训练记录"), "2026-08-18")
        self.assertEqual(NS["_requested_record_date"]("2026-08-18"), "2026-08-18")

    def test_no_date_returns_none(self) -> None:
        self.assertIsNone(NS["_requested_record_date"]("查看训练记录"))


class WiringTest(unittest.TestCase):
    def test_the_old_strict_regex_is_gone(self) -> None:
        extractor_source = module_source(function_home("extract_iso_dates"))
        self.assertNotIn(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", extractor_source)

    def test_both_paths_go_through_the_shared_extractor(self) -> None:
        plan_source = module_source(function_home("requested_plan_date"))
        record_source = module_source(function_home("_requested_record_date"))
        plan_body = plan_source.split("def requested_plan_date(", 1)[1].split("\ndef ", 1)[0]
        record_body = record_source.split("def _requested_record_date(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("extract_iso_dates(", plan_body)
        self.assertIn("extract_iso_dates(", record_body)

    def test_scheduled_plan_uses_the_gated_helper(self) -> None:
        source = module_source(function_home("scheduled_plan_for_message"))
        body = source.split("def scheduled_plan_for_message(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("requested_plan_date(message)", body)

    def test_chat_uses_all_confirmed_memories_for_weekly_schedule(self) -> None:
        self.assertIn(
            "scheduled_plan_for_message(message, deps.info_store.get_all())",
            module_source(consumer_home("chat")),
        )

    def test_scheduled_subject_overrides_router_summary(self) -> None:
        chat_source = module_source(consumer_home("chat"))
        create_branch = chat_source.split("and chat_intent.create_training_plan", 1)[1]
        create_branch = create_branch.split("elif scheduled_plan is not None", 1)[0]
        self.assertIn("if scheduled_plan is not None:", create_branch)
        self.assertIn("scheduled_date, subject = scheduled_plan", create_branch)


if __name__ == "__main__":
    unittest.main()
