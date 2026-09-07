"""observability — 回合级 agent trace（agent-trace 阶段 1）。

## 这个包是什么

把一次用户回合里发生的事记成可检索的事件流：HTTP 边界、7 个外部模型触点、所有
确定性闸门、两个 ReAct 循环，全部串在一个 `turn_id` 下。

替代 `hello_agents` 自带的 `TraceLogger`（阶段 0 已关掉）：那套按 Agent 实例分文件、
构造即开句柄、HTML 不转义，而且只覆盖 ReAct 主循环这 1/7。

## 模块边界

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `config` | 环境变量解析与校验 | 不碰文件系统 |
| `schema` | 事件契约：kind / span 枚举、字段类别、上限 | 不做裁剪 |
| `redact` | 按契约裁剪 payload、HMAC 摘要 | 不知道回合的存在 |
| `sink` | 目录校验、原子写、index 追加、保留策略与清理入口 | 不知道回合的存在 |
| `cost` | token 与成本汇总（纯函数） | 不知道回合的存在 |
| `trace` | `TurnTrace`、ContextVar、公开记录 API | 不直接碰文件、不算成本 |
| `http` | 哪些路由开 turn 的接入清单（纯数据） | 不含任何逻辑 |
| `model_trace` | 外部模型调用的记录器（一次调用一条事件） | 不知道具体是哪个供应商 |
| `react_trace` | 框架 ReAct 事件的翻译器（鸭子类型 sink） | 不落盘、不 import hello_agents |

**只从本包顶层 import**。子模块之间的依赖是单向的
（`trace` → `sink`/`cost` → `config`，`model_trace` / `react_trace` → `trace`），
从外部直接 import 子模块会让这条约束失效。

## 默认状态

`FITHEALTH_TRACE` 不设即全套停用：`trace_event()` 的代价是一次 `ContextVar.get()`，
`start_turn()` 不解析目录、不建目录、不写文件。阶段 1 结束时没有任何调用点，
全仓库行为与阶段 0 完全相同。
"""

from __future__ import annotations

from .config import (
    DETAIL_FULL,
    DETAIL_LEVELS,
    DETAIL_META,
    TraceSettings,
    load_settings,
    trace_dir,
)
from .cost import USAGE_KINDS, summarise_usage
from .redact import redact_payload, text_digest
from .http import TRACED_ROUTES
from .model_trace import SKIP_REASONS, ModelCall, endpoint_host, model_call
from .react_trace import (
    IGNORED_EVENTS,
    TRANSLATED_EVENTS,
    UNMAPPED_KIND,
    TurnTraceSink,
    attach_react_trace,
)
from .schema import (
    AGENT_SPANS,
    EVENT_SPECS,
    LIMITS,
    MODEL_CALL_SPANS,
    SCHEMA_VERSION,
)
from .sink import INDEX_NAME, TURN_PREFIX, PruneResult, TraceStore, prune
from .trace import (
    TZ,
    RESERVED_TURN_END_FIELDS,
    TurnTrace,
    current_turn,
    new_turn_id,
    set_turn_result,
    span,
    start_turn,
    trace_event,
)


__all__ = [
    "AGENT_SPANS",
    "DETAIL_FULL",
    "DETAIL_LEVELS",
    "DETAIL_META",
    "EVENT_SPECS",
    "IGNORED_EVENTS",
    "INDEX_NAME",
    "LIMITS",
    "MODEL_CALL_SPANS",
    "ModelCall",
    "PruneResult",
    "RESERVED_TURN_END_FIELDS",
    "SCHEMA_VERSION",
    "SKIP_REASONS",
    "TRACED_ROUTES",
    "TRANSLATED_EVENTS",
    "TURN_PREFIX",
    "TZ",
    "TraceSettings",
    "TraceStore",
    "TurnTrace",
    "TurnTraceSink",
    "UNMAPPED_KIND",
    "USAGE_KINDS",
    "attach_react_trace",
    "current_turn",
    "endpoint_host",
    "load_settings",
    "model_call",
    "new_turn_id",
    "prune",
    "redact_payload",
    "set_turn_result",
    "span",
    "start_turn",
    "summarise_usage",
    "text_digest",
    "trace_dir",
    "trace_event",
]
