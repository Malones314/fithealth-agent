"""BUG-02（本地兜底路由）与 BUG-06（组数统计误判）的回归测试。

两条的共同点是"能力写好了但没接线/接错了"，所以除了测函数本身的行为，
也用源码断言钉住它们确实被 /chat 调用，避免再次退化成死代码。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.module_map import consumer_home, function_home
from tests.source_tools import load_symbols, module_source


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_functions(names: set[str], extra_namespace: dict | None = None) -> dict:
    """按 tests/module_map.py 查表抽取指定函数与其依赖的模块级常量。

    避免 import main 触发整条 LLM 依赖链（见 ARCH-02）。函数搬到 domain/ 之后
    这里不用改——改 module_map.py 一行即可。
    """
    return load_symbols(names, namespace=extra_namespace)


# ══════════════════════════════════════════════════════════════════════════
# BUG-06：组数统计不能把汇总行算进去
# ══════════════════════════════════════════════════════════════════════════


class ExerciseSetCountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ns = load_functions({"_exercise_set_counts"})
        cls.counts = staticmethod(ns["_exercise_set_counts"])

    PLAN = (
        "# 胸部训练\n"
        "热身：动感单车 5 分钟\n"
        "平板卧推：4组 x 8次\n"
        "哑铃飞鸟：3组 x 12次\n"
        "总组数：7组\n"
    )

    def test_summary_line_is_excluded(self) -> None:
        """核心回归：4+3 的计划不能被算成 14。"""
        counts = self.counts(self.PLAN)
        self.assertEqual([sets for _label, sets in counts], [4, 3])
        self.assertEqual(sum(sets for _label, sets in counts), 7)

    def test_various_summary_labels_are_excluded(self) -> None:
        for line in (
            "总组数：12组",
            "合计：12组",
            "共计：12组",
            "累计：12组",
            "总计：12组",
            "本次总组数：12组",
            "- 小计：12组",
        ):
            with self.subTest(line=line):
                self.assertEqual(self.counts(line), [], f"应被当作汇总行跳过：{line}")

    def test_real_exercises_are_still_counted(self) -> None:
        for line, expected in (
            ("平板卧推：4组 x 8次", 4),
            ("- 哑铃飞鸟：3组", 3),
            ("**杠铃深蹲**：5组", 5),
        ):
            with self.subTest(line=line):
                counts = self.counts(line)
                self.assertEqual([sets for _label, sets in counts], [expected])

    def test_known_limitation_table_and_multiplier_forms(self) -> None:
        """记录当前的已知局限，避免误以为已覆盖：

        表格写法与「4 组 × 8 次」的无冒号写法目前都统计不到。这不影响
        BUG-06 的修复（少算不会造成误拦），但会让上限校验偏松。
        """
        self.assertEqual(self.counts("| 高位下拉 | 4组 |"), [])
        self.assertEqual(self.counts("高位下拉 4组 × 8次"), [])

    def test_markdown_bullets_do_not_break_label(self) -> None:
        counts = self.counts("* 平板卧推：4组\n> 哑铃飞鸟：3组\n")
        self.assertEqual([label for label, _sets in counts], ["平板卧推", "哑铃飞鸟"])


class PlanValidationUsesSharedCounterTest(unittest.TestCase):
    """两处校验必须共用同一个提取器，否则又会各写一套正则而行为不一致。"""

    def test_both_checks_call_the_shared_helper(self) -> None:
        validator_source = module_source(function_home("validate_generated_training_plan"))
        self.assertEqual(
            validator_source.count("_exercise_set_counts(answer)"), 2,
            "单动作上限与总组数校验都应调用 _exercise_set_counts",
        )

    def test_old_naive_regexes_are_gone(self) -> None:
        counter_source = module_source(function_home("_exercise_set_counts"))
        self.assertNotIn(r're.findall(r"[：:]\s*(\d+)\s*组", answer)', counter_source)
        self.assertNotIn(r're.finditer(r"[^\n：:]+[：:]\s*(\d+)\s*组", answer)', counter_source)

    def test_compliant_plan_passes_total_limit(self) -> None:
        """端到端：确认过 max_total_sets=10 时，一份 7 组的计划不该被拦。"""
        ns = load_functions(
            {"_exercise_set_counts", "validate_generated_training_plan", "infer_training_subject"},
            {
                "resolve_confirmed_memory_facts": lambda memories: [
                    fact
                    for memory in (memories or [])
                    for fact in memory.get("facts", [])
                ],
            },
        )
        validate = ns["validate_generated_training_plan"]
        memories = [{
            "user_confirmed": True,
            "facts": [
                {"namespace": "training", "key": "max_total_sets", "value": 10, "status": "active"},
                {"namespace": "training", "key": "max_sets_per_exercise", "value": 5, "status": "active"},
            ],
        }]
        violations = validate(ExerciseSetCountTest.PLAN, memories, "")
        self.assertEqual(
            [v for v in violations if "组数" in v], [],
            f"合规计划不应因组数被拦：{violations}",
        )

    def test_genuinely_excessive_plan_is_still_rejected(self) -> None:
        """修完误报不能把真正的超限也放过去。"""
        ns = load_functions(
            {"_exercise_set_counts", "validate_generated_training_plan", "infer_training_subject"},
            {
                "resolve_confirmed_memory_facts": lambda memories: [
                    fact for memory in (memories or []) for fact in memory.get("facts", [])
                ],
            },
        )
        validate = ns["validate_generated_training_plan"]
        memories = [{
            "user_confirmed": True,
            "facts": [{"namespace": "training", "key": "max_total_sets", "value": 10, "status": "active"}],
        }]
        plan = "卧推：6组\n飞鸟：6组\n总组数：12组\n"
        violations = validate(plan, memories, "")
        self.assertTrue(
            any("总组数" in v for v in violations),
            f"12 组超过上限 10，应被拦下：{violations}",
        )

    def test_generic_today_subject_is_not_treated_as_weekly_subject(self) -> None:
        ns = load_functions(
            {
                "_exercise_set_counts",
                "validate_generated_training_plan",
                "infer_training_subject",
                "is_generic_training_subject",
            },
            {"resolve_confirmed_memory_facts": lambda memories: []},
        )
        violations = ns["validate_generated_training_plan"](
            "# 背部训练\n高位下拉：4组\n坐姿划船：4组\n",
            [],
            "今日训练",
        )
        self.assertNotIn("计划内容未体现周计划科目：今日训练", violations)

    def test_concrete_weekly_subject_is_still_enforced(self) -> None:
        ns = load_functions(
            {
                "_exercise_set_counts",
                "validate_generated_training_plan",
                "infer_training_subject",
                "is_generic_training_subject",
            },
            {"resolve_confirmed_memory_facts": lambda memories: []},
        )
        violations = ns["validate_generated_training_plan"](
            "# 背部训练\n高位下拉：4组\n",
            [],
            "腿部训练",
        )
        self.assertIn("计划内容未体现周计划科目：腿部训练", violations)

    def test_generic_card_title_uses_concrete_schedule_subject(self) -> None:
        ns = load_functions({"is_generic_training_subject", "plan_card_title"})
        subject = "腿（高脚杯深蹲+臀桥+髋屈肌拉伸）"
        self.assertEqual(
            ns["plan_card_title"]("今日训练计划", subject),
            subject + "计划",
        )

    def test_concrete_card_title_is_preserved(self) -> None:
        ns = load_functions({"is_generic_training_subject", "plan_card_title"})
        self.assertEqual(
            ns["plan_card_title"]("周五下肢力量课", "腿部训练"),
            "周五下肢力量课",
        )


# ══════════════════════════════════════════════════════════════════════════
# BUG-02：本地兜底路由
# ══════════════════════════════════════════════════════════════════════════


class LocalFallbackRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ns = load_functions({"is_training_record_query", "is_profile_query", "is_training_related", "_requested_record_date", "extract_iso_dates"})
        cls.is_record_query = staticmethod(ns["is_training_record_query"])
        cls.is_profile_query = staticmethod(ns["is_profile_query"])
        cls.is_training_related = staticmethod(ns["is_training_related"])
        cls.requested_date = staticmethod(ns["_requested_record_date"])

    def test_training_record_phrasings_are_recognized(self) -> None:
        for text in (
            "查看训练记录",
            "查看我的训练记录",
            "我想看看运动记录",
            "回顾一下最近的锻炼记录",
            "查询 2026-08-18 的训练记录",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.is_record_query(text), text)

    def test_unrelated_messages_do_not_trigger_record_view(self) -> None:
        for text in ("帮我安排今天的训练", "记录一下今天的体重", "深蹲怎么做才标准"):
            with self.subTest(text=text):
                self.assertFalse(self.is_record_query(text), text)

    def test_requested_date_is_extracted_for_offline_use(self) -> None:
        self.assertEqual(self.requested_date("查询 2026-08-18 的训练记录"), "2026-08-18")
        self.assertEqual(self.requested_date("看 2026-8-9 的训练记录"), "2026-08-09")
        self.assertIsNone(self.requested_date("查看训练记录"))
        self.assertIsNone(self.requested_date("2026-13-45 这天"))

    def test_profile_query_is_recognized_but_narrow(self) -> None:
        for text in ("查看个人信息", "查看我的档案", "个人档案"):
            with self.subTest(text=text):
                self.assertTrue(self.is_profile_query(text), text)
        # 复合请求不应被短路掉，否则用户的第二个诉求会被吞（BUG-14）
        for text in ("我的可用器械有哪些，用这些器械帮我安排今天的胸推", "帮我改一下个人信息里的体重"):
            with self.subTest(text=text):
                self.assertFalse(self.is_profile_query(text), text)

    def test_is_training_related_discriminates(self) -> None:
        self.assertTrue(self.is_training_related("今天卧推做了几组"))
        self.assertFalse(self.is_training_related("今天天气怎么样"))


class LocalFallbackWiringTest(unittest.TestCase):
    """这三个函数以前定义了却零调用，必须钉住它们确实接进了 /chat。"""

    def test_all_three_helpers_are_called(self) -> None:
        chat_source = module_source(consumer_home("chat"))
        for call in (
            "is_training_record_query(message)",
            "_requested_record_date(message)",
            "is_profile_query(message)",
            "is_training_related(message)",
        ):
            with self.subTest(call=call):
                self.assertIn(call, chat_source, f"{call} 应在 /chat 中被调用")

    def test_local_branches_run_before_the_503(self) -> None:
        """本地分支必须排在"外部模型已关闭"的 503 之前，否则离线依然不可用。"""
        chat_source = module_source(consumer_home("chat"))
        record_at = chat_source.index("wants_training_records = (")
        profile_at = chat_source.index("is_profile_query(message)")
        block_at = chat_source.index("if not external_models_enabled:")
        self.assertLess(record_at, block_at)
        self.assertLess(profile_at, block_at)

    def test_offline_reply_names_the_local_commands(self) -> None:
        self.assertIn(
            "「查看训练记录」「查看个人信息」「查看本周数据」",
            module_source(consumer_home("chat")),
        )


if __name__ == "__main__":
    unittest.main()
