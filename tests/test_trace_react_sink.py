"""ReAct 内部事件的 sink 适配（agent-trace 阶段 5）。

## 这一阶段解决什么

阶段 0 把框架那套文件式 `TraceLogger` 关掉之后，ReAct 循环内部重新变成黑盒：模型
第几步调了什么工具、工具返回了多少字节、这一轮花了多少 token，trace 里一个字都没有。
阶段 5 装一个**同接口但不落盘**的 sink 把这些事件引进当前回合，于是主循环与自动修正
循环共享同一个 `turn_id`（TRACE-02），并补上可复现字段（TRACE-05）与用量汇总
（TRACE-04）。

## 四条不变量

1. **框架事件全都有归属**。`FrameworkContractTest` 静态扫描框架源码里所有
   `self.trace_logger.log_event("…")`，任何一个既不在 `TRANSLATED_EVENTS`、也不在
   `IGNORED_EVENTS`、也不在本文件那份 `KNOWN_UNMAPPED` 里的事件都会让用例先红——
   框架升级不会静默丢掉一类事件。
2. **翻译是白名单**。框架 payload 里的异常 `message`、硬编码的 `cost: 0.0` 都不进
   trace；0 token 记成"不知道"而不是"免费"。
3. **`finalize()` 严格空操作**。TRACE-07（构造即开句柄、只在特定返回路径 finalize）
   在阶段 0 已被消除，这里必须保证不重新引入。
4. **两个循环一个回合**。`role` 是唯一的区分手段，也是事件的 span。
"""

from __future__ import annotations

import ast
import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import main
from fithealth_agent.chat_intent_router import ChatIntent
from fithealth_agent.observability import (
    AGENT_SPANS,
    EVENT_SPECS,
    IGNORED_EVENTS,
    TRANSLATED_EVENTS,
    UNMAPPED_KIND,
    TurnTraceSink,
    attach_react_trace,
    current_turn,
    set_turn_result,
    start_turn,
    summarise_usage,
)
from fithealth_agent.observability import react_trace
from fithealth_agent.prompts import SYSTEM_PROMPT
from fithealth_agent.runtime import deps
from fithealth_agent.soreness_store import SorenessStore
from fithealth_agent.workflows import chat_workflow

from tests.test_trace_decision_coverage import (
    COMPLETE_PLAN,
    _empty_info_store,
    gate_payload,
    traced_trace_dir,
)
from tests.test_trace_infrastructure import read_turn, trace_env, turn_blob


FRAMEWORK_DIR = Path(
    (importlib.util.find_spec("hello_agents").submodule_search_locations or [""])[0]
)

#: 框架会发、但只出现在**异步**路径且本项目从不注册生命周期钩子的事件。它们落到
#: `react_unmapped`（记事件名、丢 payload 值）而不是被翻译：真出现了必须看得见，
#: 但它们的字段没有契约可依。列在这里而不是 `IGNORED_EVENTS`，是为了让"框架新增了
#: 一类事件"和"这两个已知项"分得开。
KNOWN_UNMAPPED = {"hook_timeout", "hook_error"}

#: 一次真实同步 run 会发出的事件序列（对照 `hello_agents/agents/react_agent.py`
#: 的 `_run_impl`）。两步、一次工具调用、一次 Finish。
STEP_TOKENS = (120, 240)


def framework_run(sink, *, answer: str = "总睡眠 7 小时 20 分钟", prompt: str = "我昨晚睡了多久？") -> None:
    """按框架同步 `run` 的真实顺序驱动一个 sink。`sink is None` 时什么都不做。"""
    if sink is None:
        return
    sink.log_event("message_written", {"role": "user", "content": prompt})
    sink.log_event(
        "model_output",
        {"content": "先查睡眠", "tool_calls": 1,
         "usage": {"total_tokens": STEP_TOKENS[0], "cost": 0.0}},
        step=1,
    )
    sink.log_event(
        "tool_call",
        {"tool_name": "query_sleep", "tool_call_id": "c1", "args": {"date": "2026-09-04"}},
        step=1,
    )
    sink.log_event(
        "tool_result",
        {"tool_name": "query_sleep", "tool_call_id": "c1", "result": "总睡眠 440 分钟"},
        step=1,
    )
    sink.log_event(
        "model_output",
        {"content": "", "tool_calls": 1,
         "usage": {"total_tokens": STEP_TOKENS[1], "cost": 0.0}},
        step=2,
    )
    sink.log_event(
        "tool_result",
        {"tool_name": "Finish", "tool_call_id": "c2", "status": "success",
         "result": f"最终答案: {answer}"},
        step=2,
    )
    sink.log_event(
        "session_end",
        {"duration": 12.5, "total_steps": 2, "final_answer": answer, "status": "success"},
    )
    sink.finalize()


