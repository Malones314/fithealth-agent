"""observability/react_trace.py — 把框架的 ReAct 事件引进当前回合（阶段 5）。

## 为什么不放在 `sink.py`

计划书原本把 `TurnTraceSink` 写进 `sink.py`，但那会形成**循环导入**：本模块需要
`trace.trace_event`，而 `trace` 已经 import `sink`。而且职责也不同——`sink.py` 管的是
磁盘（目录校验、原子写、index），本模块一个字节都不落盘。所以按 `model_trace.py`
的同一形状单开一个模块：`react_trace` → `trace`，单向。

## 鸭子类型的最小契约

框架只用 `trace_logger` 的三个成员（全仓库扫描见 `tests/test_trace_react_sink.py`）：

| 成员 | 框架怎么用 | 我们怎么实现 |
| --- | --- | --- |
| `log_event(event, payload, step=None)` | ReAct 主循环的 6 个记录点 | 按白名单翻译成本项目的 kind/span |
| `finalize()` | 三条正常返回路径各调一次 | **严格空操作**（落盘由回合负责） |
| `session_id` | `_register_devlog_tool` 读它 | 当前 `turn_id`（devlog 已在阶段 0 关掉） |

`finalize()` 是空操作这一点是刻意的：TRACE-07（构造即开句柄、只在特定返回路径
finalize）在阶段 0 已被关闭框架 logger 消除，这里必须保证不重新引入。`agent.run`
从未被调用、或在循环中途抛异常时，本模块都没有任何东西需要收尾。

## 翻译是白名单，不是透传

框架 payload 里有两样东西绝不能原样进 trace：

* `error` 事件的 `message`（`str(exc)`，常带 URL 与请求参数）；
* `model_output` 里 `usage.cost` 那个**硬编码的 0.0**——把"不知道"写成"零成本"
  正是 TRACE-04 的病根。

所以每个事件都有一个显式的翻译函数，逐字段挑；没被翻译的字段根本到不了
`redact_payload`。未知事件（框架升级新增、或异步路径专用的 `hook_*`）走
`react_unmapped`：只记事件名，payload 的**值**一律丢弃——"框架报了个我们不认识的
事件"必须可见，但它的内容没有契约可依。

## 一个框架缺陷，在这一层纠正掉

LLM 调用失败时框架先发 `error` 再 `break`，**接着走"达到最大步数"那条路**发出
`session_end{status: "timeout"}`（`react_agent.py:181-189` → `:365-385`）。于是
"真的用完 15 步"和"第 1 步就挂了"在框架的口径里是同一个值。

本项目不 patch `hello_agents`（会破坏 `vendor/` 的 wheel 校验约束），所以在翻译层
纠正：sink 记住本轮是否出现过 `error`，翻译 `session_end` 时给出**已纠正**的结论。

| 现场 | 框架说 | trace 记 |
| --- | --- | --- |
| 正常收尾 | `success` | `status=success`、`stop_reason=finished` |
| 真的用完步数 | `timeout` | `status=timeout`、`stop_reason=max_steps`、`max_steps_reached=true` |
| 第 N 步 LLM 挂了 | `timeout` | `status=llm_error`、`stop_reason=llm_error`、`error_type=…`、`framework_status=timeout` |

两条纪律：**纠正必须留痕**（`framework_status` 只在与纠正结果不一致时出现，正常回合
不添噪声），**`max_steps_reached` 由步数算而不是抄框架的 status**——`total_steps >=
max_steps` 是独立可验的事实，而那个 status 恰恰是不可信的那一项。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Any, Callable

from .config import load_settings
from .model_trace import endpoint_host
from .schema import AGENT_SPANS
from .trace import current_turn, trace_event


logger = logging.getLogger("fithealth")

#: 框架会发、但本项目刻意**不记**的事件，附理由。
#:
#: `session_start` 在 `Agent.__init__` 里发（`core/agent.py:80`），而我们是构造**之后**
#: 才装 sink，所以它根本到不了这里；就算到了也没有价值——它的 payload 是整份 Config，
#: 而可复现所需的字段已经在 `react_init` 里，且经过了字段级白名单。
IGNORED_EVENTS: dict[str, str] = {
    "session_start": "构造后才装 sink，发不出来；可复现字段见 react_init",
}


def _sha12(text: str) -> str:
    """稳定摘要。

    **不用** HMAC：这里摘的是本仓库自己的源码（系统提示词、工具 schema），不是用户
    数据，需要的恰恰是"跨进程、跨重启可比"——那正是 `redact.text_digest` 为了防低熵
    反查而刻意放弃的性质。
    """
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _tool_view(agent: Any) -> tuple[list[str], str, int]:
    """工具清单、schema 摘要、工具数。

    数量单独记：`LIMITS.list_items` 是 20，现在 14 个刚好放得下，但清单一旦超限会被
    静默截断，而"发给模型的工具有几个"是排障时第一个要对的数。
    """
    schemas = agent._build_tool_schemas()  # noqa: SLF001 - 框架没有公开等价物
    names = sorted(
        str(schema["function"]["name"])
        for schema in schemas
        if isinstance(schema, dict) and isinstance(schema.get("function"), dict)
    )
    digest = _sha12(json.dumps(schemas, ensure_ascii=False, sort_keys=True))
    return names, digest, len(schemas)


class TurnTraceSink:
    """一个 ReAct 循环的事件翻译器。所有方法都不抛异常。

    主循环与自动修正循环各有一个实例，共享同一个 `turn_id`，靠 `role` 区分
    （`role` 就是事件的 span，取值见 `schema.AGENT_SPANS`）。

    实例是**有状态**的：它累计本 role 的 token，并记住本轮是否出现过 LLM 错误——
    后者是纠正框架那个 `timeout` 口径的唯一依据（见模块头）。所以一个 sink 只能给
    一个 agent 用，不能跨 agent 复用。
    """

    __slots__ = (
        "role", "model", "max_steps",
        "_fallback_id", "_tokens", "_usage_missing", "_error_type",
    )

    def __init__(
        self, role: str = "agent", *, model: str = "", max_steps: int | None = None
    ) -> None:
        self.role = role
        self.model = model
        #: 用来判断"是不是真的用完步数"。拿不到时退化为相信框架的 status。
        self.max_steps = max_steps
        self._fallback_id = f"detached-{role}-{secrets.token_hex(3)}"
        self._tokens = 0
        self._usage_missing = 0
        self._error_type: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - 只为排障可读
        return f"TurnTraceSink(role={self.role!r})"

    @property
    def session_id(self) -> str:
        """框架的 `save_session` / DevLog 会读它。用回合 id，好和本项目的产物对上。"""
        turn = current_turn()
        return turn.turn_id if turn is not None else self._fallback_id

    def log_event(
        self, event: str, payload: dict[str, Any] | None = None, step: int | None = None
    ) -> None:
        """框架的唯一记录入口。绝不抛：它在 ReAct 主循环里被同步调用。"""
        if current_turn() is None:
            # 不在回合内（例如有人在回合外复用了这个 agent）：直接退，代价是一次
            # ContextVar.get()，与 trace 关闭时同价。
            return
        try:
            fields = dict(payload) if isinstance(payload, dict) else {}
            mapped = _EVENT_MAP.get(str(event))
            if mapped is None:
                if str(event) in IGNORED_EVENTS:
                    return
                trace_event(
                    UNMAPPED_KIND, self.role, step=step, framework_event=str(event)[:80]
                )
                return
            mapped[1](self, fields, step)
        except Exception:  # noqa: BLE001 - 可观测性故障不能变成业务故障
            turn = current_turn()
            if turn is not None:
                turn.degrade("ReAct 事件翻译失败：event=%s role=%s", event, self.role)

    def finalize(self) -> None:
        """**严格空操作**：没有句柄、没有文件、没有缓冲要刷。

        框架在三条返回路径上各调一次（成功 / Finish / 超步数），异常路径一次都不调。
        空操作让这两种情况的行为完全一致——落盘只由 `start_turn` 的 finally 负责。
        """
        return None

    # ── 翻译 ──────────────────────────────────────────────────────────────

    def _record(
        self, kind: str, span: str, step: int | None, *, dur_ms: int | None = None, **fields: Any
    ) -> None:
        """记一条事件，**丢掉值为 None 的字段**。

        为什么要显式丢：`redact` 把 None 当成合法的 structural 值原样落盘，于是
        `total_tokens: null` 会和"这一步真的报了用量"混在同一个键上。缺失就该是
        **键不存在**——`cost.summarise_usage` 与所有断言都按这条读。
        """
        trace_event(
            kind,
            span,
            step=step,
            dur_ms=dur_ms,
            **{name: value for name, value in fields.items() if value is not None},
        )

    def _input(self, payload: dict[str, Any], step: int | None) -> None:
        """`message_written` → `agent_input`。

        只有拼装后的 user 输入（框架不发 system 消息）。分段长度不在这里——它由
        `chat_workflow.build_agent_input` 的 `gate/context_budget` 记，那里才拿得到
        各段的真实长度，而且上下文超预算被拒时框架根本不会发出本事件。
        """
        content = payload.get("content")
        text = content if isinstance(content, str) else str(content or "")
        self._record("agent_input", self.role, step, total_len=len(text), text=text)

    def _step(self, payload: dict[str, Any], step: int | None) -> None:
        """`model_output` → `react_step`。"""
        usage = payload.get("usage")
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        counted = isinstance(total, int) and not isinstance(total, bool) and total > 0
        if counted:
            self._tokens += int(total)
        else:
            self._usage_missing += 1
        content = payload.get("content")
        self._record(
            "react_step",
            self.role,
            step,
            model=self.model or None,
            tool_calls=payload.get("tool_calls"),
            # 框架在 `response.usage` 为空时写 0。0 不是"免费"，是"不知道"——
            # 这里宁可不记这个字段，也不能让它进汇总（TRACE-04）。
            total_tokens=int(total) if counted else None,
            usage_source="provider" if counted else "unknown",
            content=content if isinstance(content, str) and content else None,
        )

    def _error(self, payload: dict[str, Any], step: int | None) -> None:
        """`error` → 带 `error_type` 的 `react_step`，并记住"这一轮挂过"。

        `message` 刻意丢掉：它是 `str(exc)`，里面常带 base_url 与请求参数。想知道的是
        "第几步挂的、什么类型"，这两样都在。

        记住 `error_type` 是为了纠正随后那条 `session_end{status: "timeout"}`——
        框架发完本事件就 break，然后照走"达到最大步数"的收尾路径（见模块头）。
        """
        kind = payload.get("error_type")
        self._error_type = str(kind) if kind else "unknown"
        self._record(
            "react_step",
            self.role,
            step,
            model=self.model or None,
            error_type=self._error_type,
            stop_reason="llm_error",
        )

    def _tool_call(self, payload: dict[str, Any], step: int | None) -> None:
        args = payload.get("args")
        self._record(
            "tool_call",
            _tool_name(payload),
            step,
            role=self.role,
            tool_call_id=payload.get("tool_call_id"),
            args_keys=sorted(str(key) for key in args) if isinstance(args, dict) else None,
            args=args if args else None,
        )

    def _tool_result(self, payload: dict[str, Any], step: int | None) -> None:
        """`tool_result` → `tool_result`，并**补上框架缺的 status**。

        框架只在内置工具（Thought/Finish）分支写 `status`；用户工具分支连字段都没有
        （§1.3 的"用户工具分支连 status 都没有"）。成败信息其实在结果文本的前缀里：
        `_execute_tool_call` 用 ❌ 表示错误、⚠️ 表示部分成功。这里把它还原成枚举，
        于是"这次工具到底成没成"不必靠人去读 emoji。
        """
        result = payload.get("result")
        text = result if isinstance(result, str) else str(result or "")
        declared = payload.get("status")
        self._record(
            "tool_result",
            _tool_name(payload),
            step,
            role=self.role,
            tool_call_id=payload.get("tool_call_id"),
            status=str(declared) if declared else _status_from_text(text),
            result_bytes=len(text.encode("utf-8", "replace")),
            result=text or None,
        )

    def _end(self, payload: dict[str, Any], step: int | None) -> None:
        """`session_end` → `react_end`，并纠正框架那个含糊的 `timeout`。

        `total_tokens` 是**本 role** 的累计（回合级汇总在 `turn_end`，由
        `cost.summarise_usage` 从 `react_step` 事件重算，不读这里，所以两者不会互相
        污染）。三种收尾的口径对照表见模块头。
        """
        duration = payload.get("duration")
        total_steps = payload.get("total_steps")
        framework_status = str(payload.get("status") or "") or None
        status, stop_reason = self._outcome(framework_status)
        answer = payload.get("final_answer")
        self._record(
            "react_end",
            self.role,
            step,
            dur_ms=int(float(duration) * 1000) if isinstance(duration, (int, float)) else None,
            status=status,
            stop_reason=stop_reason,
            # 纠正必须留痕，但正常回合不添噪声：只在与纠正结果不一致时记。
            framework_status=framework_status if framework_status != status else None,
            error_type=self._error_type,
            total_steps=total_steps,
            total_tokens=self._tokens or None,
            usage_source=(
                "partial" if self._tokens and self._usage_missing
                else "provider" if self._tokens
                else "unknown"
            ),
            max_steps_reached=self._hit_step_limit(total_steps, status),
            final_answer=answer if isinstance(answer, str) and answer else None,
        )

    def _outcome(self, framework_status: str | None) -> tuple[str | None, str | None]:
        """把框架的 status 翻成**已纠正**的 (status, stop_reason)。"""
        if framework_status != "timeout":
            return framework_status, "finished" if framework_status == "success" else None
        if self._error_type is not None:
            # 框架发完 `error` 就 break，收尾却照走"达到最大步数"那条路。
            return "llm_error", "llm_error"
        return "timeout", "max_steps"

    def _hit_step_limit(self, total_steps: Any, status: str | None) -> bool:
        """真的用完步数了吗。

        优先按**步数**算：`total_steps >= max_steps` 是独立可验的事实，而框架的
        status 恰恰是这里不可信的那一项。两个数都拿不到时才退化为看纠正后的 status。
        """
        if isinstance(total_steps, int) and isinstance(self.max_steps, int):
            return total_steps >= self.max_steps
        return status == "timeout"


def _tool_name(payload: dict[str, Any]) -> str:
    name = payload.get("tool_name")
    return str(name) if name else "unknown_tool"


def _status_from_text(text: str) -> str:
    """从 `_execute_tool_call` 的前缀还原成败（见 `_tool_result` 的说明）。"""
    if text.startswith("❌"):
        return "error"
    if text.startswith("⚠️"):
        return "partial"
    return "success"


#: 框架事件名 -> (本项目的 kind, 翻译函数)。**一张表**而不是两张：分开写会漂移——
#: 少一个翻译函数只会让事件静默落到 `react_unmapped`，而声明的 kind 还写着别的。
#:
#: `tests/test_trace_react_sink.py` 静态扫描框架源码，任何不在这里、也不在
#: `IGNORED_EVENTS` 里的事件都会让那条用例先红——框架升级不会静默丢事件；另有一条
#: 逐个事件跑一遍，断言真实产出的 kind 与这里声明的一致。
_EVENT_MAP: dict[str, tuple[str, Callable[[TurnTraceSink, dict[str, Any], int | None], None]]] = {
    "message_written": ("agent_input", TurnTraceSink._input),
    "model_output": ("react_step", TurnTraceSink._step),
    "error": ("react_step", TurnTraceSink._error),
    "tool_call": ("tool_call", TurnTraceSink._tool_call),
    "tool_result": ("tool_result", TurnTraceSink._tool_result),
    "session_end": ("react_end", TurnTraceSink._end),
}

#: 框架事件名 -> 本项目的 kind。给测试与读 trace 的人查表用。
TRANSLATED_EVENTS: dict[str, str] = {
    event: kind for event, (kind, _translate) in _EVENT_MAP.items()
}

#: 未翻译事件落到哪个 kind：只记事件名，payload 的值一律丢弃。
UNMAPPED_KIND = "react_unmapped"


def attach_react_trace(
    agent: Any, *, role: str = "agent", static_system_prompt: str = ""
) -> TurnTraceSink | None:
    """给一个 ReActAgent 装上 sink 并记一条 `react_init`。绝不抛。

    **trace 关闭时什么都不做**，`agent.trace_logger` 留在 `None`：框架的
    `if self.trace_logger:` 于是短路，连 payload 字典都不会构造——"关闭即零开销"
    这条验收标准在接线之后仍然成立，`tests/baseline/agent_snapshot.json` 里
    `trace_logger_type: null` 也继续为真。

    `static_system_prompt` 是**不含时间锚点**的那份提示词。为什么必须分开：
    `create_fithealth_agent` 会往系统提示词尾部拼当前时刻，整份摘要于是每秒一变，
    "这次回答用的是哪版 prompt"就答不出来了。摘要摘静态那份，长度另记一个
    `runtime_prompt_len`。

    Returns:
        装上去的 sink；trace 关闭或出任何意外时返回 None。
    """
    try:
        if not load_settings().enabled:
            return None
        sink = TurnTraceSink(
            role,
            model=_model_name(agent),
            # 用来判断"是不是真的用完步数"，而不是抄框架那个含糊的 timeout。
            max_steps=getattr(agent, "max_steps", None),
        )
        agent.trace_logger = sink
        trace_event("react_init", role, **_init_fields(agent, static_system_prompt))
        return sink
    except Exception:  # noqa: BLE001 - 装 trace 失败绝不能挡住一次对话
        logger.warning("装载 ReAct trace sink 失败（role=%s）", role, exc_info=True)
        return None


def _model_name(agent: Any) -> str:
    return str(getattr(getattr(agent, "llm", None), "model", "") or "")


def _init_fields(agent: Any, static_system_prompt: str) -> dict[str, Any]:
    """`react_init` 的 payload。逐项 `getattr`：调用方可能是测试里的假 agent。

    不记 `role`：它就是这条事件的 span，写两遍只会多一个必须保持一致的地方。
    """
    llm = getattr(agent, "llm", None)
    runtime_prompt = str(getattr(agent, "system_prompt", "") or "")
    prompt = static_system_prompt or runtime_prompt
    fields: dict[str, Any] = {
        "agent_name": str(getattr(agent, "name", "") or "") or None,
        "model": _model_name(agent) or None,
        # 完整 base_url 可能带内网拓扑与 query，只留主机名（审查意见）。
        "endpoint_host": endpoint_host(getattr(llm, "base_url", "") or "") or None,
        "temperature": getattr(llm, "temperature", None),
        "timeout_s": getattr(llm, "timeout", None),
        "max_retries": getattr(llm, "max_retries", None),
        "max_steps": getattr(agent, "max_steps", None),
        "system_prompt": prompt or None,
        "system_prompt_sha12": _sha12(prompt) if prompt else None,
        "runtime_prompt_len": len(runtime_prompt),
    }
    try:
        names, digest, count = _tool_view(agent)
    except Exception:  # noqa: BLE001 - 假 agent 没有 _build_tool_schemas
        names, digest, count = [], "", 0
    if count:
        fields.update(tools=names, tool_schema_digest=digest, tool_count=count)
    return {name: value for name, value in fields.items() if value is not None}


__all__ = [
    "AGENT_SPANS",
    "IGNORED_EVENTS",
    "TRANSLATED_EVENTS",
    "UNMAPPED_KIND",
    "TurnTraceSink",
    "attach_react_trace",
]
