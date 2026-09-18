"""「哪个符号现在住在哪个文件」的唯一声明处（main.py 拆分：阶段 0）。

拆分计划要把 5402 行的 main.py 分成 runtime / domain / workflows / routes 四层。
有 25 个测试文件直接读 main.py 源码——要么 AST 抽单个函数执行（避免 import main
触发整条 LLM 依赖链，见 ARCH-02），要么断言跨函数的结构不变量（ARCH-09 治理后
刻意保留的资产）。

如果这些文件各自硬编码 ``REPO_ROOT / "main.py"``，那么每搬一批函数就要改一遍
25 处路径。这张表把路径收敛成两个字典：**每搬走一个符号只改这里一行。**

两张表分工不同，不能只维护函数名表：

- ``FUNCTION_HOME``：符号（顶层函数、模块级常量）的**定义模块**。给"抽出来
  单独执行"的行为测试用。
- ``CONSUMER_HOME``：跨函数结构不变量所在的**消费者模块**——调用顺序、缓存优先级、
  "这个分支必须排在那个 503 之前"这类断言，钉的是接线点而不是某个函数自己。
  一个消费者搬到 routes/ 或 workflows/ 之后，它的不变量也跟着走。

阶段 0 全部指向 main.py，与现状一致；后续阶段逐条改指向。
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"

MAIN = REPO_ROOT / "main.py"

RUNTIME_DIR = PACKAGE_DIR / "runtime"
DEPS = RUNTIME_DIR / "deps.py"
UPLOAD_IO = RUNTIME_DIR / "upload_io.py"
MIDDLEWARE = RUNTIME_DIR / "middleware.py"
RESPONSES = RUNTIME_DIR / "responses.py"

WORKFLOWS_DIR = PACKAGE_DIR / "workflows"
CHAT_WORKFLOW = WORKFLOWS_DIR / "chat_workflow.py"

ROUTES_DIR = PACKAGE_DIR / "routes"
CHAT_ROUTE = ROUTES_DIR / "chat.py"
LOGOUT_WORKFLOW = WORKFLOWS_DIR / "logout_workflow.py"
UPLOAD_WORKFLOW = WORKFLOWS_DIR / "upload_workflow.py"
LOGOUT_ROUTE = ROUTES_DIR / "logout.py"
UPLOADS_ROUTE = ROUTES_DIR / "uploads.py"
WORKOUT_STATE_ROUTE = ROUTES_DIR / "workout_state.py"
RECORDS_ROUTE = ROUTES_DIR / "records.py"
PLANS_ROUTE = ROUTES_DIR / "plans.py"
MEMORIES_ROUTE = ROUTES_DIR / "memories.py"
HEALTH_ROUTE = ROUTES_DIR / "health.py"
SETTINGS_ROUTE = ROUTES_DIR / "settings.py"
MAINTENANCE_OPS_ROUTE = ROUTES_DIR / "maintenance_ops.py"

DOMAIN_DIR = PACKAGE_DIR / "domain"
PLAN_VALIDATION = DOMAIN_DIR / "plan_validation.py"
PLAN_CONTEXT = DOMAIN_DIR / "plan_context.py"
INTENT_RULES = DOMAIN_DIR / "intent_rules.py"
PROFILE_RULES = DOMAIN_DIR / "profile_rules.py"
MEMORY_VIEW = DOMAIN_DIR / "memory_view.py"
RECOVERY_VIEW = DOMAIN_DIR / "recovery_view.py"
RECORD_VIEW = DOMAIN_DIR / "record_view.py"
SEGMENT_MERGE = DOMAIN_DIR / "segment_merge.py"

# ── agent-trace 阶段 0-1 新增的模块 ─────────────────────────────────────
# 这些不是 main.py 拆分搬来的，但同样有"别的模块按名字依赖它"的关系：
# `maintenance_ops._reset_steps` 按名字取 `tool_output.clear_tool_output`，
# 后续 5 个阶段的 ~15 个调用点按名字取 `trace_event` / `span`。改名或换模块
# 的症状都是静默失效，所以一并纳入登记。
TOOL_OUTPUT = PACKAGE_DIR / "tool_output.py"

OBSERVABILITY_DIR = PACKAGE_DIR / "observability"
TRACE = OBSERVABILITY_DIR / "trace.py"
TRACE_CONFIG = OBSERVABILITY_DIR / "config.py"
TRACE_SCHEMA = OBSERVABILITY_DIR / "schema.py"
TRACE_REDACT = OBSERVABILITY_DIR / "redact.py"
TRACE_SINK = OBSERVABILITY_DIR / "sink.py"
TRACE_HTTP = OBSERVABILITY_DIR / "http.py"
TRACE_MODEL = OBSERVABILITY_DIR / "model_trace.py"
TRACE_REACT = OBSERVABILITY_DIR / "react_trace.py"
TRACE_COST = OBSERVABILITY_DIR / "cost.py"
AGENT_FACTORY = PACKAGE_DIR / "agent.py"


# ══════════════════════════════════════════════════════════════════════════
# FUNCTION_HOME：符号 -> 定义模块
# ══════════════════════════════════════════════════════════════════════════
#
# 分组注释对应拆分计划第 2 节的目标模块。搬迁时把该组的 MAIN 换成新路径即可，
# 组内顺序与计划里的阶段 3a–3d 一致。

FUNCTION_HOME: dict[str, Path] = {
    # ---- 通用 HTTP 响应（阶段 2 → runtime/responses.py）
    "context_error_response": RESPONSES,
    # ---- 计划校验与计划上下文（阶段 3a → domain/plan_validation.py, plan_context.py）
    "validate_generated_training_plan": PLAN_VALIDATION,
    "looks_like_complete_training_plan": PLAN_VALIDATION,
    "most_recent_complete_training_plan": PLAN_VALIDATION,
    "infer_training_subject": PLAN_VALIDATION,
    "is_generic_training_subject": PLAN_VALIDATION,
    "plan_card_title": PLAN_VALIDATION,
    "_exercise_set_counts": PLAN_VALIDATION,
    "constraint_regions": PLAN_VALIDATION,
    "resolve_plan_context": PLAN_CONTEXT,
    "format_plan_context": PLAN_CONTEXT,
    "daily_schedule_constraint": PLAN_CONTEXT,
    "extract_iso_dates": PLAN_CONTEXT,
    "requested_plan_date": PLAN_CONTEXT,
    "scheduled_plan_for_message": PLAN_CONTEXT,
    "scheduled_weekly_entry_for_message": PLAN_CONTEXT,
    # ---- 意图兜底与档案（阶段 3b → domain/intent_rules.py, profile_rules.py）
    "is_training_related": INTENT_RULES,
    "is_training_record_query": INTENT_RULES,
    "is_profile_query": INTENT_RULES,
    "current_instruction_override": INTENT_RULES,
    "_is_safety_bypass_request": INTENT_RULES,
    "_SAFETY_BYPASS_PATTERN": INTENT_RULES,
    "validate_profile_tool_updates": PROFILE_RULES,
    "merge_profile_updates_with_existing": PROFILE_RULES,
    "equipment_change_preview": PROFILE_RULES,
    "profile_summary": PROFILE_RULES,
    # ---- 记忆与恢复（阶段 3c → domain/memory_view.py, recovery_view.py）
    "format_cross_session_memories": MEMORY_VIEW,
    "youtube_channels_to_avoid": MEMORY_VIEW,
    "confirmed_memory_profile": MEMORY_VIEW,
    "confirmed_weekly_schedule": MEMORY_VIEW,
    "confirmed_weekly_schedule_entry": PLAN_CONTEXT,
    "active_temporary_health_facts": MEMORY_VIEW,
    "explicitly_requested_recovery_regions": RECOVERY_VIEW,
    "_subject_recovery_regions": RECOVERY_VIEW,
    "_recovery_context_payload": RECOVERY_VIEW,
    "build_session_intro": SETTINGS_ROUTE,
    # ---- 记录视图与段合并（阶段 3d → domain/record_view.py, segment_merge.py）
    "_training_record_name": RECORD_VIEW,
    "_record_overview": RECORD_VIEW,
    "_training_record_items": RECORD_VIEW,
    "_requested_record_date": RECORD_VIEW,
    "_nutrition_record_items": RECORD_VIEW,
    "_nutrition_record_date": RECORD_VIEW,
    "_active_saved_segments": SEGMENT_MERGE,
    "_validate_saved_training_updates": SEGMENT_MERGE,
    "_weighted_average": SEGMENT_MERGE,
    "_segment_numbers": SEGMENT_MERGE,
    "_merged_segment_hr": SEGMENT_MERGE,
    "_merged_segment_numbers": SEGMENT_MERGE,
    "_merge_saved_training_segments": SEGMENT_MERGE,
    "_ADDITIVE_SEGMENT_FIELDS": SEGMENT_MERGE,
    "_PEAK_SEGMENT_FIELDS": SEGMENT_MERGE,
    "_WEIGHTED_SEGMENT_FIELDS": SEGMENT_MERGE,
    # ---- 上传编排（阶段 5 → workflows/upload_workflow.py）
    "select_autoload_activity": PACKAGE_DIR / "workflows" / "upload_workflow.py",
    "_health_import_message": PACKAGE_DIR / "workflows" / "upload_workflow.py",
    # ---- 其余（阶段 4/6）
    "build_agent_input": CHAT_WORKFLOW,
    "configured_model_name": SETTINGS_ROUTE,
    # ---- 工具输出溢出目录（agent-trace 阶段 0）
    "tool_output_dir": TOOL_OUTPUT,
    "clear_tool_output": TOOL_OUTPUT,
    # ---- trace 公开 API（agent-trace 阶段 1）。后续 5 个阶段的调用点按这些
    #      名字接线，改名或换模块必须先改这张表。
    "trace_event": TRACE,
    "start_turn": TRACE,
    "span": TRACE,
    "set_turn_result": TRACE,
    "current_turn": TRACE,
    "new_turn_id": TRACE,
    "TurnTrace": TRACE,
    "TZ": TRACE,
    "load_settings": TRACE_CONFIG,
    "trace_dir": TRACE_CONFIG,
    "TraceSettings": TRACE_CONFIG,
    "redact_payload": TRACE_REDACT,
    "text_digest": TRACE_REDACT,
    "EVENT_SPECS": TRACE_SCHEMA,
    "EventSpec": TRACE_SCHEMA,
    "LIMITS": TRACE_SCHEMA,
    "SCHEMA_VERSION": TRACE_SCHEMA,
    "reserve_turn_path": TRACE_SINK,
    "write_turn": TRACE_SINK,
    "append_event": TRACE_SINK,
    "append_index": TRACE_SINK,
    "ensure_writable": TRACE_SINK,
    "reset_writable_cache": TRACE_SINK,
    # ---- 保留策略与清理入口（agent-trace 阶段 6）。`maintenance_ops._reset_steps`
    #      按名字取 `deps.trace_store.clear`，`trace._finalise` 按名字取 `prune`。
    "prune": TRACE_SINK,
    "PruneResult": TRACE_SINK,
    "TraceStore": TRACE_SINK,
    "INDEX_NAME": TRACE_SINK,
    "TURN_PREFIX": TRACE_SINK,
    # ---- turn 的 HTTP 接入清单（agent-trace 阶段 2）
    "TRACED_ROUTES": TRACE_HTTP,
    # ---- 外部模型调用记录器（agent-trace 阶段 4）
    "model_call": TRACE_MODEL,
    "ModelCall": TRACE_MODEL,
    "SKIP_REASONS": TRACE_MODEL,
    "endpoint_host": TRACE_MODEL,
    "MODEL_CALL_SPANS": TRACE_SCHEMA,
    # ---- ReAct 内部（agent-trace 阶段 5）。sink 与 agent 工厂互相按名字依赖：
    #      `create_fithealth_agent` 调 `attach_react_trace`，后者鸭子类型实现框架
    #      `trace_logger` 的三个成员。改名的症状是"trace 里少了 ReAct 那一截"。
    "TurnTraceSink": TRACE_REACT,
    "attach_react_trace": TRACE_REACT,
    "TRANSLATED_EVENTS": TRACE_REACT,
    "IGNORED_EVENTS": TRACE_REACT,
    "AGENT_SPANS": TRACE_SCHEMA,
    "summarise_usage": TRACE_COST,
    "USAGE_KINDS": TRACE_COST,
    "create_fithealth_agent": AGENT_FACTORY,
    "agent_config": AGENT_FACTORY,
    # ---- 框架吞掉的 LLM 异常（阶段 5 附带修复）。`chat_workflow` 在 `agent.run`
    #      返回后按名字取 `swallowed_model_failure`；改名的症状是模型服务故障重新
    #      显示成 200 加一句"任务太复杂"。
    "TrackedLLM": AGENT_FACTORY,
    "swallowed_model_failure": AGENT_FACTORY,
}


# ══════════════════════════════════════════════════════════════════════════
# CONSUMER_HOME：消费者/接线点 -> 所在模块
# ══════════════════════════════════════════════════════════════════════════
#
# 键名尽量取消费者函数自己的名字，这样它搬到哪个文件、这里就改成哪个文件。
# 少数键（degraded_error_handlers、memory_fact_routes…）钉的是模块级接线而不是
# 单个函数，用描述性名字。

CONSUMER_HOME: dict[str, Path] = {
    # /chat：意图路由顺序、本地兜底分支位置、计划 artifact 门槛、
    # plan_draft_cache 优先级、JSONResponse 不外泄。阶段 4 拆分后指向
    # workflows/chat_workflow.py 与 routes/chat.py。
    "chat": CHAT_WORKFLOW,
    # 上传三条链路（阶段 5 → routes/uploads.py + workflows/upload_workflow.py）
    "upload_health": UPLOAD_WORKFLOW,
    "upload_fit": UPLOAD_WORKFLOW,
    # 计划保存端点（阶段 6 → routes/plans.py）
    "save_plan": PLANS_ROUTE,
    # 训练记录编辑（阶段 6 → routes/records.py）
    "update_training_record": RECORDS_ROUTE,
    "get_daily_overview": HEALTH_ROUTE,
    # 档案确认写入是唯一的写路径（阶段 6 → routes/settings.py）
    "confirm_profile_update": SETTINGS_ROUTE,
    # 首页开场白文案（阶段 6 → routes/settings.py）
    "session_intro": SETTINGS_ROUTE,
    "session_intro_copy": RECOVERY_VIEW,
    # 记忆 fact 寻址路由（阶段 6 → routes/memories.py）
    "memory_fact_routes": MEMORIES_ROUTE,
    # 待确认训练的隔离区与编辑动作（阶段 6 → routes/workout_state.py）
    "workout_state_routes": WORKOUT_STATE_ROUTE,
    # 备份导入与重置的事务性（阶段 6 → routes/maintenance_ops.py）
    "import_backup": MAINTENANCE_OPS_ROUTE,
    "reset_all_data": MAINTENANCE_OPS_ROUTE,
    "reset_steps": MAINTENANCE_OPS_ROUTE,
    # 两个 DegradedError 处理器（阶段 2 → runtime/middleware.py）
    "degraded_error_handlers": MIDDLEWARE,
}


#: 全部实现模块。给"这个函数/字符串必须已经彻底消失"这类**缺席断言**用——
#: 缺席断言不能只看一个文件，否则搬迁之后它会静默变成永真。
#: 拆分推进时把新模块追加进来。
IMPLEMENTATION_MODULES: tuple[Path, ...] = (
    MAIN,
    DEPS,
    UPLOAD_IO,
    MIDDLEWARE,
    RESPONSES,
    PLAN_VALIDATION,
    PLAN_CONTEXT,
    INTENT_RULES,
    PROFILE_RULES,
    MEMORY_VIEW,
    RECOVERY_VIEW,
    RECORD_VIEW,
    SEGMENT_MERGE,
    CHAT_WORKFLOW,
    CHAT_ROUTE,
    UPLOAD_WORKFLOW,
    LOGOUT_WORKFLOW,
    LOGOUT_ROUTE,
    UPLOADS_ROUTE,
    WORKOUT_STATE_ROUTE,
    RECORDS_ROUTE,
    PLANS_ROUTE,
    MEMORIES_ROUTE,
    HEALTH_ROUTE,
    SETTINGS_ROUTE,
    MAINTENANCE_OPS_ROUTE,
    # agent-trace 阶段 0-1（不是拆分搬来的，但同样被别的模块按名字依赖）
    TOOL_OUTPUT,
    TRACE,
    TRACE_CONFIG,
    TRACE_SCHEMA,
    TRACE_REDACT,
    TRACE_SINK,
    TRACE_HTTP,
    TRACE_MODEL,
    # agent-trace 阶段 5
    TRACE_REACT,
    TRACE_COST,
    AGENT_FACTORY,
)

#: **消费** `runtime.deps` 的模块。阶段 1 只有 main.py，阶段 4–6 会加上
#: workflows/ 和 routes/ 下的文件。
#:
#: 与 `IMPLEMENTATION_MODULES` 分开是必须的：`deps.py` 自己**必须**持有
#: `route_chat_intent` 这些名字（那正是它的职责），而消费者**必须不**持有——
#: 消费者留一份同名绑定，打在 deps 上的桩就会静默失效。
#: `tests/test_deps_stub_reachability.py` 按这张表做那条检查。
DEPS_CONSUMERS: tuple[Path, ...] = (
    CHAT_WORKFLOW,
    UPLOAD_WORKFLOW,
    WORKOUT_STATE_ROUTE,
    RECORDS_ROUTE,
    PLANS_ROUTE,
    MEMORIES_ROUTE,
    HEALTH_ROUTE,
    SETTINGS_ROUTE,
    MAINTENANCE_OPS_ROUTE,
)


def module_import_name(path: Path) -> str:
    """文件路径 → 可 import 的模块名（`main.py` → `main`，包内文件 → 点分路径）。"""
    relative = Path(path).resolve().relative_to(REPO_ROOT)
    return ".".join(relative.with_suffix("").parts)


def function_home(name: str) -> Path:
    """符号的定义模块。"""
    try:
        return FUNCTION_HOME[name]
    except KeyError:
        raise AssertionError(
            f"{name!r} 不在 FUNCTION_HOME 里。测试要抽一个新符号时，"
            f"请先在 tests/module_map.py 里声明它住在哪个文件。"
        ) from None


def consumer_home(key: str) -> Path:
    """结构不变量所在的消费者模块。"""
    try:
        return CONSUMER_HOME[key]
    except KeyError:
        raise AssertionError(
            f"{key!r} 不在 CONSUMER_HOME 里。新增跨函数结构断言时，"
            f"请先在 tests/module_map.py 里声明这个接线点住在哪个文件。"
        ) from None
