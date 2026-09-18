"""确定性闸门的 trace 覆盖（agent-trace 阶段 3）。

## 这一阶段解决什么

本项目的回答**主要由确定性闸门决定**：健康风险筛查、酸痛入库、周计划决策、
计划校验、自动修正、记忆候选写入。阶段 0-2 之后 trace 已经有回合边界，但"为什么
给了这个回答"仍然读不出来——闸门一行都没记。

## 两层防线

1. **枚举层**（AST）：`chat()` 里所有返回都必须走 `_chat_response` 这唯一出口，
   而出口无条件记一条 `gate/chat_response`。所以"新加的分支自动被覆盖"不是靠人
   记得补，而是靠这条结构断言。同时把 `source` 全枚举冻住，加分支时必须显式过一遍。
2. **行为层**（真实 `/chat`）：三条典型路径（急症拦截 / 安全冲突 409 / 自动修正
   通过）逐条验证 gate 序列，外加若干 `source` 的运行时覆盖。

## 为什么不给全部 17 个 source 各写一条运行时用例

记录点是**所有分支共用的一行**，逐个 source 跑一遍验证的是同一行代码；而每个
source 需要自己的 store fixture（有些还要伪造周计划和已确认记忆）。所以这里用
"枚举层冻住全集 + 行为层覆盖各个类别"，而不是 17 条近乎重复的用例。
"""

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import main
from fithealth_agent.chat_intent_router import ChatIntent
from fithealth_agent.observability import config as trace_config
from fithealth_agent.observability import sink as trace_sink
from fithealth_agent.observability import trace as trace_mod
from fithealth_agent.runtime import deps
from fithealth_agent.soreness_store import SorenessStore
from fithealth_agent.workflows import chat_workflow

from tests import module_map
from tests.source_tools import module_tree
from tests.test_trace_infrastructure import event_of, read_turn


#: `chat()` 能给出的全部 `source`。新增一个提前返回分支时这里必须同步——那正是
#: "过一遍隐私与可观测性"的时机，而不是让新分支静默出现在 trace 里。
EXPECTED_SOURCES = {
    "agent",
    "context_budget",
    "external_models_disabled",
    "health_risk_block",
    "local_nutrition_records",
    "local_plan_save",
    "local_profile",
    "local_soreness_feedback",
    "local_training_records",
    "local_weekly_summary",
    "memory_confirmation",
    "onboarding",
    "plan_context_clarification",
    "plan_context_rest",
    "plan_context_safety_block",
    "profile_update",
    "soreness_clarification",
}

#: 一份能通过 `looks_like_complete_training_plan` 的正文：≥300 字、≥2 个动作词、
#: ≥2 个结构词（热身 / 组 / 次数 / 拉伸 / 训练计划）。用真实谓词而不是打桩它，
#: 是为了让"自动修正通过"这条路径真的走完整条链。
COMPLETE_PLAN = (
    "# 上肢训练计划\n\n"
    "## 热身\n动态热身 8 分钟，肩关节绕环与弹力带激活，逐步提高心率。\n\n"
    "## 主要动作\n"
    "1. 杠铃弯举 4 组，每组 10 次数，组间休息 90 秒，注意肘部固定不摆动。\n"
    "2. 绳索三头下压 4 组，每组 12 次数，组间休息 60 秒，全程保持上臂贴近躯干。\n"
    "3. 法式推举 3 组，每组 10 次数，组间休息 90 秒，下放阶段控制两秒。\n"
    "4. 窄距卧推 3 组，每组 8 次数，组间休息 120 秒，握距略窄于肩宽。\n\n"
    "## 拉伸\n训练后做三头与肱二头静态拉伸各 30 秒，配合胸小肌放松。\n\n"
    "## 备注\n若任何动作出现关节疼痛立即停止，下次训练把重量下调一成再逐步加回。\n"
)


