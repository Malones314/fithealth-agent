"""阶段 0 的契约与不变量测试（agent-trace）。

阶段 0 只做一件事：给 `ReActAgent` 显式传一份冻结的 `Config`，把框架默认全开的
6 个可选子系统关掉。它同时修掉三个问题：

* TRACE-10：不再往 registry 里注册 `Skill` / `Task` / `TodoWrite` / `DevLog`，
  发给模型的 schema 从 18 个回到 14 个；
* TRACE-07/08：不再创建框架的文件式 `TraceLogger`，于是没有"构造即开两个句柄、
  只在特定返回路径 finalize"的泄漏，也没有未转义的 HTML 事件片段；
* `memory/sessions/session-error.json` 这类含对话历史的崩溃转储不再产生。

这三条都是**行为**，所以这里全部用行为断言：构造真实 agent，比对运行时产出的
schema 与配置快照，并在正常、未运行、崩溃、中断四条路径上检查落盘目录。

契约快照的生成与再生成见 `tests/agent_snapshot.py`。
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.agent_snapshot import (
    FRAMEWORK_OUTPUT_DIRS,
    build_snapshot,
    isolated_agent_sandbox,
    load_snapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[1]

#: 框架自动注册的四个内置工具。本项目没有实现其中任何一个对应的能力。
FRAMEWORK_BUILTIN_TOOLS = ("Skill", "Task", "TodoWrite", "DevLog")


def _files_under(root: Path) -> list[str]:
    """沙箱里出现的所有文件（相对路径，正斜杠），目录本身不计。"""
    return sorted(
        item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()
    )


def _build_agent():
    from fithealth_agent.agent import create_fithealth_agent

    return create_fithealth_agent()


class AgentContractSnapshotTest(unittest.TestCase):
    """工具 schema 与生效配置的逐项比对。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.current = build_snapshot()
        cls.expected = load_snapshot()

    def test_framework_version_is_unchanged(self) -> None:
        """框架升级会改动默认值，必须先失败一次再由人决定要不要接受。"""
        self.assertEqual(self.current["framework_version"], self.expected["framework_version"])

    def test_no_tool_was_lost_or_added(self) -> None:
        current = set(self.current["tool_names"])
        expected = set(self.expected["tool_names"])
        self.assertEqual(sorted(expected - current), [], "这些工具不见了")
        self.assertEqual(sorted(current - expected), [], "这些工具是多出来的")

    def test_every_tool_keeps_its_full_schema(self) -> None:
        """只比名字不够：description、参数类型、默认值、required 都是契约。"""
        current = {item["function"]["name"]: item for item in self.current["tool_schemas"]}
        expected = {item["function"]["name"]: item for item in self.expected["tool_schemas"]}
        for name in sorted(set(expected) & set(current)):
            with self.subTest(tool=name):
                self.assertEqual(current[name], expected[name])

    def test_tool_schema_digest_is_unchanged(self) -> None:
        self.assertEqual(
            self.current["tool_schema_digest"], self.expected["tool_schema_digest"]
        )

    def test_the_four_framework_builtin_tools_are_not_registered(self) -> None:
        """TRACE-10 的直接断言：数字对得上也可能是换了一个进来。"""
        for name in FRAMEWORK_BUILTIN_TOOLS:
            with self.subTest(tool=name):
                self.assertNotIn(name, self.current["tool_names"])
        self.assertEqual(len(self.current["tool_names"]), 14)

    def test_effective_config_is_unchanged(self) -> None:
        """整份 dump 逐字段比：阶段 0 只显式锁了 13 个，其余靠这条挡漂移。"""
        current = self.current["config"]
        expected = self.expected["config"]
        self.assertEqual(sorted(current), sorted(expected), "配置字段集合变了")
        for field in sorted(expected):
            with self.subTest(field=field):
                self.assertEqual(current[field], expected[field])

    def test_agent_runtime_defaults_are_unchanged(self) -> None:
        self.assertEqual(self.current["agent_runtime"], self.expected["agent_runtime"])


