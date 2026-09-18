from __future__ import annotations

import importlib.util
import unittest
from functools import partial
from pathlib import Path

from fithealth_agent.health_safety import CLAUSE_SEPARATORS, clause_bounds
from fithealth_agent.info_store import parse_weekly_schedule, weekly_schedule_entry_for_date
from fithealth_agent.observability import trace_event
from tests.source_tools import load_symbols


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_state_for_context():
    """取 plan_workflow.state_for_context；本文件的用例不校验状态本身。

    plan_workflow.py 用了 3.11+ 的 StrEnum（项目要求 3.11+）。在更低版本上
    退回一个等价实现，好让这些与状态无关的上下文用例仍然可以运行。
    """
    path = REPO_ROOT / "fithealth_agent/plan_workflow.py"
    spec = importlib.util.spec_from_file_location("plan_workflow_for_memory_test", path)
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            return module.state_for_context
        except ImportError:  # Python < 3.11：没有 StrEnum
            pass

    def fallback(context):
        if context.get("blocking_reasons"):
            return "constraint_conflict"
        if context.get("clarification_required"):
            return "needs_clarification"
        return "ready_to_generate"

    return fallback


def load_context_functions():
    wanted = {
        "profile_summary", "format_cross_session_memories", "build_agent_input",
        "confirmed_memory_profile", "confirmed_weekly_schedule", "daily_schedule_constraint",
        "confirmed_weekly_schedule_entry", "scheduled_plan_for_message", "scheduled_weekly_entry_for_message",
        "extract_iso_dates", "requested_plan_date",
        "validate_generated_training_plan", "current_instruction_override", "resolve_plan_context", "format_plan_context",
        "explicitly_requested_recovery_regions", "_subject_recovery_regions", "_recovery_context_payload",
        "_exercise_set_counts", "is_generic_training_subject", "_is_safety_bypass_request",
    }

    class UserProfileStoreStub:
        DEFAULT_EQUIPMENT = ["哑铃", "哑铃凳"]

    budget_path = REPO_ROOT / "fithealth_agent/context_budget.py"
    spec = importlib.util.spec_from_file_location("context_budget_for_memory_test", budget_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load context_budget")
    budget = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(budget)

    namespace = {
        "UserProfileStore": UserProfileStoreStub,
        "AGENT_INPUT_MAX_CHARS": budget.AGENT_INPUT_MAX_CHARS,
        "ContextInputError": budget.ContextInputError,
        # `build_agent_input` 会记一条 `gate/context_budget`（agent-trace 阶段 5）。
        # 注入**真实**的记录入口而不是空桩：它在回合外本来就是一次 ContextVar.get()，
        # 而空桩会让"这些函数抽出来还能不能跑"这条验证漏掉真实签名。
        "trace_event": trace_event,
        "resolve_confirmed_memory_facts": lambda memories: [
            fact
            for memory in (memories or [])
            if isinstance(memory, dict) and memory.get("user_confirmed")
            for fact in memory.get("facts", [])
            if isinstance(fact, dict)
        ],
        "state_for_context": _load_state_for_context(),
        "parse_weekly_schedule": parse_weekly_schedule,
        "weekly_schedule_entry_for_date": weekly_schedule_entry_for_date,
        "clause_bounds": clause_bounds,
        "CLAUSE_SEPARATORS": CLAUSE_SEPARATORS,
        "REGION_ALIASES": {
            "胸部": ("胸部", "胸"), "背部": ("背部", "背", "后背"),
            "腿部": ("腿部", "腿", "下肢"), "肩部": ("肩部", "肩膀", "肩"),
            "手臂": ("手臂", "胳膊", "上臂", "前臂"), "核心": ("核心", "腹部", "腹"),
        },
    }
    # 按 tests/module_map.py 查表抽取；这些函数依赖的模块级私有大写常量
    # （_SAFETY_BYPASS_PATTERN、_SET_COUNT_PATTERN…）由 load_symbols 自动带上，
    # 直接复用实现里的定义，避免测试与实现各写一份正则而悄悄漂移。
    namespace = load_symbols(wanted, namespace=namespace)
    namespace["format_cross_session_memories"] = partial(
        namespace["format_cross_session_memories"],
        resolve_facts_fn=namespace["resolve_confirmed_memory_facts"],
    )
    namespace["confirmed_memory_profile"] = partial(
        namespace["confirmed_memory_profile"],
        resolve_facts_fn=namespace["resolve_confirmed_memory_facts"],
    )
    namespace["confirmed_weekly_schedule"] = partial(
        namespace["confirmed_weekly_schedule"],
        resolve_facts_fn=namespace["resolve_confirmed_memory_facts"],
        parse_schedule_fn=namespace["parse_weekly_schedule"],
    )
    namespace["resolve_plan_context"] = partial(
        namespace["resolve_plan_context"],
        current_instruction_override_fn=namespace["current_instruction_override"],
        is_safety_bypass_request_fn=namespace["_is_safety_bypass_request"],
        recovery_context_payload_fn=namespace["_recovery_context_payload"],
        subject_recovery_regions_fn=namespace["_subject_recovery_regions"],
        explicitly_requested_recovery_regions_fn=namespace["explicitly_requested_recovery_regions"],
        scheduled_weekly_entry_for_message_fn=namespace["scheduled_weekly_entry_for_message"],
    )
    namespace["daily_schedule_constraint"] = partial(
        namespace["daily_schedule_constraint"],
        current_instruction_override_fn=namespace["current_instruction_override"],
        scheduled_weekly_entry_for_message_fn=namespace["scheduled_weekly_entry_for_message"],
    )
    return (
        namespace["format_cross_session_memories"], namespace["build_agent_input"],
        namespace["confirmed_memory_profile"], namespace["daily_schedule_constraint"],
        namespace["scheduled_plan_for_message"], namespace["validate_generated_training_plan"],
        namespace["current_instruction_override"], namespace["resolve_plan_context"], namespace["format_plan_context"],
    )

class CrossSessionMemoryContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        formatter, builder, profile_formatter, schedule_constraint, scheduled_plan, validator, current_override, resolve_context, format_context = load_context_functions()
        cls.format_memories = staticmethod(formatter)
        cls.build_input = staticmethod(builder)
        cls.profile_formatter = staticmethod(profile_formatter)
        cls.schedule_constraint = staticmethod(schedule_constraint)
        cls.scheduled_plan = staticmethod(scheduled_plan)
        cls.plan_validator = staticmethod(validator)
        cls.current_override = staticmethod(current_override)
        cls.resolve_context = staticmethod(resolve_context)
        cls.format_context = staticmethod(format_context)
        cls.profile = {
            "weekly_weight_kg": [79.8],
            "height_cm": 184,
            "goal": "减脂",
            "equipment": ["哑铃", "哑铃凳"],
        }

    def test_build_input_reuses_resolved_plan_context(self) -> None:
        resolved = self.resolve_context("今天练腿", self.profile, [])
        resolved["scheduled_subject"] = "预解析科目"
        result = self.build_input(
            "这条消息若重算会改变上下文",
            self.profile,
            [],
            [],
            plan_context=resolved,
        )
        self.assertIn("预解析科目", result)
    def test_includes_cross_session_memory_and_current_message_precedence(self) -> None:
        result = self.build_input(
            "今天改练腿",
            self.profile,
            [],
            [{"summary": "用户上次计划今天练背"}],
        )

        self.assertIn("【跨会话记忆（历史摘要，仅供参考）】", result)
        self.assertIn("用户上次计划今天练背", result)
        self.assertIn("若与当前用户消息或当前档案冲突，以当前信息为准", result)
        self.assertTrue(result.endswith("【最新用户消息】：今天改练腿"))

    def test_goal_remains_primary_when_new_constraints_are_added(self) -> None:
        result = self.build_input(
            "以后练胸的强度低一些，我有盆骨前倾且久坐",
            self.profile,
            [],
            [{
                "user_confirmed": True,
                "facts": [
                    {"namespace": "plan", "key": "preference", "value": "胸部训练低强度"},
                    {"namespace": "health", "key": "injury_or_constraint", "value": "骨盆前倾"},
                ],
            }],
        )
        self.assertIn("当前主目标：减脂", result)
        self.assertIn("不得覆盖、取消或替代当前主目标", result)
        self.assertIn("计划偏好：胸部训练低强度", result)
        self.assertIn("健康限制：骨盆前倾", result)

    def test_filters_invalid_items_and_bounds_memory_context(self) -> None:
        memories = [
            {"summary": f"memory-{index}-" + ("x" * 600)}
            for index in range(7)
        ]
        memories.insert(1, {"summary": None})
        memories.insert(2, "invalid")

        result = self.format_memories(memories)

        self.assertNotIn("memory-5-", result)
        self.assertNotIn("memory-6-", result)
        summary_lines = [line for line in result.splitlines() if line.startswith("- ")]
        self.assertLessEqual(len(summary_lines), 5)
        self.assertLessEqual(sum(len(line) - 2 for line in summary_lines), 2000)

    def test_omits_empty_memory_section(self) -> None:
        result = self.build_input("你好", self.profile, [], [{"summary": "  "}])
        self.assertNotIn("跨会话记忆", result)

    def test_places_confirmed_facts_in_dedicated_context_section(self) -> None:
        result = self.format_memories([{
            "user_confirmed": True,
            "facts": [{"namespace": "youtube", "key": "avoid_channel", "value": "Jeff Nippard"}],
            "summary": "用户不喜欢 Jeff Nippard",
        }])
        self.assertIn("【已确认约束与偏好】", result)
        self.assertIn("不优先推荐频道：Jeff Nippard", result)

    def test_profile_projection_includes_confirmed_preferences(self) -> None:
        result = self.profile_formatter([{
            "user_confirmed": True,
            "facts": [{"namespace": "plan", "key": "preference", "value": "胸部训练低强度"}],
        }])
        self.assertIn("计划偏好：胸部训练低强度", result)

    def test_plan_context_marks_weekly_schedule_as_suppressed_for_today(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule", "value": '{"tue":"背部训练"}',
        }]}]
        context = self.resolve_context("今天是2026-08-18，我今天不想练背", self.profile, memories)
        self.assertEqual(context["decision"], "override_today")
        self.assertEqual(context["scheduled_subject"], "背部训练")
        self.assertTrue(context["suppressed_rules"])
        self.assertTrue(context["clarification_required"])

    def test_plan_context_accepts_explicit_replacement(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule", "value": '{"tue":"背部训练"}',
        }]}]
        context = self.resolve_context("今天是2026-08-18，今天改练腿", self.profile, memories)
        self.assertEqual(context["effective_subject"], "腿")
        self.assertFalse(context["clarification_required"])

    def test_rest_day_becomes_a_rest_decision(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule",
            "value": '{"days":{"tue":{"type":"rest"}}}',
        }]}]
        context = self.resolve_context("今天是2026-08-18，生成今天计划", self.profile, memories)
        self.assertEqual(context["decision"], "rest_today")
        self.assertEqual(context["scheduled_subject"], "休息日")
    def test_confirmed_weekly_schedule_constrains_requested_date(self) -> None:
        memories = [{
            "user_confirmed": True,
            "facts": [{
                "namespace": "plan", "key": "weekly_schedule",
                "value": '{"mon":"胸部训练","tue":"背部训练"}',
            }],
        }]
        result = self.schedule_constraint("今天是2026-08-18，给我生成今天计划", memories)
        self.assertIn("当天训练科目必须为：背部训练", result)
        self.assertIn("不得生成全身计划", result)

    def test_generated_plan_validator_enforces_confirmed_constraints(self) -> None:
        memories = [{"user_confirmed": True, "facts": [
            {"namespace":"training", "key":"avoid_exercise", "value":"跳绳", "user_confirmed":True},
            {"namespace":"training", "key":"max_rpe", "value":7, "user_confirmed":True},
            {"namespace":"training", "key":"required_plan_elements", "value":"髋屈肌拉伸|臀桥", "user_confirmed":True},
            {"namespace":"youtube", "key":"avoid_channel", "value":"Bad Channel", "user_confirmed":True},
        ]}]
        violations = self.plan_validator(
            "胸部训练：跳绳热身，卧推 RPE 9。视频：Bad Channel",
            memories,
            "胸部训练",
        )
        self.assertTrue(any("禁止动作" in item for item in violations))
        self.assertTrue(any("RPE" in item for item in violations))
        self.assertTrue(any("YouTube" in item for item in violations))
    def test_generated_plan_validator_checks_high_end_of_rpe_range(self) -> None:
        memories = [{"user_confirmed": True, "facts": [
            {"namespace": "training", "key": "max_rpe", "value": 7},
        ]}]
        violations = self.plan_validator("胸部训练，哑铃卧推 RPE 6-8。", memories, "胸部训练")
        self.assertTrue(any("RPE 8" in item for item in violations))

    def test_posture_constraint_requires_targeted_warmup(self) -> None:
        memories = [{"user_confirmed": True, "facts": [
            {"namespace": "training", "key": "required_plan_elements", "value": "髋屈肌拉伸|臀桥"},
        ]}]
        violations = self.plan_validator("背部训练。普通热身 10 分钟，然后划船。", memories, "背部训练")
        self.assertTrue(any("必需元素" in item for item in violations))
        compliant = self.plan_validator(
            "背部训练。热身包含髋屈肌拉伸和臀桥，然后划船 RPE 7。",
            memories,
            "背部训练",
        )
        self.assertFalse(compliant)

    def test_subject_comparison_uses_core_subject_name(self) -> None:
        self.assertFalse(self.plan_validator("今天进行胸部主项，包含哑铃卧推。", [], "胸部训练"))
        violations = self.plan_validator("今天进行腿部训练，包含深蹲。", [], "背部训练")
        self.assertTrue(any("周计划科目" in item for item in violations))

    def test_schedule_details_in_parentheses_are_not_subject_terms(self) -> None:
        violations = self.plan_validator(
            "今天进行腿部训练，主项为高脚杯深蹲，随后完成臀桥。",
            [],
            "腿（高脚杯深蹲+臀桥+髋屈肌拉伸）",
        )
        self.assertFalse(violations)

    def test_generated_plan_validator_enforces_configured_set_limits(self) -> None:
        memories = [{"user_confirmed": True, "facts": [
            {"namespace": "training", "key": "max_sets_per_exercise", "value": 3},
            {"namespace": "training", "key": "max_total_sets", "value": 5},
            {"namespace": "plan", "key": "preference", "value": "胸部训练低强度"},
        ]}]
        violations = self.plan_validator(
            "胸部训练：哑铃卧推：4组，哑铃飞鸟：3组，RPE 8。",
            memories,
            "胸部训练",
        )
        self.assertTrue(any("单个动作组数" in item for item in violations))
        self.assertTrue(any("训练总组数" in item for item in violations))

    def test_validator_does_not_apply_removed_or_unconfirmed_preferences(self) -> None:
        memories = [
            {"user_confirmed": False, "facts": [
                {"namespace": "training", "key": "max_rpe", "value": 6},
                {"namespace": "training", "key": "required_plan_elements", "value": "髋屈肌拉伸|臀桥"},
            ]},
            {"user_confirmed": True, "facts": [
                {"namespace": "training", "key": "avoid_exercise", "value": "深蹲", "status": "cleared"},
            ]},
        ]
        violations = self.plan_validator("腿部训练：深蹲 4 组，RPE 9，普通热身。", memories, "腿部训练")
        self.assertFalse(violations)

    def test_qualitative_low_intensity_does_not_invent_numeric_limit(self) -> None:
        memories = [{"user_confirmed": True, "facts": [
            {"namespace": "plan", "key": "preference", "value": "胸部训练低强度"},
        ]}]
        violations = self.plan_validator("胸部训练：哑铃卧推 3 组。", memories, "胸部训练")
        self.assertFalse(violations)

    def test_router_override_replaces_the_scheduled_subject(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule",
            "value": '{"wed":"背部训练"}',
        }]}]
        context = self.resolve_context(
            "2026-08-19 把练背挪到练腿",
            self.profile,
            memories,
            routed_intent={
                "schedule_decision": "override",
                "subject": "腿部训练",
                "excluded_subjects": ["背部训练"],
            },
        )
        self.assertEqual(context["decision"], "override_today")
        self.assertEqual(context["effective_subject"], "腿部训练")
        self.assertIn("背部训练", context["excluded_subjects"])
        self.assertFalse(context["clarification_required"])

    def test_router_rest_avoids_a_spurious_clarification(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule",
            "value": '{"wed":"背部训练"}',
        }]}]
        context = self.resolve_context(
            "2026-08-19 今天不太想练背",
            self.profile,
            memories,
            routed_intent={"schedule_decision": "rest", "subject": "休息"},
        )
        self.assertEqual(context["decision"], "rest_today")
        self.assertFalse(context["clarification_required"])

    def test_router_decision_is_not_overridden_by_the_legacy_heuristic(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule",
            "value": '{"wed":"背部训练"}',
        }]}]
        context = self.resolve_context(
            "2026-08-19 今天不想练背",
            self.profile,
            memories,
            routed_intent={"schedule_decision": "follow", "subject": "背部训练"},
        )
        self.assertEqual(context["decision"], "follow_schedule")
        self.assertFalse(context["clarification_required"])

    def test_explicit_current_subject_overrides_a_misclassified_follow_decision(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule",
            "value": '{"fri":"背部训练"}',
        }]}]
        for message, subject in (
            ("今天想练腿", "腿部训练"),
            ("今天练腿", "腿部训练"),
            ("今天想有氧", "有氧训练"),
        ):
            with self.subTest(message=message):
                context = self.resolve_context(
                    message,
                    self.profile,
                    memories,
                    routed_intent={
                        "schedule_decision": "follow",
                        "subject": subject,
                    },
                )
                self.assertEqual(context["decision"], "override_today")
                self.assertEqual(context["effective_subject"], subject)

    def test_router_temporary_constraint_is_plan_context_only(self) -> None:
        context = self.resolve_context(
            "帮我安排今天的训练",
            self.profile,
            [],
            routed_intent={
                "schedule_decision": "follow",
                "temporary_health_constraints": ["今天腰部不适"],
            },
        )
        self.assertIn("今天腰部不适", context["active_safety_constraints"])
    def test_priority_context_orders_safety_current_instruction_schedule_goal(self) -> None:
        memories = [{"user_confirmed": True, "facts": [
            {"namespace": "health", "key": "injury_or_constraint", "value": "腰部不适"},
            {"namespace": "plan", "key": "weekly_schedule", "value": '{"tue":"背部训练"}'},
            {"namespace": "plan", "key": "preference", "value": "胸部训练低强度"},
        ]}]
        message = "今天不想练背，腰部不适"
        # BUG-03：六级阶梯以前写在从未被调用的 training_priority_context 里。
        # 现在它必须来自 format_plan_context —— 也就是真正接进 build_agent_input
        # 的那一段，否则测试通过而模型什么也看不到。
        context = self.format_context(self.resolve_context(message, {"goal": "减脂"}, memories))
        self.assertLess(context.index("安全/伤病限制"), context.index("用户当前明确指令"))
        self.assertLess(context.index("用户当前明确指令"), context.index("已确认周计划"))
        self.assertLess(context.index("已确认周计划"), context.index("健身目标"))
        self.assertIn("当前消息可能覆盖当天安排", context)
        self.assertTrue(self.current_override("今天不想练背"))
        self.assertFalse(self.current_override("我不太喜欢背部训练动作"))

    def test_priority_ladder_actually_reaches_the_agent_input(self) -> None:
        """BUG-03 的核心断言：阶梯必须真的出现在送进模型的字符串里。"""
        memories = [{"user_confirmed": True, "facts": [
            {"namespace": "plan", "key": "weekly_schedule", "value": '{"tue":"背部训练"}'},
        ]}]
        result = self.build_input("今天有点累", self.profile, [], memories)
        self.assertIn("【后端 PlanContext（已裁决优先级，必须遵守）】", result)
        # 这条语义澄清是整个阶梯里最关键的一句：以前它只存在于死代码中，
        # 于是"今天有点累"这类普通抱怨可能被当成覆盖周计划的明确指令。
        self.assertIn("普通抱怨或提问不算", result)
        self.assertIn("冲突时按上述顺序决策", result)
        for level in (
            "1. 安全/伤病限制",
            "3. 肌群恢复状态",
            "4. 已确认周计划",
            "7. 历史摘要",
        ):
            self.assertIn(level, result)
        # 普通抱怨不该被判成覆盖
        self.assertFalse(self.current_override("今天有点累"))
        self.assertNotIn("当前消息可能覆盖当天安排", result)

    def test_priority_section_appears_exactly_once(self) -> None:
        """BUG-04 的教训：同一件事两处实现，必有一处失效。"""
        result = self.build_input("生成今天的训练计划", self.profile, [], [])
        self.assertEqual(result.count("优先级由高到低"), 1)
        # 合并前这里还有一段独立的【训练设计优先级】
        self.assertNotIn("【训练设计优先级】", result)
        self.assertNotIn("【后端统一优先级", result)
    def test_scheduled_plan_returns_subject_and_iso_date_for_artifact(self) -> None:
        memories = [{"user_confirmed": True, "facts": [{
            "namespace": "plan", "key": "weekly_schedule",
            "value": '{"tue":"背部训练"}',
        }]}]
        scheduled = self.scheduled_plan("今天是2026-08-18，生成今天计划", memories)
        self.assertEqual(scheduled[0].isoformat(), "2026-08-18")
        self.assertEqual(scheduled[1], "背部训练")


if __name__ == "__main__":
    unittest.main()
