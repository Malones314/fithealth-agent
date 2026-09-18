from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PackageLazyImportTest(unittest.TestCase):
    def run_isolated(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(source)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def test_non_llm_modules_import_without_loading_hello_agents(self) -> None:
        result = self.run_isolated(
            """
            import builtins
            import sys

            real_import = builtins.__import__
            def guarded_import(name, *args, **kwargs):
                if name == "hello_agents" or name.startswith("hello_agents."):
                    raise AssertionError("LLM dependency imported eagerly: " + name)
                return real_import(name, *args, **kwargs)

            builtins.__import__ = guarded_import
            import fithealth_agent
            from fithealth_agent import create_fithealth_agent
            from fithealth_agent.storage import DailyRecordStore
            from fithealth_agent.context_budget import ContextInputError

            assert callable(create_fithealth_agent)
            assert DailyRecordStore.__module__ == "fithealth_agent.storage"
            assert ContextInputError.__module__ == "fithealth_agent.context_budget"
            assert "fithealth_agent.agent" not in sys.modules
            assert not any(
                name == "hello_agents" or name.startswith("hello_agents.")
                for name in sys.modules
            )
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_factory_delegates_only_when_called(self) -> None:
        result = self.run_isolated(
            """
            import sys
            import types

            import fithealth_agent
            assert "fithealth_agent.agent" not in sys.modules

            fake_agent = types.ModuleType("fithealth_agent.agent")
            calls = []
            def factory(*, avoid_youtube_channels=None, role="agent", runtime_settings=None):
                calls.append((avoid_youtube_channels, role, runtime_settings))
                return "agent-created"
            fake_agent.create_fithealth_agent = factory
            sys.modules["fithealth_agent.agent"] = fake_agent

            result = fithealth_agent.create_fithealth_agent(
                avoid_youtube_channels=["channel-a"]
            )
            assert result == "agent-created"
            # role 默认为主循环；自动修正循环传 "correction_agent"（agent-trace 阶段 5）。
            assert calls == [(["channel-a"], "agent", None)]
            assert (
                fithealth_agent.create_fithealth_agent(role="correction_agent")
                == "agent-created"
            )
            assert calls[-1] == (None, "correction_agent", None)
            configured = object()
            assert (
                fithealth_agent.create_fithealth_agent(runtime_settings=configured)
                == "agent-created"
            )
            assert calls[-1] == (None, "agent", configured)
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
