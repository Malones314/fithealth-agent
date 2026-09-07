"""observability/schema.py — 事件契约（agent-trace 阶段 1）。

## 为什么要一份显式契约，而不是"传什么记什么"

trace 里会流过健康隐私。`hello_agents` 那套正则脱敏（`sk-*` / `Bearer *` /
`/Users/x`）对"膝盖疼"完全无效，所以本项目改成**字段级白名单**：每个事件类型
声明自己允许哪些字段、每个字段属于哪一类，未注册的值一律不落盘。

## 名字与值的区别（这条决定了不兼容时怎么降级）

字段名和 span 名**来自我们自己的源码**，永远不来自用户输入；字段值可能是用户
的健康描述。所以两者的降级方向相反：

* 未注册的 **span / 字段名** 照记，并标记 `_unknown_span` / 列进 `_dropped`——
  这是漏注册的编程失误，藏起来只会让人查不到；
* 未注册的 **值** 一律丢弃——宁可少一条信息，不能多一份原文。

## 三个类别

* ``S``（structural）：枚举、计数、布尔、状态码、工具名。原样保留。
* ``T``（text）：自由文本。`meta` 级别只留长度与 HMAC 摘要，`full` 级别留原文
  （仍受长度上限约束）。
* ``TL``（text_list）：字符串列表，逐项按 ``T`` 处理，条数受上限约束。

新增字段等于一次隐私评审：加进下面的表，同时在
`tests/test_trace_infrastructure.py` 里补一条断言。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


#: 事件格式版本。每条事件都带，方便按行 grep 时自描述。
#: 改动规则：加字段不升版本（读方按缺失处理）；改字段含义或删字段必须升。
SCHEMA_VERSION = 1

S = "structural"
T = "text"
TL = "text_list"


@dataclass(frozen=True)
class Limits:
    """payload 的硬上限。

    为什么每一项都要有上限：`tool_result` 可能是一次 30 KB 的查询结果，
    `plan_validation` 的 violations 可能有十几条，`args` 里可能嵌着整份计划正文。
    没有上限，一个回合就能写出几 MB，而 trace 是**默认可在生产开启**的。
    """

    #: 单个回合最多记多少条事件。超出后停止追加并在 turn_end 报 events_dropped。
    events_per_turn: int = 2000
    #: structural 字符串的长度上限（工具名、枚举、状态码这类，正常都很短）。
    structural_chars: int = 200
    #: text 字段在 full 级别的长度上限；超出截断并标 `_truncated`。
    text_chars: int = 4000
    #: 列表类字段的条数上限。
    list_items: int = 20
    #: structural 容器的嵌套深度上限（list/dict 层数）。
    depth: int = 3
    #: 单条事件序列化后的字节上限，兜住上面几条都没拦住的组合爆炸。
    event_bytes: int = 16 * 1024


LIMITS = Limits()


@dataclass(frozen=True)
class EventSpec:
    """一个事件类型的契约。

    `spans` 为 None 表示 span 自由取值（路由路径、工具名这类无法枚举的）；
    否则必须落在枚举里。`fields` 是"所有 span 共用"的字段，`span_fields` 给
    个别 span 追加专有字段——gate 就靠这一层做到"每个闸门固定 schema"。
    """

    spans: frozenset[str] | None
    fields: Mapping[str, str]
    span_fields: Mapping[str, Mapping[str, str]] = None  # type: ignore[assignment]

    def allowed(self, span: str) -> Mapping[str, str]:
        if not self.span_fields:
            return self.fields
        return {**self.fields, **self.span_fields.get(span, {})}


#: 确定性闸门。span 就是闸门名，每个闸门有自己的字段表（见 `GATE_SPEC`）。
GATE_SPANS = frozenset({
    "health_risk",
    "soreness",
    "plan_context",
    "plan_validation",
    "auto_correction",
    "memory_candidate",
    "profile_gate",
    "context_budget",
    # 所有 20 个提前返回分支共用的出口（`chat_workflow._chat_response`）。
    "chat_response",
})

#: 外部模型触点。span 用**公开入口的函数名**（而不是内部那个真正发请求的私有函数），
#: 因为读 trace 的人是拿着调用方的代码在查：`route_information` 的请求实际在
#: `_level3_llm_decide` 里发，`validate_training_plan` 的在 `_level2_llm_check` 里。
#:
#: 完整性由 `tests/test_trace_model_calls.py` 静态保证：全包扫描任何往
#: `/chat/completions` 发请求的函数，都必须处在 `model_call` 块内。那条检查在
#: 阶段 4 落地时就抓到了计划漏掉的第 7 个触点（上传计划分类）。
MODEL_CALL_SPANS = frozenset({
    "classify_user_health_statement",
    "route_chat_intent",
    "route_information",
    "validate_plan_goal_alignment",
    "validate_training_plan",
    "query_muscles_with_lite_model",
    "analyze_food_image",
})

#: 两个 ReAct 循环。主循环与自动修正循环共享一个 turn，靠这个区分。
AGENT_SPANS = frozenset({"agent", "correction_agent"})

_MODEL_CALL_FIELDS = {
    "model": S,
    "endpoint_host": S,
    "http_status": S,
    # ok=True 调用成功；False 失败并回落；None 根本没调（关了联网模型 / 缺 key）。
    # 三态是刻意的：把"没调"和"调了返回空"混成一个值，正是 BUG-02 查不下去的原因。
    "ok": S,
    "skipped_reason": S,
    "fallback_reason": S,
    "error_type": S,
    "prompt_tokens": S,
    "completion_tokens": S,
    "total_tokens": S,
    # usage 是供应商回的还是本地估的。0 不能当成"零成本"。
    "usage_source": S,
    "call_id": S,
    "attempt": S,
    "retry_count": S,
    # 供应商给的停止原因。`length` = 被 max_tokens 截断，这是"解析失败"最常见的
    # 真实原因，不记下来只能靠猜（阶段 4 就是这么被咬了一次）。
    "finish_reason": S,
    # 请求体字节数（图片分析尤其需要）。**只记大小，不记内容**。
    "request_bytes": S,
    # 结果被采用时的**短结构化记号**，不是模型输出原文。各 span 的约定见
    # `observability/model_trace.py`：意图名 / saved|not_saved / 命中条数…
    "result": S,
}

_GATE_COMMON = {"outcome": S, "reason": S}

#: 每个闸门的专有字段。字段名与 `chat_workflow` 里既有的变量名一致，方便对照。
_GATE_SPAN_FIELDS: Mapping[str, Mapping[str, str]] = {
    "health_risk": {
        "level": S, "labels": S, "painful_regions": S, "painful_count": S,
        "blocks_plan": S,
    },
    "soreness": {
        "saved_count": S, "regions": S, "levels": S, "asked_regions": S,
        # 这次反馈是我们主动问出来的（asked_regions 非空），还是从用户自发的一句话里
        # 解析出来的。前者可信度高得多；出现"记错了部位"的投诉时这是第一个要看的字段。
        "prompted": S,
    },
    "plan_context": {
        "decision": S, "effective_subject": S, "scheduled_subject": S,
        "blocking_reasons": TL, "clarification_required": S,
        "active_safety_constraints": TL, "workflow_state": S,
    },
    "plan_validation": {
        "violations": TL, "violation_count": S, "goal_alignment_passed": S,
        "missing_subjects": TL, "expected_subject": S, "alignment_stage": S,
    },
    "auto_correction": {
        "attempted": S, "passed": S, "first_violations": TL,
        "final_violations": TL, "corrected_is_complete_plan": S,
        # attempt 恒为 1（现实现只修正一次），显式记下来是为了将来改成多轮时
        # 读方不必猜。stop_reason 区分四种收尾：passed / still_violating /
        # not_a_plan / call_failed——"修正没通过"和"修正服务挂了"是两件事。
        "attempt": S, "stop_reason": S,
    },
    "memory_candidate": {
        "entry_id": S, "namespace": S, "key": S, "capture": S,
        "retention_class": S, "value": T,
    },
    "profile_gate": {
        "is_complete": S, "missing_fields": S, "updated_fields": S,
    },
    # 上下文预算闸门。阶段 5 起**两种结局都记**（`outcome` = accepted / rejected）：
    # `section_lens` 是"记忆到底进没进上下文""是哪一段把预算吃光了"的唯一答案，
    # 只在被拒时记就等于只在最坏情况下有数据。
    "context_budget": {
        "code": S, "total_len": S, "limit": S, "section_lens": S,
    },
    "chat_response": {
        "source": S, "status_code": S, "artifact_type": S, "reply_len": S,
        "soreness_saved": S, "memory_candidates": S, "extra_keys": S,
    },
}

EVENT_SPECS: Mapping[str, EventSpec] = {
    "turn_start": EventSpec(
        spans=None,  # 路由路径
        fields={
            "route": S, "source": S, "request_id": S, "history_len": S,
            "external_models_enabled": S, "detail_level": S, "tz": S,
            "producer": S, "message": T,
        },
    ),
    "turn_end": EventSpec(
        spans=None,
        fields={
            "route": S, "source": S, "status": S, "status_code": S,
            "complete": S, "artifact_type": S, "error_type": S,
            # 用量与成本由 `cost.summarise_usage` 从事件流算出，调用方不得覆盖
            # （名字列进 `trace.RESERVED_TURN_END_FIELDS`）。口径见 cost.py：
            # 缺用量时不写 total_tokens 而不是写 0，没价格表时 cost_basis="unknown"。
            "total_tokens": S, "model_calls": S, "usage_missing": S,
            "cost_estimate": S, "cost_basis": S, "cost_unpriced_tokens": S,
            # 降级计数：trace 自身出问题时不能静默累积（审查意见）。
            "events_dropped": S, "fields_dropped": S, "trace_errors": S,
        },
    ),
    "gate": EventSpec(
        spans=GATE_SPANS, fields=_GATE_COMMON, span_fields=_GATE_SPAN_FIELDS
    ),
    "model_call": EventSpec(spans=MODEL_CALL_SPANS, fields=_MODEL_CALL_FIELDS),
    "react_init": EventSpec(
        spans=AGENT_SPANS,
        fields={
            "agent_name": S, "model": S, "endpoint_host": S,
            # 复现一次请求需要的随机性与超时参数（审查意见）。
            "temperature": S, "timeout_s": S, "max_steps": S,
            "tools": S, "tool_count": S, "tool_schema_digest": S,
            # 摘要摘的是**不含时间锚点**的静态提示词，所以跨回合可比；拼上当前时刻后的
            # 总长度另记 `runtime_prompt_len`。理由见 react_trace.attach_react_trace。
            "system_prompt_sha12": S, "runtime_prompt_len": S,
            "system_prompt": T,
        },
    ),
    "react_step": EventSpec(
        spans=AGENT_SPANS,
        fields={
            "step": S, "tool_calls": S, "model": S, "total_tokens": S,
            "usage_source": S, "stop_reason": S, "max_steps_reached": S,
            # LLM 调用失败也走这条（框架发 `error` 后 break）。`stop_reason="llm_error"`
            # 是它与正常步骤的区分点，见 react_trace 模块头那段框架缺陷说明。
            "error_type": S, "content": T,
        },
    ),
    "react_end": EventSpec(
        spans=AGENT_SPANS,
        fields={
            # `status` 是**已纠正**的三态：`success` / `timeout`（真的用完步数）/
            # `llm_error`（框架把 LLM 调用失败也报成 timeout，在 `react_trace._outcome`
            # 里纠正回来）。`framework_status` 只在纠正发生时出现——纠正要留痕，
            # 但正常回合不添噪声。
            "status": S, "framework_status": S, "stop_reason": S,
            "total_steps": S, "total_tokens": S, "usage_source": S,
            # 由 `total_steps >= max_steps` 算出，不抄框架的 status（那正是不可信的
            # 那一项）。
            "max_steps_reached": S, "error_type": S,
            "final_answer": T,
        },
    ),
    #: 框架发了一个本项目没有翻译的事件（升级新增、或异步路径专用的 `hook_*`）。
    #: 只记事件名：payload 的值没有契约可依，一律丢弃。
    "react_unmapped": EventSpec(
        spans=AGENT_SPANS, fields={"framework_event": S}
    ),
    "agent_input": EventSpec(
        spans=AGENT_SPANS,
        # 框架实际收到的那串输入。**分段长度不在这里**——它由
        # `gate/context_budget` 记（组装处才拿得到各段真实长度，而且上下文超预算被拒
        # 时框架根本不会发 `message_written`）。阶段 1 曾在这里注册过 `section_lens`，
        # 零调用点、从未落过盘，故直接移走而不升 SCHEMA_VERSION：没有任何既有数据
        # 带这个字段，也就没有读方会被影响。
        #
        # `total_len` 与 T 字段自动派生的 `text_len` 数值相同，刻意保留：它和
        # `gate/context_budget.total_len` **同名**，于是"组装出来多长"与"框架收到多长"
        # 可以用同一个键直接比——不相等就说明中间有谁改过上下文。
        fields={"total_len": S, "text": T},
    ),
    "tool_call": EventSpec(
        spans=None,  # 工具名
        fields={"role": S, "tool_call_id": S, "args_keys": S, "args": T},
    ),
    "tool_result": EventSpec(
        spans=None,
        fields={
            "role": S, "tool_call_id": S, "status": S, "result_bytes": S,
            # `truncated` 现在恒不出现：框架只在**异步** `arun` 路径调 truncator，
            # 本项目走同步 `run`（`chat_workflow` 的 `run_in_threadpool(agent.run, …)`），
            # 结果一字不截。留着这个字段是为了将来切到 arun 时不必改契约；
            # 眼下判断"观察是不是太大了"看 `result_bytes`。
            "truncated": S, "error_type": S, "result": T,
        },
    ),
    "store_write": EventSpec(
        spans=frozenset({
            "info_store", "soreness_store", "plan_draft_cache", "daily_record_store",
        }),
        fields={
            # `outcome` / `reason` 与 gate 同名同义：写成了 / 跳过了 / 失败了，以及为什么。
            "outcome": S, "reason": S,
            "entry_id": S, "user_confirmed": S,
            "capture": S, "ok": S, "error_type": S, "idempotency_key": S,
            # **恒为列表**（一次写入可能带多条事实）。计划表里原本写的是单数
            # `namespace`/`key`，但不同分支一个传 str 一个传 list 就是审查提到的
            # "同名异类型"——统一成复数列表，读方不用分情况处理。
            # 两者的取值都是本项目源码里的固定词表（health/injury_or_constraint/
            # recovery_status/chat_turn…），不是用户自由输入，所以按 structural 记。
            "namespaces": S, "keys": S,
        },
    ),
}



