"""agent.py — FitHealthAgent 主体工厂函数

职责：
    组装并返回一个配置完毕的 ReActAgent 实例，包括：
    - LLM 后端（``TrackedLLM``：记下框架会吞掉的那次调用异常）
    - 框架配置（``agent_config()``，显式冻结，见 ``_FROZEN_CONFIG`` 的注释）
    - 内置工具：数据存取、FIT 文件组编辑（更新/合并）
    - YouTube 视频搜索工具（原生 Tool 子类，无需任何 MCP 客户端扩展）
    - 本项目的 trace sink（``attach_react_trace``，见 agent-trace 阶段 5）

设计说明：
    YouTube 搜索直接通过 ``YouTubeSearchTool`` 实现，该类继承自
    ``hello_agents.tools.Tool``，与框架 1.0.0 完全兼容，不依赖
    ``MCPTool`` 或任何框架扩展特性。
    MCP Server（``mcp_servers/youtube_server.py``）保留用于将来
    若需要以 MCP 协议对外暴露该能力时使用。
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stdout
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from hello_agents import HelloAgentsLLM, ReActAgent, ToolRegistry
from hello_agents.core.config import Config
from hello_agents.core.llm_adapters import OpenAIAdapter

from .fit_tools import (
    DeleteSetTool,
    MergeSetsTool,
    RestoreParsedSourceTool,
    UndoLastEditTool,
    UpdateSetTool,
)
from .health_store import HealthStore
from .health_tools import (
    QueryDailyHealthTool,
    QueryHealthRangeTool,
    QueryHeartRateWindowTool,
    QuerySleepTool,
)
from .observability import attach_react_trace
from .prompts import SYSTEM_PROMPT
from .settings import AgentRuntimeSettings, load_agent_runtime_settings
from .storage import DailyRecordStore
from .tools import QueryDailyRecordsTool, SaveDailyRecordTool
from .tool_output import tool_output_dir
from .youtube_tool import YouTubeSearchTool


#: 显式冻结的框架配置（agent-trace 阶段 0）。
#:
#: 为什么必须显式传：`ReActAgent(config=None)` 会在 `hello_agents/core/agent.py:43`
#: 落到 `Config()` 默认值，而框架把 6 个可选子系统**默认全开**。这不是理论问题，
#: 三条后果都已实测复现：
#:
#: * TRACE-10：`skills / subagent / todowrite / devlog` 打开后，`Agent.__init__`
#:   会往 registry 里注册 `Skill` / `Task` / `TodoWrite` / `DevLog` 四个工具，
#:   于是每次请求向模型多发 4 个 schema（本项目 12 个工具 + Thought + Finish
#:   = 14，实际发出 18）。`skills/` 是空目录，全仓库没有任何一处引用这四个
#:   能力——模型真去调它们只会拿到无意义结果。
#: * TRACE-07/08：`trace_enabled` 打开后，`TraceLogger.__init__` 立即 open 两个
#:   文件句柄，而 `finalize()` 只在 `_run_impl` 的返回路径调用。`chat_workflow`
#:   建好 agent 后若 `build_agent_input` 抛 `ContextInputError`，agent 从未 run，
#:   句柄泄漏且 HTML 没有尾部；HTML 事件片段还未做转义。trace 由本项目自己的
#:   observability 层接管——阶段 5 已把一个同接口、`finalize()` 为空操作的 sink 装回
#:   `agent.trace_logger`（见 `create_fithealth_agent` 结尾），框架这套文件式实现
#:   不能留着。
#: * `session_enabled` 打开后，异常路径会把整段对话历史写进
#:   `memory/sessions/session-error.json`——那里面是健康隐私，且不受
#:   `/data/reset` 与备份事务管辖。
#:
#: 开关之外还**显式锁定**截断、压缩与日志三组参数。它们当前的值与框架默认值
#: 一致（所以本次改动不改变行为），锁定的目的是防框架升级时默认值漂移：
#: `enable_smart_compression` 一旦变成 True 就会凭空多出一次 LLM 调用，
#: 而截断上限直接决定 ReAct 观察会不会把上下文撑爆。
#:
#: 唯一一个路径字段 `tool_output_dir` **不在这里**，见下面的 `agent_config()`。
#:
#: 任何字段改动都会被 `tests/baseline/agent_snapshot.json` 挡住。
_FROZEN_CONFIG = Config(
    # ── 6 个可选子系统：本项目一个都不用 ────────────────────────────────
    trace_enabled=False,
    skills_enabled=False,
    subagent_enabled=False,
    todowrite_enabled=False,
    devlog_enabled=False,
    session_enabled=False,
    # ── 工具输出截断：决定 ReAct 观察的体积上限 ──────────────────────────
    tool_output_max_lines=2000,
    tool_output_max_bytes=51200,
    tool_output_truncate_direction="head",
    # ── 历史压缩：smart 压缩会额外调一次 LLM，必须显式关掉 ───────────────
    enable_smart_compression=False,
    # ── 日志：debug=True 会让框架往 stdout 打更多内容 ────────────────────
    debug=False,
    log_level="INFO",
)


def agent_config() -> Config:
    """在冻结模板上补一个**每次重新解析**的绝对路径。

    为什么 `tool_output_dir` 不能写进模块级常量：`settings.data_dir()` 的契约是每
    次调用都重读 `FITHEALTH_DATA_DIR`（见 `settings.py` 的"约定"一节）。冻在
    import 时会让测试切换数据目录失效，也会让数据落在哪取决于 import 顺序。

    为什么必须给绝对路径：框架默认的 `"tool-output"` 是相对 CWD 的，而
    `ObservationTruncator.__init__` 无条件 `makedirs` 它，超限的工具输出原文（含
    训练与健康记录）就落在那里——与 DATA-10 / ARCH-03 同型的问题。详见
    `tool_output.py`。
    """
    return _FROZEN_CONFIG.model_copy(
        update={"tool_output_dir": str(tool_output_dir())}
    )


class TrackedLLM(HelloAgentsLLM):
    """记住最后一次模型调用异常——因为框架会把它**吞掉**。

    `ReActAgent._run_impl` 给 `invoke_with_tools` 包了
    `except Exception: print(...); break`（`react_agent.py:181-189`），异常既不重抛
    也不改变返回值：循环 break 之后**照走"达到最大步数"那条收尾路径**
    （`:365-387`），`agent.run` 于是正常返回一句
    "抱歉，我无法在限定步数内完成这个任务。"。

    后果是一个用户可见的缺陷：模型服务故障（超时、502、缺 key、余额不足）会显示成
    HTTP 200 加一句"任务太复杂"，而 `chat_workflow` 那条
    `except HelloAgentsException → 503 "模型服务当前不可用"` 分支**永远不会触发**。

    本类只做一件事：记下异常再原样抛出。怎么处理是调用方的事——见
    `swallowed_model_failure()` 与 `chat_workflow` 里的两处调用点。

    **只覆盖同步入口**就够了：框架的 `ainvoke_with_tools` 是
    `run_in_executor(lambda: self.invoke_with_tools(...))`（`core/llm.py:269-282`），
    异步路径最终还是走这里。`tests/test_model_failure_surfacing.py` 有一条用例钉住
    这条委派关系，框架哪天改成独立实现就会先红。
    """

    def __init__(self, *args, max_retries: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.max_retries = max_retries
        self._configure_openai_retries()
        #: 最后一次失败的模型调用异常；从未失败时为 None。**不重置**——一个 agent
        #: 实例只服务一次回合（`chat_workflow` 每次请求各建一个），"这一轮挂过没有"
        #: 正是调用方要问的问题。
        self.last_failure: BaseException | None = None

    def _configure_openai_retries(self) -> None:
        """Apply retries lazily; HelloAgents 1.0.0 drops extra LLM kwargs."""
        adapter = self._adapter
        if not isinstance(adapter, OpenAIAdapter):
            return

        create_client = adapter.create_client
        create_async_client = adapter.create_async_client
        adapter.create_client = lambda: create_client().with_options(
            max_retries=self.max_retries
        )
        adapter.create_async_client = lambda: create_async_client().with_options(
            max_retries=self.max_retries
        )

    def invoke_with_tools(self, *args, **kwargs):
        try:
            return super().invoke_with_tools(*args, **kwargs)
        except Exception as exc:
            self.last_failure = exc
            raise


def swallowed_model_failure(agent: object) -> BaseException | None:
    """取框架吞掉的那次模型调用异常，没有则 None。绝不抛。

    逐层 `getattr`：调用方拿到的可能是测试里的假 agent，也可能是别处构造的、没装
    `TrackedLLM` 的实例——那两种情况都应当是"没有已知故障"，而不是崩在诊断代码上。
    """
    failure = getattr(getattr(agent, "llm", None), "last_failure", None)
    return failure if isinstance(failure, BaseException) else None


def create_fithealth_agent(
    *,
    avoid_youtube_channels: Iterable[str] | None = None,
    role: str = "agent",
    runtime_settings: AgentRuntimeSettings | None = None,
) -> ReActAgent:
    """创建并返回配置完毕的 FitHealthAgent 实例。

    该函数执行以下步骤：
    1. 初始化 LLM 后端与数据存储层。
    2. 注册所有内置工具（数据存取 + FIT 文件编辑）。
    3. 注册 YouTubeSearchTool（原生 Tool 子类，直接调用 YouTube Data API v3）。
    4. 组装 ReActAgent，并装上本项目的 trace sink（agent-trace 阶段 5）。

    Args:
        avoid_youtube_channels: 用户明确不想看的频道，交给 YouTube 工具过滤。
        role: 这个 agent 在一次回合里的角色，也是 trace 事件的 span。
            `"agent"` 是主循环，`"correction_agent"` 是计划自动修正循环
            （`chat_workflow` 里那一处）。两者共享同一个 `turn_id`，靠它区分——
            取值范围见 `observability.AGENT_SPANS`，写别的值会让事件带
            `_unknown_span` 而不是静默归错类。
        runtime_settings: 可选的已校验运行参数。未传时，每次创建 Agent 都重新读取
            环境变量，便于部署配置和测试覆盖。

    Returns:
        已完成初始化的 ReActAgent 实例，可直接调用 .run() 处理用户消息。
    """
    settings = runtime_settings or load_agent_runtime_settings()
    llm = TrackedLLM(
        model=os.getenv("LLM_MODEL_ID") or "deepseek-chat",
        base_url=os.getenv("LLM_BASE_URL") or "https://api.deepseek.com",
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    )
    store = DailyRecordStore()
    health_store = HealthStore()

    registry = ToolRegistry()

    # ── 内置工具：数据存取 ────────────────────────────────────────────────
    # hello-agents 1.0.0 prints an emoji for every registration. Redirecting
    # library noise also avoids UnicodeEncodeError on Windows GBK consoles.
    with redirect_stdout(io.StringIO()):
        registry.register_tool(SaveDailyRecordTool(store))
        registry.register_tool(QueryDailyRecordsTool(store))

        # ── 全天健康与睡眠查询（SQLite 汇总，不向模型暴露原始时间序列） ───
        registry.register_tool(QueryDailyHealthTool(health_store))
        registry.register_tool(QueryHealthRangeTool(health_store))
        registry.register_tool(QuerySleepTool(health_store))
        registry.register_tool(QueryHeartRateWindowTool(health_store))

        # ── 内置工具：FIT 文件组编辑 ─────────────────────────────────────
        registry.register_tool(UpdateSetTool())
        registry.register_tool(MergeSetsTool())
        registry.register_tool(DeleteSetTool())
        registry.register_tool(UndoLastEditTool())
        registry.register_tool(RestoreParsedSourceTool())

        # ── YouTube 视频搜索（原生 Tool，无需 MCPTool）────────────────────
        registry.register_tool(YouTubeSearchTool(avoid_channels=avoid_youtube_channels))

    with redirect_stdout(io.StringIO()):
        current_time = datetime.now(ZoneInfo("Asia/Shanghai"))
        runtime_system_prompt = (
            SYSTEM_PROMPT
            + "\n\n## Current time\n"
            + f"Current date: {current_time.date().isoformat()}\n"
            + f"Current time: {current_time.isoformat(timespec='seconds')} (Asia/Shanghai)\n"
            + "Resolve relative dates such as today/now/this week using this anchor unless the user explicitly provides a date."
        )
        agent = ReActAgent(
            name="FitHealthAgent",
            llm=llm,
            tool_registry=registry,
            system_prompt=runtime_system_prompt,
            config=agent_config(),
            max_steps=settings.max_steps,
        )

    # 框架自己那套文件式 TraceLogger 在 `_FROZEN_CONFIG` 里已经关掉（阶段 0）。这里装
    # 一个同接口、**不落盘**的 sink，把 model_output / tool_call / tool_result /
    # session_end 引进当前回合，于是主 agent 与自动修正 agent 共享同一个 turn_id。
    #
    # 刻意放在 `redirect_stdout` 之外：被丢掉的是框架的 emoji，不该连我们自己的降级
    # 告警一起丢。trace 关闭时本调用是空操作，`agent.trace_logger` 留在 None
    # （见 `observability/react_trace.py`）。
    attach_react_trace(agent, role=role, static_system_prompt=SYSTEM_PROMPT)
    return agent