class FrameworkSubsystemsAreOffTest(unittest.TestCase):
    """四条执行路径上都不许出现框架的落盘产物。"""

    def test_framework_trace_logger_is_disabled(self) -> None:
        with isolated_agent_sandbox():
            self.assertIsNone(_build_agent().trace_logger)

    def test_session_and_skill_subsystems_are_disabled(self) -> None:
        with isolated_agent_sandbox():
            agent = _build_agent()
        self.assertIsNone(agent.session_store, "session_store 还在，崩溃时会转储对话历史")
        self.assertIsNone(agent.skill_loader)

    def test_construction_creates_no_framework_output_dirs(self) -> None:
        with isolated_agent_sandbox() as sandbox:
            _build_agent()
            created = [name for name in FRAMEWORK_OUTPUT_DIRS if (sandbox / name).exists()]
            self.assertEqual(created, [], "构造 agent 就创建了这些目录")

    def test_a_never_run_agent_writes_nothing(self) -> None:
        """`chat_workflow` 建好 agent 后可能因 ContextInputError 直接返回，
        agent 从未 run。旧行为在这条路径上留下一对 trace 文件（HTML 还没有尾部）。"""
        with isolated_agent_sandbox() as sandbox:
            agent = _build_agent()
            self.assertIsNone(agent.trace_logger)
            self.assertEqual(_files_under(sandbox), [])

    def test_a_crashing_run_leaves_no_session_dump(self) -> None:
        """`ReActAgent.run` 的 except 分支会 `save_session("session-error")`——
        `memory/sessions/session-error.json` 就是这么来的，里面是对话历史。"""
        with isolated_agent_sandbox() as sandbox:
            agent = _build_agent()
            with mock.patch.object(agent, "_run_impl", side_effect=RuntimeError("boom")):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(RuntimeError):
                        agent.run("查看本周数据")
            self.assertEqual(_files_under(sandbox), [], "崩溃路径写了文件")

    def test_an_interrupted_run_leaves_no_session_dump(self) -> None:
        """取消路径走的是另一个分支：`save_session("session-interrupted")`。"""
        with isolated_agent_sandbox() as sandbox:
            agent = _build_agent()
            with mock.patch.object(agent, "_run_impl", side_effect=KeyboardInterrupt):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(KeyboardInterrupt):
                        agent.run("查看本周数据")
            self.assertEqual(_files_under(sandbox), [], "取消路径写了文件")

    def test_the_truncation_directory_is_anchored_and_starts_empty(self) -> None:
        """框架在 __init__ 里无条件 makedirs(tool_output_dir)。

        两条不变量：目录必须锚在数据目录下（否则会跟着 CWD 跑，容器重建即丢失，
        见 `tool_output.py`），且构造阶段必须是空的——超过 50 KiB 的工具输出会把
        未截断原文写进去，那里面是训练与健康记录。
        """
        from fithealth_agent.tool_output import tool_output_dir

        with isolated_agent_sandbox() as sandbox:
            agent = _build_agent()
            target = Path(agent.config.tool_output_dir)
            self.assertTrue(target.is_absolute(), target)
            self.assertEqual(target, tool_output_dir())
            self.assertFalse(
                target.is_relative_to(sandbox), "溢出目录跟着 CWD 跑了"
            )
            self.assertEqual(sorted(item.name for item in target.glob("*")), [])