@contextmanager
def traced_trace_dir():
    """开启 trace 并把目录指向临时目录。

    只动 `FITHEALTH_TRACE*`：store 的位置由进程级临时数据目录决定，换它会碰上
    "谁先 import 谁定下数据目录"那个坑。
    """
    names = ("FITHEALTH_TRACE", "FITHEALTH_TRACE_DIR", "FITHEALTH_TRACE_DETAIL")
    previous = {name: os.environ.get(name) for name in names}
    with tempfile.TemporaryDirectory(prefix="fithealth-trace-gate-") as temp:
        root = Path(temp)
        os.environ["FITHEALTH_TRACE"] = "on"
        os.environ["FITHEALTH_TRACE_DIR"] = str(root / "traces")
        os.environ.pop("FITHEALTH_TRACE_DETAIL", None)
        trace_sink.reset_writable_cache()
        trace_config._warned.clear()
        try:
            yield root
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            trace_sink.reset_writable_cache()
            trace_config._warned.clear()


def gate_pairs(events: list[dict]) -> list[tuple[str, object]]:
    """按 seq 顺序列出 (span, outcome)，只看闸门与 store 写入。"""
    return [
        (event["span"], event["payload"].get("outcome"))
        for event in sorted(events, key=lambda item: item["seq"])
        if event["kind"] in {"gate", "store_write"}
    ]


def gate_payload(events: list[dict], span: str) -> dict:
    return next(
        event["payload"]
        for event in sorted(events, key=lambda item: item["seq"])
        if event["kind"] in {"gate", "store_write"} and event["span"] == span
    )


def _chat_function() -> ast.AsyncFunctionDef:
    tree = module_tree(module_map.CHAT_WORKFLOW)
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "chat"
    )


def declared_sources() -> set[str]:
    """`chat()` 里能出现的 `source` 全集。

    两个来源：直接写成 `source="..."` 的字面量，以及先赋给 `pending_view_source`
    再传进去的那三个（`source=pending_view_source or "agent"`）。
    """
    function = _chat_function()
    found: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.keyword) and node.arg == "source":
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                found.add(node.value.value)
            # `pending_view_source or "agent"` 这种兜底写法里的常量也要算上
            elif isinstance(node.value, ast.BoolOp):
                found.update(
                    item.value
                    for item in node.value.values
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pending_view_source"
            for target in node.targets
        ):
            for item in ast.walk(node.value):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    found.add(item.value)
    return found


def returns_bypassing_the_single_exit() -> list[int]:
    """`chat()` 里没有走 `_chat_response` 的 return 行号。

    `_chat_response` 自己那条 `return ChatResult(...)` 不算——它就是那个出口。
    """
    function = _chat_function()
    exit_function = next(
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_chat_response"
    )
    inside_exit = {node for node in ast.walk(exit_function)}
    offenders: list[int] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Return) or node.value is None or node in inside_exit:
            continue
        call = node.value
        name = None
        if isinstance(call, ast.Call):
            name = (
                call.func.id
                if isinstance(call.func, ast.Name)
                else getattr(call.func, "attr", None)
            )
        if name != "_chat_response":
            offenders.append(node.lineno)
    return sorted(offenders)


