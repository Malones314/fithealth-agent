from __future__ import annotations

import os
import unittest
from unittest import mock

from fithealth_agent.settings import (
    AGENT_MAX_STEPS_ENV,
    LLM_MAX_RETRIES_ENV,
    LLM_MAX_TOKENS_ENV,
    LLM_TEMPERATURE_ENV,
    LLM_TIMEOUT_ENV,
    AgentRuntimeSettings,
    AgentSettingsError,
    load_agent_runtime_settings,
)
from tests.agent_snapshot import isolated_agent_sandbox


class AgentRuntimeSettingsTest(unittest.TestCase):
    def test_defaults_preserve_existing_agent_behavior(self) -> None:
        self.assertEqual(
            load_agent_runtime_settings({}),
            AgentRuntimeSettings(
                max_steps=15,
                temperature=0.7,
                max_tokens=None,
                timeout=90,
                max_retries=0,
            ),
        )

    def test_environment_values_are_parsed(self) -> None:
        settings = load_agent_runtime_settings(
            {
                AGENT_MAX_STEPS_ENV: " 24 ",
                LLM_TEMPERATURE_ENV: "0.2",
                LLM_MAX_TOKENS_ENV: "4096",
                LLM_TIMEOUT_ENV: "90",
                LLM_MAX_RETRIES_ENV: "1",
            }
        )
        self.assertEqual(
            settings,
            AgentRuntimeSettings(24, 0.2, 4096, 90, 1),
        )

    def test_blank_optional_values_use_defaults(self) -> None:
        settings = load_agent_runtime_settings(
            {
                AGENT_MAX_STEPS_ENV: " ",
                LLM_TEMPERATURE_ENV: "",
                LLM_MAX_TOKENS_ENV: " ",
                LLM_TIMEOUT_ENV: "",
                LLM_MAX_RETRIES_ENV: "",
            }
        )
        self.assertEqual(settings, AgentRuntimeSettings())

    def test_invalid_values_name_the_bad_environment_variable(self) -> None:
        cases = (
            (AGENT_MAX_STEPS_ENV, "0"),
            (AGENT_MAX_STEPS_ENV, "101"),
            (LLM_TEMPERATURE_ENV, "nan"),
            (LLM_TEMPERATURE_ENV, "2.1"),
            (LLM_MAX_TOKENS_ENV, "0"),
            (LLM_TIMEOUT_ENV, "ten"),
            (LLM_TIMEOUT_ENV, "601"),
            (LLM_MAX_RETRIES_ENV, "-1"),
            (LLM_MAX_RETRIES_ENV, "11"),
        )
        for name, value in cases:
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(AgentSettingsError, name):
                    load_agent_runtime_settings({name: value})

    def test_settings_are_read_again_for_each_call(self) -> None:
        environment = {
            AGENT_MAX_STEPS_ENV: "8",
            LLM_TEMPERATURE_ENV: "",
            LLM_MAX_TOKENS_ENV: "",
            LLM_TIMEOUT_ENV: "",
            LLM_MAX_RETRIES_ENV: "",
        }
        with mock.patch.dict(os.environ, environment):
            self.assertEqual(load_agent_runtime_settings().max_steps, 8)
            os.environ[AGENT_MAX_STEPS_ENV] = "9"
            self.assertEqual(load_agent_runtime_settings().max_steps, 9)

    def test_explicit_settings_reach_the_framework_objects(self) -> None:
        from fithealth_agent.agent import create_fithealth_agent

        configured = AgentRuntimeSettings(
            max_steps=7,
            temperature=0.1,
            max_tokens=2048,
            timeout=45,
            max_retries=1,
        )
        with isolated_agent_sandbox():
            agent = create_fithealth_agent(runtime_settings=configured)
        self.assertEqual(agent.max_steps, 7)
        self.assertEqual(agent.llm.temperature, 0.1)
        self.assertEqual(agent.llm.max_tokens, 2048)
        self.assertEqual(agent.llm.timeout, 45)
        self.assertEqual(agent.llm.max_retries, 1)

    def test_openai_clients_receive_the_configured_retry_count(self) -> None:
        from fithealth_agent.agent import TrackedLLM

        with mock.patch.dict(os.environ, {"LLM_API_KEY": "placeholder-not-a-real-key"}):
            llm = TrackedLLM(
                model="test-model",
                base_url="https://api.example.com",
                timeout=30,
                max_retries=1,
            )
        self.assertIsNone(llm._adapter._client, "客户端应保持延迟创建")
        client = llm._adapter.create_client()
        try:
            self.assertEqual(client.max_retries, 1)
            self.assertEqual(client.timeout, 30)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
