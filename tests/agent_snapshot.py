"""`create_fithealth_agent()` 的框架契约快照（agent-trace 阶段 0）。

## 为什么要冻快照，而不是手写一份工具名清单

工具 schema 是**发给模型的契约**。`hello-agents 1.0.0` 把 6 个可选子系统默认全开，
其中 4 个会往 registry 里自动注册 `Skill` / `Task` / `TodoWrite` / `DevLog`——
本项目 12 个工具 + `Thought` + `Finish` 一共 14 个，实际发出去 18 个。这种漂移的
症状不是报错，而是"模型偶尔调了一个本项目没实现的能力"，或者"上下文莫名其妙更
贵了"。

手写清单会过期：加一个工具、改一句 description、改一个参数是否必填，手写的断言
都察觉不到。所以这里按 `route_snapshot.py` 的同一套做法，把**运行时真实产出**
冻成 `tests/baseline/agent_snapshot.json`，由
`tests/test_agent_tool_schema_contract.py` 每次跑测试都比一遍。

`config` 一栏同样进契约：阶段 0 显式锁定了 13 个字段，但框架升级可能改动**任何**
默认值，整份 dump 比对才能把这类漂移暴露成一次明确的失败。`framework_version`
一起记下来，让"升级导致的差异"和"我们自己改的差异"能一眼分开。

重新生成（**只在确实要改契约时**）::

    python -m tests.agent_snapshot --write

"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


SNAPSHOT_PATH = Path(__file__).resolve().parent / "baseline" / "agent_snapshot.json"

#: 构造 `HelloAgentsLLM` 需要 model / api_key / base_url 三者齐全，缺任何一个都会
#: 抛 `HelloAgentsException`。model 与 base_url 在 `agent.py` 里有硬编码回落，只有
#: api_key 没有。这里补一个**占位值**让对象能构造出来——快照全程不发任何请求。
#: 真实 key 在 `.env` 里，而测试进程刻意不 `load_dotenv()`。
_API_KEY_ENV = "LLM_API_KEY"
_API_KEY_PLACEHOLDER = "sk-agent-snapshot-placeholder-not-a-real-key"

#: 框架可选子系统的落盘目录，全部按 CWD 解析。构造一次 agent 之后这四个目录
#: **一个都不该出现**——它们的内容是对话历史与健康细节。
FRAMEWORK_OUTPUT_DIRS = (
    "memory/traces",
    "memory/sessions",
    "memory/todos",
    "memory/devlogs",
)

#: 阶段 5 起 `create_fithealth_agent` 会在 trace 打开时装一个 `TurnTraceSink`，
#: 于是 `trace_logger_type` 变成随环境而定。快照必须只描述**契约**，所以这里显式
#: 关掉 trace：否则谁的 shell 里开着 `FITHEALTH_TRACE`，重新生成的基线就会带上
#: `"TurnTraceSink"`，而那不是契约的一部分。
_TRACE_ENV = ("FITHEALTH_TRACE", "FITHEALTH_TRACE_DETAIL", "FITHEALTH_TRACE_STREAM")
_AGENT_RUNTIME_ENV = (
    "FITHEALTH_AGENT_MAX_STEPS",
    "LLM_TEMPERATURE",
    "LLM_MAX_TOKENS",
    "LLM_TIMEOUT",
    "LLM_MAX_RETRIES",
)


@contextlib.contextmanager
def isolated_agent_sandbox():
    """在临时 CWD + 占位 key + trace 关闭的状态下构造 agent，退出时原样恢复。

    为什么必须隔离 CWD：框架的 `ObservationTruncator.__init__` 直接
    `os.makedirs(tool_output_dir)`，而 `tool_output_dir` 是相对路径。不隔离的话，
    光是跑一次快照就会在仓库里留下目录；`FRAMEWORK_OUTPUT_DIRS` 的断言也就无从
    成立——分不清目录是这次构造出来的，还是仓库里本来就有的。

    为什么要关 trace：见 `_TRACE_ENV` 的说明。
    """
    previous_cwd = Path.cwd()
    previous_key = os.environ.get(_API_KEY_ENV)
    previous_trace = {name: os.environ.get(name) for name in _TRACE_ENV}
    previous_runtime = {name: os.environ.get(name) for name in _AGENT_RUNTIME_ENV}
    if previous_key is None:
        os.environ[_API_KEY_ENV] = _API_KEY_PLACEHOLDER
    for name in _TRACE_ENV:
        os.environ.pop(name, None)
    for name in _AGENT_RUNTIME_ENV:
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="fithealth-agent-snapshot-") as temp:
        os.chdir(temp)
        try:
            yield Path(temp)
        finally:
            os.chdir(previous_cwd)
            if previous_key is None:
                os.environ.pop(_API_KEY_ENV, None)
            for name, value in previous_trace.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            for name, value in previous_runtime.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _normalised(value):
    """递归排序字典键，让落盘的 JSON 与比对结果都不受插入顺序影响。"""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _config_view(agent) -> dict:
    """整份配置 dump 都进契约，不只是阶段 0 显式锁定的那 13 个字段。

    `tool_output_dir` 是唯一一个绝对路径字段，值随 `FITHEALTH_DATA_DIR` 变化——
    直接冻进快照就绑死了某台机器的临时目录。这里折叠成一个稳定记号，于是**契约**
    变成"它锚在数据目录下"，而不是"它等于某个字符串"；一旦有人改回相对路径或指
    到别的地方，折叠失败，快照立刻不一致。
    """
    from fithealth_agent.tool_output import tool_output_dir

    config = agent.config
    dump = config.model_dump() if hasattr(config, "model_dump") else config.dict()
    anchored = Path(tool_output_dir())
    dump["tool_output_dir"] = (
        "<data_dir>/tool-output"
        if Path(dump["tool_output_dir"]) == anchored
        else dump["tool_output_dir"]
    )
    return _normalised(dump)


def _tool_schema_view(agent) -> list[dict]:
    """按工具名排序。

    schema 的**顺序**由 registry 插入顺序决定，不属于契约（与路由快照相反——
    那里顺序会影响路径匹配，这里不会）。但 description 和每个参数的类型、默认值、
    是否必填都属于契约：它们直接决定模型会不会正确调用工具。
    """
    return sorted(
        (_normalised(schema) for schema in agent._build_tool_schemas()),
        key=lambda schema: schema["function"]["name"],
    )


def build_snapshot() -> dict:
    """构造一次真实 agent，产出可比对的契约视图。"""
    import hello_agents

    from fithealth_agent.agent import create_fithealth_agent

    with isolated_agent_sandbox() as sandbox:
        agent = create_fithealth_agent()
        leaked_dirs = sorted(
            relative
            for relative in FRAMEWORK_OUTPUT_DIRS
            if (sandbox / relative).exists()
        )
        # 截断目录由框架在 __init__ 里无条件创建，属于已知行为。现在它锚在数据目录
        # 下（不再跟着 CWD 跑），所以要去绝对路径上看，并且记它**是否为空**——超过
        # 50 KiB 的工具输出会把原文写进去，那里面是训练与健康记录。
        overflow_dir = Path(agent.config.tool_output_dir)
        overflow_files = (
            sorted(item.name for item in overflow_dir.glob("*"))
            if overflow_dir.is_dir()
            else None
        )
        # 隔离沙箱里不该出现任何文件：路径锚定之后，CWD 相对的产物应该一个都没有。
        sandbox_files = sorted(
            item.relative_to(sandbox).as_posix()
            for item in sandbox.rglob("*")
            if item.is_file()
        )

    schemas = _tool_schema_view(agent)
    return {
        "framework_version": getattr(hello_agents, "__version__", "unknown"),
        "agent_runtime": {
            "max_steps": agent.max_steps,
            "temperature": agent.llm.temperature,
            "max_tokens": agent.llm.max_tokens,
            "timeout": agent.llm.timeout,
            "max_retries": agent.llm.max_retries,
        },
        "tool_names": [schema["function"]["name"] for schema in schemas],
        "tool_schema_digest": _digest(schemas),
        "trace_logger_type": (
            type(agent.trace_logger).__name__ if agent.trace_logger is not None else None
        ),
        "framework_output_dirs_created": leaked_dirs,
        "cwd_relative_files_created": sandbox_files,
        "tool_output_dir_files": overflow_files,
        "config": _config_view(agent),
        "tool_schemas": schemas,
    }


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def main() -> int:
    write = "--write" in sys.argv
    current = build_snapshot()
    if write:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"已写入 {SNAPSHOT_PATH}"
            f"（{len(current['tool_names'])} 个工具，digest={current['tool_schema_digest']}）"
        )
        return 0

    if not SNAPSHOT_PATH.is_file():
        print(f"快照不存在：{SNAPSHOT_PATH}；先跑 --write 生成基线", file=sys.stderr)
        return 2
    if current == load_snapshot():
        print(f"契约一致（{len(current['tool_names'])} 个工具）")
        return 0
    print(
        "契约与快照不一致；跑 pytest tests/test_agent_tool_schema_contract.py 看逐条差异",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