class ToolOutputOverflowTest(unittest.TestCase):
    """溢出目录的解析与清理（`/data/reset` 第 11 步依赖这两件事）。"""

    def setUp(self) -> None:
        from fithealth_agent import settings, tool_output

        self.settings = settings
        self.tool_output = tool_output
        self.previous = os.environ.get(settings.DATA_DIR_ENV)
        self.temp = tempfile.TemporaryDirectory(prefix="fithealth-tool-output-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._restore)
        os.environ[settings.DATA_DIR_ENV] = self.temp.name
        self.root = Path(self.temp.name) / tool_output.DIR_NAME
        self.root.mkdir(parents=True)

    def _restore(self) -> None:
        if self.previous is None:
            os.environ.pop(self.settings.DATA_DIR_ENV, None)
        else:
            os.environ[self.settings.DATA_DIR_ENV] = self.previous

    def test_the_directory_follows_the_data_dir_on_every_call(self) -> None:
        """不能有模块级缓存：`settings.data_dir()` 的契约是每次重读环境变量。"""
        self.assertEqual(self.tool_output.tool_output_dir(), self.root)
        with tempfile.TemporaryDirectory() as other:
            os.environ[self.settings.DATA_DIR_ENV] = other
            self.assertEqual(
                self.tool_output.tool_output_dir(),
                Path(other) / self.tool_output.DIR_NAME,
            )

    def test_clear_removes_overflow_files_and_counts_them(self) -> None:
        for name in ("tool_20260904_1_query_sleep.json", "tool_20260904_2_query_x.json"):
            (self.root / name).write_text("{}", encoding="utf-8")
        self.assertEqual(self.tool_output.clear_tool_output(), 2)
        self.assertEqual(sorted(item.name for item in self.root.glob("*")), [])

    def test_clear_leaves_unrelated_files_and_the_directory_itself(self) -> None:
        """这个目录名和仓库根的人工临时目录同名过，不该顺手抹掉别人的东西。"""
        (self.root / "notes.md").write_text("keep me", encoding="utf-8")
        (self.root / "tool_20260904_1_query_sleep.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.tool_output.clear_tool_output(), 1)
        self.assertEqual(sorted(item.name for item in self.root.glob("*")), ["notes.md"])
        self.assertTrue(self.root.is_dir())

    def test_clear_on_a_missing_directory_is_a_no_op(self) -> None:
        shutil.rmtree(self.root)
        self.assertEqual(self.tool_output.clear_tool_output(), 0)


class IgnoreRulesTest(unittest.TestCase):
    """忽略清单只防误提交，但那道防线本身要有断言。

    这里问 git "你到底忽略不忽略"，而不是去读 `.gitignore` 的文本——文本对不对
    不重要，实际生效才重要（`memory/devlogs/` 之前正是漏在这里）。
    """

    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("环境里没有 git")

    def _is_ignored(self, relative: str) -> bool:
        completed = subprocess.run(
            ["git", "check-ignore", "-q", relative],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        return completed.returncode == 0

    def test_git_ignores_every_framework_output_dir(self) -> None:
        for relative in FRAMEWORK_OUTPUT_DIRS:
            with self.subTest(path=relative):
                self.assertTrue(
                    self._is_ignored(f"{relative}/placeholder"),
                    f"{relative}/ 没有被忽略；它的内容含对话历史与健康细节",
                )

    def test_git_ignores_the_tool_output_overflow_dir(self) -> None:
        """`data/` 不是整目录忽略的（只列了几个 glob），新增子目录必须自己加一行。"""
        self.assertTrue(
            self._is_ignored("data/tool-output/tool_20260904_120000_query_sleep.json"),
            "data/tool-output/ 没有被忽略；里面是未截断的训练与健康记录",
        )

    def test_git_ignores_the_trace_and_recovery_point_dirs(self) -> None:
        """两个同型的子目录（agent-trace 阶段 6 一并补上）。

        `data/traces/` 是 `FITHEALTH_TRACE_DIR` 的默认值——`full` 级别会往里落用户
        原文；`data/recovery-points/` 是 `/data/reset` 前的完整数据快照 zip，而
        `data/*.json` 这类 glob 拦不住子目录里的 `.zip`。
        """
        for relative in (
            "data/traces/2026-09-05/turn-t-20260905-101929-74ca3f.jsonl",
            "data/traces/index.jsonl",
            "data/recovery-points/pre-reset-20260905120000.zip",
        ):
            with self.subTest(path=relative):
                self.assertTrue(self._is_ignored(relative), f"{relative} 没有被忽略")


if __name__ == "__main__":
    unittest.main()