def _is_self_trace_logger(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "trace_logger"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def framework_trace_logger_uses() -> tuple[set[str], set[str]]:
    """扫框架源码，返回 (`self.trace_logger` 被用到的成员, `log_event` 的事件名)。

    只认 `self.trace_logger`：`agents/simple_agent.py` 用的是自己 new 出来的局部
    `TraceLogger`，本项目的 sink 从来装不到那上面去，扫进来只会制造假需求。
    """
    members: set[str] = set()
    events: set[str] = set()
    for path in sorted(FRAMEWORK_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and _is_self_trace_logger(node.value):
                members.add(node.attr)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "log_event"
                and _is_self_trace_logger(node.func.value)
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                events.add(str(node.args[0].value))
    return members, events


def kinds_of(events: list[dict]) -> list[str]:
    return [event["kind"] for event in sorted(events, key=lambda item: item["seq"])]


def event_payload(events: list[dict], kind: str, span: str | None = None) -> dict:
    for event in sorted(events, key=lambda item: item["seq"]):
        if event["kind"] == kind and (span is None or event["span"] == span):
            return event["payload"]
    raise AssertionError(f"没有 kind={kind} span={span} 的事件；实际有 {kinds_of(events)}")


def events_of(events: list[dict], kind: str) -> list[dict]:
    return [event for event in sorted(events, key=lambda item: item["seq"]) if event["kind"] == kind]


class FrameworkContractTest(unittest.TestCase):
    """鸭子类型的最小契约必须由测试盯着，不能靠"上次看过一遍"。"""

    def test_every_framework_event_is_classified(self) -> None:
        _members, events = framework_trace_logger_uses()
        self.assertEqual(
            sorted(events),
            sorted(set(TRANSLATED_EVENTS) | set(IGNORED_EVENTS) | KNOWN_UNMAPPED),
            "框架发的事件里有一类没有归属：翻译它、列进 IGNORED_EVENTS，"
            "或者确认它该落到 react_unmapped 后加进本文件的 KNOWN_UNMAPPED",
        )

    def test_the_sink_implements_every_member_the_framework_uses(self) -> None:
        members, _events = framework_trace_logger_uses()
        missing = sorted(members - set(dir(TurnTraceSink)))
        self.assertEqual(missing, [], "框架会读这些成员，但 sink 上没有")

    def test_every_translated_kind_is_registered_in_the_schema(self) -> None:
        """span 或 kind 打错字的症状是事件带 `_unknown_*`，统计按 kind 分组就漏掉它。"""
        produced = set(TRANSLATED_EVENTS.values()) | {UNMAPPED_KIND}
        self.assertEqual(sorted(produced - set(EVENT_SPECS)), [])

    def test_each_translator_produces_the_kind_it_declares(self) -> None:
        """声明表与实现挨着写也会漂移，这里按事件逐个跑一遍验收。

        payload 用各事件的最小合法形状：翻译函数对缺字段是宽容的（框架升级可能改
        payload 形状），所以这条只关心"落出来的 kind 对不对"。
        """
        minimal = {
            "message_written": {"role": "user", "content": "x"},
            "model_output": {"content": "", "tool_calls": 0, "usage": {}},
            "error": {"error_type": "LLM_ERROR", "message": "boom"},
            "tool_call": {"tool_name": "query_sleep", "tool_call_id": "c1", "args": {}},
            "tool_result": {"tool_name": "query_sleep", "tool_call_id": "c1", "result": "ok"},
            "session_end": {"duration": 1.0, "total_steps": 1,
                            "final_answer": "a", "status": "success"},
        }
        self.assertEqual(sorted(minimal), sorted(TRANSLATED_EVENTS))
        for event, expected in sorted(TRANSLATED_EVENTS.items()):
            with self.subTest(event=event), trace_env(FITHEALTH_TRACE="on") as root:
                with start_turn("/chat"):
                    TurnTraceSink("agent").log_event(event, minimal[event], step=1)
                recorded = [
                    item["kind"] for item in read_turn(root)
                    if item["kind"] not in {"turn_start", "turn_end"}
                ]
                self.assertEqual(recorded, [expected])

    def test_the_two_roles_are_the_registered_agent_spans(self) -> None:
        self.assertEqual(sorted(AGENT_SPANS), ["agent", "correction_agent"])


class SinkTranslationTest(unittest.TestCase):
    """逐个事件的翻译结果。全部通过真实落盘读回来，不看内存里的中间态。"""

    def drive(self, feed, *, role: str = "agent", detail: str | None = None, max_steps=None):
        """在一个回合里喂事件，返回 (落盘事件列表, 落盘全文)。"""
        overrides = {"FITHEALTH_TRACE": "on"}
        if detail is not None:
            overrides["FITHEALTH_TRACE_DETAIL"] = detail
        with trace_env(**overrides) as root:
            with start_turn("/chat"):
                feed(TurnTraceSink(role, model="deepseek-chat", max_steps=max_steps))
            return read_turn(root), turn_blob(root)

    def test_a_full_run_translates_into_the_expected_kind_sequence(self) -> None:
        events, _blob = self.drive(framework_run)
        self.assertEqual(
            kinds_of(events),
            [
                "turn_start", "agent_input", "react_step", "tool_call", "tool_result",
                "react_step", "tool_result", "react_end", "turn_end",
            ],
        )
        # span 全是 role 或工具名，没有一个落在契约外。
        self.assertEqual(
            [event["span"] for event in events if event["kind"].startswith("react")],
            ["agent", "agent", "agent"],
        )
        self.assertEqual(
            [event["span"] for event in events_of(events, "tool_result")],
            ["query_sleep", "Finish"],
        )

    def test_model_output_records_step_model_and_provider_tokens(self) -> None:
        events, _blob = self.drive(framework_run)
        first = events_of(events, "react_step")[0]
        self.assertEqual(first["step"], 1)
        self.assertEqual(
            [first["payload"]["tool_calls"], first["payload"]["total_tokens"],
             first["payload"]["usage_source"], first["payload"]["model"]],
            [1, STEP_TOKENS[0], "provider", "deepseek-chat"],
        )

    def test_zero_tokens_are_unknown_not_free(self) -> None:
        """框架在 `response.usage` 为空时写 0。把它当成"零成本"正是 TRACE-04 的病根。"""
        def feed(sink):
            sink.log_event(
                "model_output",
                {"content": "", "tool_calls": 0, "usage": {"total_tokens": 0, "cost": 0.0}},
                step=1,
            )

        events, _blob = self.drive(feed)
        payload = event_payload(events, "react_step")
        self.assertEqual(payload["usage_source"], "unknown")
        self.assertNotIn("total_tokens", payload)

    def test_the_frameworks_hardcoded_cost_never_lands(self) -> None:
        events, _blob = self.drive(framework_run)
        for payload in (event["payload"] for event in events_of(events, "react_step")):
            with self.subTest(payload=payload):
                self.assertNotIn("cost", payload)
                self.assertNotIn("usage", payload)

    def test_tool_call_keeps_the_arg_keys_but_not_the_values(self) -> None:
        events, blob = self.drive(framework_run)
        payload = event_payload(events, "tool_call")
        self.assertEqual(
            [payload["role"], payload["tool_call_id"], payload["args_keys"]],
            ["agent", "c1", ["date"]],
        )
        # 参数值可能是用户写的训练内容：meta 级别只留长度与摘要。
        self.assertNotIn("args", payload)
        self.assertNotIn("2026-09-04", blob)

    def test_tool_call_args_land_in_full_mode_only(self) -> None:
        events, blob = self.drive(framework_run, detail="full")
        self.assertIn("2026-09-04", blob)
        self.assertIn("args", event_payload(events, "tool_call"))

    def test_tool_result_derives_the_status_the_framework_omits(self) -> None:
        """用户工具分支连 `status` 字段都没有，成败只在结果文本的前缀里。"""
        cases = {
            "success": "总睡眠 440 分钟",
            "error": "❌ 错误 [TOOL_ERROR]: 找不到那一天的记录",
            "partial": "⚠️ 部分成功: 只有 3 天有数据",
        }
        for expected, text in cases.items():
            with self.subTest(status=expected):
                events, _blob = self.drive(
                    lambda sink, text=text: sink.log_event(
                        "tool_result",
                        {"tool_name": "query_sleep", "tool_call_id": "c1", "result": text},
                        step=1,
                    )
                )
                payload = event_payload(events, "tool_result")
                self.assertEqual(payload["status"], expected)
                self.assertEqual(payload["result_bytes"], len(text.encode("utf-8")))

    def test_a_declared_status_wins_over_the_prefix(self) -> None:
        events, _blob = self.drive(framework_run)
        self.assertEqual(events_of(events, "tool_result")[1]["payload"]["status"], "success")

    def test_session_end_records_duration_status_and_role_tokens(self) -> None:
        events, blob = self.drive(framework_run)
        end = events_of(events, "react_end")[0]
        self.assertEqual(end["dur_ms"], 12_500)
        self.assertEqual(
            [end["payload"]["status"], end["payload"]["stop_reason"],
             end["payload"]["total_steps"], end["payload"]["max_steps_reached"],
             end["payload"]["total_tokens"], end["payload"]["usage_source"]],
            ["success", "finished", 2, False, sum(STEP_TOKENS), "provider"],
        )
        # 正常收尾没有纠正发生，所以不留 framework_status（纠正才留痕，见模块头）。
        self.assertNotIn("framework_status", end["payload"])
        # 最终答案是给用户的正文：meta 级别只有长度与摘要。
        self.assertNotIn("final_answer", end["payload"])
        self.assertNotIn("7 小时 20 分钟", blob)

    def _llm_failure(self, *, total_steps: int = 1):
        """框架在 LLM 挂掉时的真实事件序列：`error` → break → "超步数"收尾。"""
        secret_url = "https://api.example.com/v1/chat/completions?key=sk-do-not-log"

        def feed(sink):
            sink.log_event(
                "error",
                {"error_type": "LLM_ERROR", "message": f"Connection refused: {secret_url}"},
                step=total_steps,
            )
            sink.log_event(
                "session_end",
                {"duration": 3.0, "total_steps": total_steps,
                 "final_answer": "抱歉，我无法在限定步数内完成这个任务。", "status": "timeout"},
            )

        return feed, secret_url

    def test_an_llm_failure_is_a_step_with_an_error_type_and_no_message(self) -> None:
        """异常 message 里常带 base_url 与请求参数，一个字都不能记。"""
        feed, secret_url = self._llm_failure()
        events, blob = self.drive(feed, max_steps=15)
        step = event_payload(events, "react_step")
        self.assertEqual([step["error_type"], step["stop_reason"]], ["LLM_ERROR", "llm_error"])
        self.assertNotIn("message", step)
        for forbidden in (secret_url, "sk-do-not-log", "Connection refused", "api.example.com"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, blob)

    def test_an_llm_failure_is_not_recorded_as_a_step_limit(self) -> None:
        """框架把 LLM 调用失败也报成 `timeout`；这一层把它纠正回来。

        不纠正的话，"模型服务挂了"和"任务太复杂用完了 15 步"在 trace 里是同一个值，
        而这两件事的处理人完全不同（一个查服务，一个调 max_steps 或提示词）。
        """
        feed, _url = self._llm_failure()
        events, _blob = self.drive(feed, max_steps=15)
        end = event_payload(events, "react_end")
        self.assertEqual(
            [end["status"], end["stop_reason"], end["error_type"], end["max_steps_reached"]],
            ["llm_error", "llm_error", "LLM_ERROR", False],
        )
        # 纠正留痕：框架原话仍然读得到，不是被我们悄悄改掉的。
        self.assertEqual(end["framework_status"], "timeout")

    def test_a_real_step_limit_stays_a_timeout(self) -> None:
        """纠正不能反过来把真的超步数说成 LLM 故障。"""
        def feed(sink):
            for step in range(1, 16):
                sink.log_event(
                    "model_output",
                    {"content": "", "tool_calls": 1, "usage": {"total_tokens": 10, "cost": 0.0}},
                    step=step,
                )
            sink.log_event(
                "session_end",
                {"duration": 90.0, "total_steps": 15,
                 "final_answer": "抱歉，我无法在限定步数内完成这个任务。", "status": "timeout"},
            )

        events, _blob = self.drive(feed, max_steps=15)
        end = event_payload(events, "react_end")
        self.assertEqual(
            [end["status"], end["stop_reason"], end["max_steps_reached"]],
            ["timeout", "max_steps", True],
        )
        self.assertNotIn("framework_status", end)
        self.assertNotIn("error_type", end)

    def test_max_steps_reached_comes_from_the_step_count_not_the_status(self) -> None:
        """框架的 status 正是这里不可信的那一项，所以这个布尔值必须独立算。"""
        cases = {
            # (报的步数, max_steps) -> 是否真的撞上上限
            (1, 15): False,
            (15, 15): True,
            (16, 15): True,
        }
        for (steps, limit), expected in cases.items():
            with self.subTest(total_steps=steps, max_steps=limit):
                events, _blob = self.drive(
                    lambda sink, steps=steps: sink.log_event(
                        "session_end",
                        {"duration": 1.0, "total_steps": steps,
                         "final_answer": "x", "status": "timeout"},
                    ),
                    max_steps=limit,
                )
                self.assertEqual(event_payload(events, "react_end")["max_steps_reached"], expected)

    def test_without_max_steps_it_falls_back_to_the_corrected_status(self) -> None:
        """`attach_react_trace` 拿不到 `max_steps`（假 agent）时不能凭空断言。"""
        feed, _url = self._llm_failure()
        events, _blob = self.drive(feed)
        self.assertEqual(event_payload(events, "react_end")["max_steps_reached"], False)

    def test_an_unknown_framework_event_becomes_react_unmapped(self) -> None:
        """框架升级新增一类事件时不能静默丢掉，但它的 payload 没有契约可依。"""
        def feed(sink):
            sink.log_event("hook_timeout", {"event_type": "step_start", "timeout": 5.0}, step=2)

        events, blob = self.drive(feed)
        event = events_of(events, "react_unmapped")[0]
        self.assertEqual([event["span"], event["step"]], ["agent", 2])
        self.assertEqual(event["payload"]["framework_event"], "hook_timeout")
        self.assertEqual(sorted(set(event["payload"]) - {"framework_event"}), [])
        self.assertNotIn("step_start", blob)

    def test_an_ignored_event_records_nothing(self) -> None:
        events, _blob = self.drive(
            lambda sink: sink.log_event("session_start", {"agent_name": "x", "config": {}})
        )
        self.assertEqual(kinds_of(events), ["turn_start", "turn_end"])

    def test_finalize_writes_nothing_and_can_be_called_twice(self) -> None:
        """空操作在成功、异常、从未 run 三种情况下都必须成立（否则 TRACE-07 回来）。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            sink = TurnTraceSink("agent")
            self.assertIsNone(sink.finalize())
            self.assertIsNone(sink.finalize())
            self.assertEqual(sorted(path.name for path in root.rglob("*.jsonl")), [])

    def test_events_outside_a_turn_are_dropped_without_creating_files(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            framework_run(TurnTraceSink("agent"))
            self.assertEqual(sorted(path.name for path in root.rglob("*.jsonl")), [])


class SharedTurnTest(unittest.TestCase):
    """TRACE-02 的正解：两个 ReAct 循环一个回合、一个文件、靠 role 区分。"""

    def test_both_loops_land_in_one_turn_with_distinct_roles(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat") as turn:
                framework_run(TurnTraceSink("agent", model="deepseek-chat"))
                framework_run(
                    TurnTraceSink("correction_agent", model="deepseek-chat"),
                    answer="修正后的计划",
                    prompt="你刚才生成的训练计划未通过后端安全校验。",
                )
                turn_id = turn.turn_id
            # read_turn 自带"恰好一个文件"的断言：两个循环绝不能写出两个文件。
            events = read_turn(root)

        self.assertEqual({event["turn_id"] for event in events}, {turn_id})
        self.assertEqual(
            [event["span"] for event in events_of(events, "react_end")],
            ["agent", "correction_agent"],
        )
        # 顺序只由 seq 承载：主循环整段在前，修正整段在后。
        roles = [
            event["payload"].get("role") or event["span"]
            for event in sorted(events, key=lambda item: item["seq"])
            if event["kind"] in {"agent_input", "react_step", "tool_call", "tool_result", "react_end"}
        ]
        self.assertEqual(roles, ["agent"] * 7 + ["correction_agent"] * 7)

    def test_the_two_loops_keep_their_own_token_totals(self) -> None:
        """`react_end.total_tokens` 是**本 role** 的累计，两个循环不能互相污染。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                main_sink = TurnTraceSink("agent")
                framework_run(main_sink)
                correction = TurnTraceSink("correction_agent")
                correction.log_event(
                    "model_output",
                    {"content": "", "tool_calls": 1, "usage": {"total_tokens": 40, "cost": 0.0}},
                    step=1,
                )
                correction.log_event(
                    "session_end",
                    {"duration": 1.0, "total_steps": 1, "final_answer": "x", "status": "success"},
                )
            events = read_turn(root)
        totals = {
            event["span"]: event["payload"]["total_tokens"]
            for event in events_of(events, "react_end")
        }
        self.assertEqual(totals, {"agent": sum(STEP_TOKENS), "correction_agent": 40})
        # 回合级汇总从 react_step 重算，不读 react_end，所以不会翻倍。
        self.assertEqual(
            event_payload(events, "turn_end")["total_tokens"], sum(STEP_TOKENS) + 40
        )


def build_real_agent(role: str = "agent"):
    """构造一次真实 agent。

    `HelloAgentsLLM` 缺 api_key 会直接抛，所以补一个占位值——全程不发任何请求。
    `redirect_stdout` 吞掉框架注册工具时打的 emoji（Windows GBK 控制台会炸）。
    """
    from fithealth_agent.agent import create_fithealth_agent

    with mock.patch.dict(
        os.environ,
        {
            "LLM_API_KEY": "sk-react-sink-placeholder-not-a-real-key",
            "LLM_BASE_URL": "https://api.deepseek.com",
        },
    ):
        with redirect_stdout(io.StringIO()):
            return create_fithealth_agent(role=role)


class ReactInitTest(unittest.TestCase):
    """TRACE-05：拿到一份 trace 能不能知道"这次用的是哪版 prompt、哪些工具"。"""

    def test_trace_off_leaves_the_framework_logger_none(self) -> None:
        """关闭时 `agent.trace_logger` 必须留在 None：框架的 `if self.trace_logger:`
        于是短路，连 payload 字典都不构造——"关闭即零开销"在接线之后仍然成立。"""
        with trace_env():
            self.assertIsNone(build_real_agent().trace_logger)

    def test_trace_on_attaches_a_sink_that_never_touches_disk(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                agent = build_real_agent()
                self.assertIsInstance(agent.trace_logger, TurnTraceSink)
                self.assertEqual(agent.trace_logger.role, "agent")
                # 装 sink 不落盘：缓冲模式下回合结束前一个文件都不该有。
                self.assertEqual(sorted(root.rglob("*.jsonl")), [])
            events = read_turn(root)
        payload = event_payload(events, "react_init", "agent")
        self.assertEqual(
            [payload["agent_name"], payload["max_steps"], payload["tool_count"]],
            ["FitHealthAgent", 15, 14],
        )
        self.assertEqual(len(payload["tool_schema_digest"]), 12)
        # 只留主机名：完整 base_url 可能带内网拓扑与 query。
        self.assertEqual(payload["endpoint_host"], "api.deepseek.com")
        self.assertNotIn("/", payload["endpoint_host"])

    def test_react_init_carries_the_reproducibility_fields(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                agent = build_real_agent()
            events = read_turn(root)
        payload = event_payload(events, "react_init", "agent")
        missing = sorted(
            {
                "model", "endpoint_host", "temperature", "timeout_s", "max_retries",
                "max_steps",
             "tools", "tool_count", "tool_schema_digest",
             "system_prompt_sha12", "system_prompt_len", "runtime_prompt_len"}
            - set(payload)
        )
        self.assertEqual(missing, [], "少了这些字段就重放不了那次请求")
        self.assertEqual(payload["model"], agent.llm.model)
        self.assertEqual(payload["system_prompt_len"], len(SYSTEM_PROMPT))
        # 运行时提示词额外拼了时间锚点，所以更长。
        self.assertGreater(payload["runtime_prompt_len"], payload["system_prompt_len"])

    def test_the_prompt_digest_identifies_the_version_not_the_second(self) -> None:
        """摘要摘的是**静态**提示词。

        `create_fithealth_agent` 会往系统提示词尾部拼当前时刻；对整份摘要就等于每秒
        换一个值，"这次回答用的是哪版 prompt"永远答不出来。
        """
        digests = []
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                build_real_agent()
                build_real_agent(role="correction_agent")
            events = read_turn(root)
        for span in ("agent", "correction_agent"):
            digests.append(event_payload(events, "react_init", span)["system_prompt_sha12"])
        self.assertEqual(len(set(digests)), 1, "同一份提示词摘出了两个值")
        self.assertEqual(digests[0], react_trace._sha12(SYSTEM_PROMPT))

    def test_meta_level_never_writes_the_system_prompt(self) -> None:
        """提示词里有档案与记忆的渲染规则，原文只有 full 级别才落盘。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                build_real_agent()
            blob = turn_blob(root)
        windows = [
            SYSTEM_PROMPT[start:start + 30]
            for start in range(0, max(len(SYSTEM_PROMPT) - 30, 1), 30)
        ]
        leaked = [window for window in windows if window and window in blob]
        self.assertEqual(leaked, [], "系统提示词的片段出现在了 meta 级别的产物里")

    def test_full_level_records_the_prompt_for_replay(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_DETAIL="full") as root:
            with start_turn("/chat"):
                build_real_agent()
            events = read_turn(root)
        recorded = event_payload(events, "react_init", "agent")["system_prompt"]
        self.assertEqual(recorded, SYSTEM_PROMPT[: len(recorded)])


class UsageSummaryTest(unittest.TestCase):
    """TRACE-04：turn_end 的用量与成本口径。纯函数部分直接测 `summarise_usage`。"""

    STEPS = [
        {"kind": "react_step", "payload": {"total_tokens": 12_406, "model": "deepseek-chat"}},
        {"kind": "react_step", "payload": {"total_tokens": 3_474, "model": "deepseek-chat"}},
        {"kind": "model_call", "payload": {"ok": True, "model": "deepseek-chat",
                                           "total_tokens": 88, "prompt_tokens": 70,
                                           "completion_tokens": 18}},
    ]

    def test_it_sums_react_steps_and_model_calls(self) -> None:
        summary = summarise_usage(self.STEPS)
        self.assertEqual(
            [summary["total_tokens"], summary["model_calls"], summary["usage_missing"]],
            [12_406 + 3_474 + 88, 3, 0],
        )

    def test_a_skipped_model_call_is_not_a_call(self) -> None:
        """`ok is None` 是"根本没调"（关了联网模型 / 缺 key）。算进分母会让缺失率失真。"""
        summary = summarise_usage(
            [*self.STEPS, {"kind": "model_call",
                           "payload": {"ok": None, "skipped_reason": "external_models_disabled"}}]
        )
        self.assertEqual([summary["model_calls"], summary["usage_missing"]], [3, 0])

    def test_a_failed_call_without_usage_is_counted_as_missing(self) -> None:
        summary = summarise_usage(
            [*self.STEPS, {"kind": "model_call",
                           "payload": {"ok": False, "error_type": "ConnectTimeout"}}]
        )
        self.assertEqual([summary["model_calls"], summary["usage_missing"]], [4, 1])

    def test_no_price_table_means_unknown_not_zero(self) -> None:
        summary = summarise_usage(self.STEPS)
        self.assertEqual(summary["cost_basis"], "unknown")
        self.assertNotIn("cost_estimate", summary)

    def test_the_estimate_reports_how_much_it_could_not_price(self) -> None:
        """ReAct 每步只有总量、没有输入/输出拆分，而价格表是分方向的。

        这类 token 不硬算（按输出价会高估几倍），而是明写缺口有多大。
        """
        summary = summarise_usage(
            self.STEPS, prices={"deepseek-chat": {"in": 0.0000002, "out": 0.0000008}}
        )
        self.assertEqual(summary["cost_basis"], "estimated")
        self.assertEqual(summary["cost_estimate"], round(70 * 2e-7 + 18 * 8e-7, 6))
        self.assertEqual(summary["cost_unpriced_tokens"], 12_406 + 3_474)

    def test_an_empty_turn_reports_zero_calls_and_no_cost(self) -> None:
        summary = summarise_usage([])
        self.assertEqual(summary, {"model_calls": 0})

    def test_callers_cannot_overwrite_the_derived_fields(self) -> None:
        """派生值只有一个来源。允许业务另报一份，就是允许两个数字对不上。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                framework_run(TurnTraceSink("agent"))
                set_turn_result(total_tokens=999_999, cost_estimate=42.0, status_code=200)
            events = read_turn(root)
        payload = event_payload(events, "turn_end")
        self.assertEqual(payload["total_tokens"], sum(STEP_TOKENS))
        self.assertNotIn("cost_estimate", payload)
        # 拒绝必须**可见**：静默丢掉只会让人以为记上了。
        self.assertGreater(payload["trace_errors"], 0)
        self.assertEqual(payload["status_code"], 200)


class _FrameworkishAgent:
    """驱动**真实** sink 的假 agent：`run` 里发的正是框架同步 `run` 会发的那串事件。

    比 `mock.Mock` 有用的地方在于它带 `llm` / `system_prompt` / `_build_tool_schemas`，
    所以 `attach_react_trace` 走的是完整路径，而不是全靠 `getattr` 的兜底。
    """

    name = "FitHealthAgent"
    max_steps = 15

    class _LLM:
        model = "deepseek-chat"
        base_url = "https://api.deepseek.com/v1"
        temperature = 0.7
        timeout = 60

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.llm = self._LLM()
        self.system_prompt = "SYSTEM PROMPT + 时间锚点"
        self.trace_logger = None

    def _build_tool_schemas(self) -> list[dict]:
        return [
            {"type": "function",
             "function": {"name": "query_sleep", "description": "查询睡眠", "parameters": {}}}
        ]

    def run(self, text: str) -> str:
        framework_run(self.trace_logger, answer=self.answer, prompt=text)
        return self.answer


def _fake_factory(*, avoid_youtube_channels=None, role: str = "agent"):
    """复刻 `create_fithealth_agent` 的接线：构造之后立刻装 sink。"""
    agent = _FrameworkishAgent(COMPLETE_PLAN)
    attach_react_trace(agent, role=role, static_system_prompt="STATIC SYSTEM PROMPT")
    return agent


class EndToEndTurnTest(unittest.TestCase):
    """真实 `/chat`：一个回合里同时读出闸门、上下文组装、两个 ReAct 循环。"""

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.profile = {
            "height_cm": 175, "birth_date": "1995-01-01", "gender": "male",
            "goal": "增肌", "equipment": ["哑铃"], "weekly_weight_kg": [70],
        }
        self.intent = ChatIntent(
            create_training_plan=True,
            training_plan_subject="上肢",
            training_plan_title="上肢训练",
        )
        self.resolved = {
            "decision": "override_today", "effective_subject": "上肢训练",
            "blocking_reasons": [], "active_safety_constraints": [],
            "workflow_state": "PLAN_READY", "muscle_recovery": {},
        }

    def _post(self, message: str, *, validation, limit: int | None = None):
        with tempfile.TemporaryDirectory(prefix="fithealth-react-store-") as directory:
            patches = [
                mock.patch.object(
                    deps, "soreness_store",
                    SorenessStore(Path(directory) / "muscle_soreness.json"),
                ),
                mock.patch.object(deps, "info_store", _empty_info_store()),
                mock.patch.object(deps, "classify_user_health_statement", return_value=False),
                mock.patch.object(deps.profile_store, "get_profile", return_value=self.profile),
                mock.patch.object(deps.profile_store, "is_complete", return_value=True),
                mock.patch.object(deps, "build_current_week_reply", return_value=None),
                mock.patch.object(deps, "route_chat_intent", return_value=self.intent),
                mock.patch.object(deps, "create_fithealth_agent", _fake_factory),
                mock.patch.object(
                    chat_workflow, "resolve_plan_context", return_value=self.resolved
                ),
                mock.patch.object(
                    deps, "validate_plan_goal_alignment",
                    return_value={"passed": True, "matched_subjects": ["上肢训练"],
                                  "missing_subjects": [], "reason": "", "stage": "lite_llm"},
                ),
                mock.patch.object(
                    chat_workflow, "validate_generated_training_plan", side_effect=validation
                ),
            ]
            if limit is not None:
                patches.append(mock.patch.object(chat_workflow, "AGENT_INPUT_MAX_CHARS", limit))
            with ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                with traced_trace_dir() as root:
                    response = self.client.post(
                        "/chat",
                        json={"message": message, "history": [], "source": "chat"},
                    )
                    events = read_turn(root)
        return response, events

    def test_a_single_pass_turn_reads_end_to_end(self) -> None:
        response, events = self._post("帮我生成一份上肢训练计划", validation=[[]])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len({event["turn_id"] for event in events}), 1)
        # 组装 → 输入 → ReAct → 出口，一条链读下来。
        self.assertEqual(
            [kind for kind in kinds_of(events) if kind.startswith(("react", "agent_input"))],
            ["react_init", "agent_input", "react_step", "react_step", "react_end"],
        )
        budget = gate_payload(events, "context_budget")
        self.assertEqual(budget["outcome"], "accepted")
        # 分段长度加起来必须等于总长：这条让"是哪一段吃光了预算"可算而不是可猜。
        self.assertEqual(sum(budget["section_lens"].values()), budget["total_len"])
        self.assertGreater(budget["section_lens"]["profile"], 0)
        # 框架收到的那串输入与组装结果同长——中间没有谁把上下文改过。
        self.assertEqual(event_payload(events, "agent_input")["total_len"], budget["total_len"])
        self.assertEqual(event_payload(events, "turn_end")["total_tokens"], sum(STEP_TOKENS))

    def test_auto_correction_shares_the_turn_with_the_main_loop(self) -> None:
        """验收问题 #5："自动修正到底改了什么"要能在同一份 trace 里连续读下来。"""
        response, events = self._post(
            "帮我生成一份上肢训练计划", validation=[["组数低于恢复要求"], []]
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len({event["turn_id"] for event in events}), 1)
        self.assertEqual(
            [event["span"] for event in events_of(events, "react_init")],
            ["agent", "correction_agent"],
        )
        self.assertEqual(
            [event["span"] for event in events_of(events, "react_end")],
            ["agent", "correction_agent"],
        )
        correction = gate_payload(events, "auto_correction")
        self.assertEqual([correction["outcome"], correction["stop_reason"]], ["passed", "passed"])
        # 两个循环各跑两步，token 汇总覆盖两者。
        self.assertEqual(
            event_payload(events, "turn_end")["total_tokens"], 2 * sum(STEP_TOKENS)
        )
        self.assertEqual(event_payload(events, "turn_end")["model_calls"], 4)

    def test_a_rejected_context_records_the_section_that_blew_the_budget(self) -> None:
        """上下文超预算时 agent 从未 run，框架一个事件都不发——闸门必须在组装处记。

        阈值调小到 500：真实上限是 65,536，而单条消息被 `CHAT_MESSAGE_MAX_CHARS`
        挡在 12,000，从 HTTP 层根本撞不到。这条用例要验的是**事件**，不是那个数字。
        """
        response, events = self._post("帮我生成一份上肢训练计划", validation=[[]], limit=500)
        self.assertEqual(response.status_code, 413)
        budget = gate_payload(events, "context_budget")
        self.assertEqual([budget["outcome"], budget["code"], budget["limit"]],
                         ["rejected", "CONTEXT_BUDGET_EXCEEDED", 500])
        self.assertGreater(budget["total_len"], 500)
        # 分段长度加起来等于总长：于是"是哪一段吃光了预算"可算而不是可猜。
        self.assertEqual(sum(budget["section_lens"].values()), budget["total_len"])
        # agent 已经建好（`react_init` 在），但一步都没跑——这正是阶段 0 里 TRACE-07
        # 那条泄漏路径的形状，现在它在 trace 里看得出来。
        self.assertEqual(
            [kind for kind in kinds_of(events) if kind.startswith("react")], ["react_init"]
        )
        self.assertEqual(gate_payload(events, "chat_response")["source"], "context_budget")


class FailureIsolationTest(unittest.TestCase):
    """sink 自己出问题绝不能打断 ReAct 主循环——它是在循环里被同步调用的。"""

    class _Explodes:
        def __str__(self) -> str:
            raise RuntimeError("这个对象的 __str__ 就炸")

    def test_a_payload_that_explodes_degrades_instead_of_raising(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                sink = TurnTraceSink("agent")
                # 不抛出去：抛了就等于把一次对话毁在诊断代码上。
                self.assertIsNone(
                    sink.log_event("message_written", {"role": "user", "content": self._Explodes()})
                )
                self.assertEqual(current_turn().trace_errors, 1)
                # 后续事件照记，不因为前一条坏掉就整段失效。
                sink.log_event(
                    "model_output",
                    {"content": "", "tool_calls": 0, "usage": {"total_tokens": 7, "cost": 0.0}},
                    step=1,
                )
            events = read_turn(root)
        self.assertEqual(kinds_of(events), ["turn_start", "react_step", "turn_end"])
        self.assertEqual(event_payload(events, "turn_end")["trace_errors"], 1)

    def test_a_non_dict_payload_is_tolerated(self) -> None:
        """框架升级可能换 payload 形状。翻译不该因此抛，也不该写出半条事件。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                sink = TurnTraceSink("agent")
                sink.log_event("model_output", None, step=1)
                sink.log_event("tool_result", ["not", "a", "dict"], step=1)
            events = read_turn(root)
        self.assertEqual(kinds_of(events), ["turn_start", "react_step", "tool_result", "turn_end"])
        self.assertEqual(event_payload(events, "tool_result")["status"], "success")
        self.assertEqual(event_payload(events, "react_step")["usage_source"], "unknown")

    def test_attach_survives_an_agent_without_any_of_the_attributes(self) -> None:
        """`deps.create_fithealth_agent` 在多数测试里是个假 agent。装 trace 不能挑食。"""
        class _Bare:
            pass

        with trace_env(FITHEALTH_TRACE="on") as root:
            with start_turn("/chat"):
                bare = _Bare()
                sink = attach_react_trace(bare, role="agent")
                self.assertIsInstance(sink, TurnTraceSink)
                self.assertIs(bare.trace_logger, sink)
            events = read_turn(root)
        payload = event_payload(events, "react_init", "agent")
        self.assertEqual(payload, {"runtime_prompt_len": 0})

    def test_the_session_id_points_at_the_current_turn(self) -> None:
        """框架的 `save_session` 会读它；指向回合 id 才能和本项目的产物对上。"""
        with trace_env(FITHEALTH_TRACE="on"):
            sink = TurnTraceSink("agent")
            detached = sink.session_id
            with start_turn("/chat") as turn:
                self.assertEqual(sink.session_id, turn.turn_id)
        self.assertTrue(detached.startswith("detached-agent-"), detached)


if __name__ == "__main__":
    unittest.main()