class SingleExitInvariantTest(unittest.TestCase):
    """枚举层：出口唯一 + source 全集冻住。"""

    def test_all_returns_go_through_the_single_exit(self) -> None:
        """这条断言是"新分支自动被 trace 覆盖"的**前提**。

        它抓到过一个真问题：原先 `build_agent_input` 抛 `ContextInputError` 时
        `return context_error_response(exc)` 绕过了出口，而那个函数在本模块根本
        没导入——NameError 被外层 `except Exception` 吞掉，用户拿到的是一句无用的
        500，而不是"上下文过长"这条可操作提示。
        """
        self.assertEqual(returns_bypassing_the_single_exit(), [])

    def test_the_declared_source_set_is_frozen(self) -> None:
        self.assertEqual(sorted(declared_sources()), sorted(EXPECTED_SOURCES))

    def test_the_single_exit_records_a_gate_event(self) -> None:
        """出口里必须真的有 `trace_event`，否则上面两条断言都是空转。"""
        function = _chat_function()
        exit_function = next(
            node
            for node in ast.walk(function)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_chat_response"
        )
        spans = [
            node.args[1].value
            for node in ast.walk(exit_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "trace_event"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ]
        self.assertEqual(spans, ["chat_response"])


class _FakeAgent:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def run(self, _text: str) -> str:
        return self.answer


def _empty_info_store() -> mock.Mock:
    store = mock.Mock()
    store.get_context_memories.return_value = []
    store.get_all.return_value = []
    store.get_enforceable_memories.return_value = []
    store.cleanup_expired.return_value = 0
    return store


class GateSequenceTest(unittest.TestCase):
    """行为层：三条典型路径的闸门序列。"""

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.profile = {
            "height_cm": 175, "birth_date": "1995-01-01", "gender": "male",
            "goal": "增肌", "equipment": ["哑铃"], "weekly_weight_kg": [70],
        }

    @contextmanager
    def _common_stubs(self, **extra):
        # 用临时目录里的真实 SorenessStore，而不是共享的进程级 store：这些用例会
        # 真的写酸痛记录，落在共享目录里会污染别的用例。
        with tempfile.TemporaryDirectory(prefix="fithealth-gate-store-") as directory:
            with (
                mock.patch.object(
                    deps, "soreness_store", SorenessStore(Path(directory) / "muscle_soreness.json")
                ),
                mock.patch.object(deps, "info_store", _empty_info_store()),
                mock.patch.object(deps, "classify_user_health_statement", return_value=True),
                mock.patch.object(deps.profile_store, "get_profile", return_value=self.profile),
                mock.patch.object(deps.profile_store, "is_complete", return_value=True),
                mock.patch.object(deps.profile_store, "missing_fields", return_value=[]),
                mock.patch.object(deps, "build_current_week_reply", return_value=None),
            ):
                yield

    def _post(self, message: str) -> tuple[dict, list[dict]]:
        with traced_trace_dir() as root:
            response = self.client.post(
                "/chat",
                json={"message": message, "history": [], "source": "chat"},
            )
            events = read_turn(root)
        return response.json() | {"_status": response.status_code}, events

    def test_emergency_signal_blocks_and_records_the_risk_gate(self) -> None:
        with self._common_stubs():
            body, events = self._post("胸痛得很厉害，喘不上气")

        self.assertEqual([body["_status"], body["source"]], [200, "health_risk_block"])
        risk = gate_payload(events, "health_risk")
        self.assertEqual([risk["outcome"], risk["level"]], ["emergency", "emergency"])
        # 顺序本身就是结论：先把"胸痛"解析成 painful 部位，acute_pain_finding 再把它
        # 并进风险，最后才拦下回答。闸门顺序反了，这条因果链就说不出来。
        self.assertEqual(
            [span for span, _ in gate_pairs(events)],
            ["soreness", "health_risk", "chat_response"],
        )
        self.assertEqual(gate_payload(events, "chat_response")["source"], "health_risk_block")
        self.assertGreater(risk["painful_count"], 0)
        self.assertEqual(gate_payload(events, "soreness")["prompted"], False)

    def test_safety_conflict_records_the_plan_context_gate(self) -> None:
        """安全冲突 409：闸门里要能看出**是哪条限制**拦下的（meta 级别只有摘要）。"""
        blocked = {
            "decision": "override_today",
            "effective_subject": "腿部训练",
            "blocking_reasons": ["已确认的膝伤限制禁止下肢负重"],
            "active_safety_constraints": ["膝关节术后禁止深蹲"],
            "workflow_state": "CONSTRAINT_CONFLICT",
            "muscle_recovery": {},
        }
        intent = ChatIntent(
            create_training_plan=True,
            training_plan_subject="腿部",
            training_plan_title="腿部训练",
        )
        with self._common_stubs(), (
            mock.patch.object(deps, "route_chat_intent", return_value=intent)
        ), mock.patch.object(chat_workflow, "resolve_plan_context", return_value=blocked):
            body, events = self._post("今天帮我练腿")

        self.assertEqual([body["_status"], body["source"]], [409, "plan_context_safety_block"])
        context = gate_payload(events, "plan_context")
        self.assertEqual(
            [context["outcome"], context["decision"], context["clarification_required"]],
            ["override_today", "override_today", False],
        )
        # 约束原文是健康信息：meta 级别只落条数与 HMAC 摘要，原文不落盘。
        self.assertEqual(context["blocking_reasons_count"], 1)
        self.assertNotIn("blocking_reasons", context)
        self.assertNotIn("active_safety_constraints", context)
        self.assertEqual(
            [span for span, _ in gate_pairs(events)][-2:], ["plan_context", "chat_response"]
        )

    def test_auto_correction_pass_records_both_violation_sets(self) -> None:
        """自动修正通过：first/final 两组违规都要留着，才看得出修好了哪几条。"""
        intent = ChatIntent(
            create_training_plan=True,
            training_plan_subject="上肢",
            training_plan_title="上肢训练",
        )
        resolved = {
            "decision": "override_today",
            "effective_subject": "上肢训练",
            "blocking_reasons": [],
            "active_safety_constraints": [],
            "workflow_state": "PLAN_READY",
            "muscle_recovery": {},
        }
        with self._common_stubs(), (
            mock.patch.object(deps, "route_chat_intent", return_value=intent)
        ), mock.patch.object(
            chat_workflow, "resolve_plan_context", return_value=resolved
        ), mock.patch.object(
            deps, "create_fithealth_agent", return_value=_FakeAgent(COMPLETE_PLAN)
        ), mock.patch.object(
            deps,
            "validate_plan_goal_alignment",
            return_value={"passed": True, "matched_subjects": ["上肢训练"],
                          "missing_subjects": [], "reason": "", "stage": "lite_llm"},
        ), mock.patch.object(
            chat_workflow,
            "validate_generated_training_plan",
            side_effect=[["组数低于恢复要求"], []],
        ):
            body, events = self._post("帮我生成一份上肢训练计划")

        self.assertEqual(body["_status"], 200)
        validation = gate_payload(events, "plan_validation")
        self.assertEqual(
            [validation["outcome"], validation["violation_count"], validation["alignment_stage"]],
            ["failed", 1, "lite_llm"],
        )
        correction = gate_payload(events, "auto_correction")
        self.assertEqual(
            [correction["outcome"], correction["passed"], correction["stop_reason"],
             correction["attempt"], correction["corrected_is_complete_plan"]],
            ["passed", True, "passed", 1, True],
        )
        self.assertEqual(
            [correction["first_violations_count"], correction["final_violations_count"]], [1, 0]
        )
        self.assertEqual(
            [span for span, _ in gate_pairs(events)],
            # `context_budget` 从阶段 5 起两种结局都记（accepted / rejected），并带各段
            # 长度——上下文是怎么拼出来的，与"拼出来之后校验通不通过"是两件事，
            # 顺序上前者必然在前。
            ["health_risk", "plan_context", "context_budget", "plan_validation",
             "auto_correction", "chat_response"],
        )

    def test_representative_sources_reach_the_single_exit(self) -> None:
        """各类别的运行时覆盖：本地短路、离线拒绝、档案未完成、澄清追问。"""
        offline = {"external_models_enabled": False}
        cases = {
            "external_models_disabled": (
                "帮我生成一份训练计划",
                lambda: mock.patch.object(
                    deps.external_model_settings_store, "get", return_value=offline
                ),
            ),
            "onboarding": (
                "帮我生成一份训练计划",
                lambda: mock.patch.object(deps.profile_store, "is_complete", return_value=False),
            ),
            "local_training_records": (
                "查看训练记录",
                lambda: mock.patch.object(
                    deps.external_model_settings_store, "get", return_value=offline
                ),
            ),
        }
        for expected_source, (message, extra) in cases.items():
            with self.subTest(source=expected_source):
                with self._common_stubs(), extra():
                    body, events = self._post(message)
                self.assertEqual(body["source"], expected_source, body)
                exit_gate = gate_payload(events, "chat_response")
                self.assertEqual(exit_gate["source"], expected_source)
                # 出口无条件记这四个字段，是"能不能按 source 检索"的基础。
                self.assertEqual(
                    sorted({"source", "status_code", "reply_len", "soreness_saved"} - set(exit_gate)),
                    [],
                )

    def test_acute_injury_candidate_is_recorded_as_a_store_write(self) -> None:
        """急性伤病候选会写进记忆库。写入是状态变更，必须留痕。"""
        info = _empty_info_store()
        info.add_entry.return_value = {
            "id": "entry-1",
            "summary": "用户报告急性伤病信号",
            "facts": [{"namespace": "health", "key": "recovery_status", "value": "膝部：疼痛"}],
        }
        with self._common_stubs(), mock.patch.object(deps, "info_store", info):
            _body, events = self._post("膝盖疼得走不了路")

        write = gate_payload(events, "info_store")
        self.assertEqual(
            [write["outcome"], write["ok"], write["capture"], write["user_confirmed"]],
            ["pending_confirmation", True, "chat_safety", False],
        )
        self.assertEqual([write["namespaces"], write["keys"]], [["health"], ["recovery_status"]])
        self.assertEqual(write["entry_id"], "entry-1")


def _stable_body(body: dict) -> dict:
    """去掉每次请求都会变的字段，再比响应是否一致。

    酸痛记录的 `id` 是随机 UUID，`reported_at` / `expires_at` 是当前时间——它们本来
    就每次不同，与 trace 无关。把它们剥掉，剩下的必须逐字节相同。
    """
    volatile = {"id", "reported_at", "expires_at"}
    stable = dict(body)
    stable["soreness_reports"] = [
        {name: value for name, value in report.items() if name not in volatile}
        for report in body.get("soreness_reports") or []
    ]
    return stable


class TraceFailureIsolationTest(unittest.TestCase):
    """trace 出问题绝不能改变响应——这是 observability 的硬承诺。"""

    def test_a_broken_gate_recorder_does_not_change_the_response(self) -> None:
        real_add = trace_mod.TurnTrace.add

        def explode(self, kind, span="", **payload):
            if kind == "gate":
                raise RuntimeError("payload 裁剪炸了")
            return real_add(self, kind, span, **payload)

        client = TestClient(main.app)
        payload = {"message": "胸痛得很厉害，喘不上气", "history": [], "source": "chat"}
        with tempfile.TemporaryDirectory(prefix="fithealth-gate-iso-") as directory, (
            mock.patch.object(
                deps, "soreness_store", SorenessStore(Path(directory) / "s.json")
            )
        ), mock.patch.object(deps, "info_store", _empty_info_store()), mock.patch.object(
            deps, "classify_user_health_statement", return_value=True
        ):
            with traced_trace_dir() as root:
                healthy = client.post("/chat", json=payload)
            with traced_trace_dir() as root, mock.patch.object(
                trace_mod.TurnTrace, "add", explode
            ):
                broken = client.post("/chat", json=payload)
                events = read_turn(root)

        self.assertEqual(broken.status_code, healthy.status_code)
        self.assertEqual(_stable_body(broken.json()), _stable_body(healthy.json()))
        # 降级必须**可见**：turn_end 里的计数就是那条可见性。
        self.assertEqual([event["kind"] for event in events].count("gate"), 0)
        self.assertGreater(event_of(events, "turn_end")["payload"]["trace_errors"], 0)


if __name__ == "__main__":
    unittest.main()





