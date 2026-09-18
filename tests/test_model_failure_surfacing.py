"""模型调用失败必须显示成 503，而不是一句"任务太复杂"。

## 这是什么缺陷

`hello-agents 1.0.0` 的 `ReActAgent._run_impl` 给 `invoke_with_tools` 包了
`except Exception: print(...); break`（`react_agent.py:181-189`）：异常既不重抛也不改变
返回值，循环 break 之后**照走"达到最大步数"那条收尾路径**（`:365-387`），于是
`agent.run` 正常返回一句"抱歉，我无法在限定步数内完成这个任务。"。

后果有两个，都是用户可见的：

1. 模型服务故障（超时 / 502 / 缺 key / 余额不足）显示成 **HTTP 200 + "任务太复杂"**，
   而 `chat_workflow` 那条 `except HelloAgentsException → 503 "模型服务当前不可用"`
   分支**永远不会触发**——用户不知道该重试还是该改问法；
2. 自动修正循环里同样如此，`gate/auto_correction.stop_reason` 会记成 `not_a_plan`
   （模型能力问题），而真相是 `call_failed`（基础设施问题）。

本项目不 patch `hello_agents`（会破坏 `vendor/` 的 wheel 校验约束），所以用
`agent.TrackedLLM` 记下那次异常，`chat_workflow` 在 `agent.run` 返回后检查它。

## 三层验证

1. `TrackedLLMTest`——记录器本身：记下并原样抛出，成功调用不留痕；
2. `FrameworkSwallowsLlmErrorsTest`——**钉住我们绕开的那个框架行为**。框架哪天改成
   重抛，这条会先红，届时可以把绕行代码删掉；
3. `ChatSurfacesModelFailureTest`——真实 `/chat`：故障 → 503，正常 → 200，修正循环
   故障 → 明确的"自动修正服务调用失败"而不是"结果不是完整训练计划"。
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import main
from fithealth_agent.agent import TrackedLLM, swallowed_model_failure
from fithealth_agent.chat_intent_router import ChatIntent
from fithealth_agent.runtime import deps
from fithealth_agent.soreness_store import SorenessStore
from fithealth_agent.workflows import chat_workflow

from tests.test_trace_decision_coverage import COMPLETE_PLAN, _empty_info_store


#: 框架在"达到最大步数"分支里写死的那句话。LLM 挂掉时用户看到的正是它。
#: 本项目的修复**不匹配这段文本**（`test_the_fix_does_not_depend_on_the_canned_text`
#: 钉住这一点）；它在这里只是为了让用例读起来像真实现场。
STEP_LIMIT_ANSWER = "抱歉，我无法在限定步数内完成这个任务。"

#: 503 的文案只有一处定义（`chat_workflow` 的 `except HelloAgentsException`）。
MODEL_UNAVAILABLE_REPLY = "模型服务当前不可用"

_API_KEY_ENV = "LLM_API_KEY"
_API_KEY_PLACEHOLDER = "sk-model-failure-placeholder-not-a-real-key"


def placeholder_key():
    """补一个占位 key：缺 api_key 时 `HelloAgentsLLM.__init__` 直接抛。全程不发请求。"""
    return mock.patch.dict(os.environ, {_API_KEY_ENV: _API_KEY_PLACEHOLDER})


def build_tracked_llm() -> TrackedLLM:
    with placeholder_key():
        return TrackedLLM(model="deepseek-chat", base_url="https://api.deepseek.com")


def build_real_agent():
    from fithealth_agent.agent import create_fithealth_agent

    with placeholder_key(), redirect_stdout(io.StringIO()):
        return create_fithealth_agent()


class Boom(RuntimeError):
    """只可能来自本文件，不会和实现里的异常混淆。"""


class TrackedLLMTest(unittest.TestCase):
    """记录器本身：记下、原样抛出、不改变成功路径。"""

    def test_a_failed_call_is_remembered_and_reraised(self) -> None:
        llm = build_tracked_llm()
        failure = Boom("connect timeout")
        with mock.patch.object(llm._adapter, "invoke_with_tools", side_effect=failure):
            with self.assertRaises(Boom):
                llm.invoke_with_tools([{"role": "user", "content": "x"}], [])
        self.assertIs(llm.last_failure, failure)
        self.assertIs(swallowed_model_failure(mock.Mock(llm=llm)), failure)

    def test_a_successful_call_leaves_no_failure(self) -> None:
        llm = build_tracked_llm()
        sentinel = object()
        with mock.patch.object(llm._adapter, "invoke_with_tools", return_value=sentinel):
            self.assertIs(llm.invoke_with_tools([{"role": "user", "content": "x"}], []), sentinel)
        self.assertIsNone(llm.last_failure)

    def test_the_async_entry_point_goes_through_the_sync_one(self) -> None:
        """框架的 `ainvoke_with_tools` 只是把同步版丢进 executor（`core/llm.py:269-282`）。

        钉住这条委派关系：它成立，所以覆盖同步入口就够了；框架哪天改成独立实现，
        这条会先红，届时必须补一个 async 覆盖。
        """
        llm = build_tracked_llm()
        failure = Boom("502 Bad Gateway")
        with mock.patch.object(llm._adapter, "invoke_with_tools", side_effect=failure):
            with self.assertRaises(Boom):
                asyncio.run(llm.ainvoke_with_tools([{"role": "user", "content": "x"}], []))
        self.assertIs(llm.last_failure, failure)

    def test_the_factory_builds_a_tracked_llm(self) -> None:
        """少了这一条，有人把 `TrackedLLM` 换回 `HelloAgentsLLM` 时 503 会静默失效。"""
        self.assertIsInstance(build_real_agent().llm, TrackedLLM)

    def test_the_accessor_tolerates_agents_without_an_llm(self) -> None:
        """多数测试把 `deps.create_fithealth_agent` 换成假 agent，取值不能挑食。"""
        for candidate in (object(), mock.Mock(llm=None), mock.Mock(llm=object())):
            with self.subTest(agent=type(candidate).__name__):
                self.assertIsNone(swallowed_model_failure(candidate))


class FrameworkSwallowsLlmErrorsTest(unittest.TestCase):
    """钉住我们绕开的那个框架行为本身。

    这条用例的价值在**将来**：`hello_agents` 哪天改成把异常重抛，它就会红，
    那时 `chat_workflow` 里那段绕行代码可以删掉，而不是留着当谜题。
    """

    def test_a_failing_llm_call_returns_the_step_limit_text_instead_of_raising(self) -> None:
        agent = build_real_agent()
        with mock.patch.object(
            agent.llm._adapter, "invoke_with_tools", side_effect=Boom("connect timeout")
        ):
            with redirect_stdout(io.StringIO()):
                answer = agent.run("我昨晚睡了多久？")

        # 框架吞掉了异常：返回值是那句套话，调用方看不出发生了故障……
        self.assertEqual(answer, STEP_LIMIT_ANSWER)
        # ……除了通过 TrackedLLM 留下的这一条。
        self.assertIsInstance(swallowed_model_failure(agent), Boom)


class _StubbedLLM:
    """框架吞掉异常之后 `agent.llm` 的样子。"""

    model = "deepseek-chat"

    def __init__(self, failure: BaseException | None) -> None:
        self.last_failure = failure


class _SwallowingAgent:
    """模仿框架：LLM 挂了也正常返回，返回值由用例指定。"""

    name = "FitHealthAgent"
    max_steps = 15

    def __init__(self, *, failure: BaseException | None, answer: str) -> None:
        self.llm = _StubbedLLM(failure)
        self.answer = answer
        self.trace_logger = None

    def run(self, _text: str) -> str:
        return self.answer


class ChatSurfacesModelFailureTest(unittest.TestCase):
    """真实 `/chat`：同一条链路上三种结局要分得开。"""

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.profile = {
            "height_cm": 175, "birth_date": "1995-01-01", "gender": "male",
            "goal": "增肌", "equipment": ["哑铃"], "weekly_weight_kg": [70],
        }

    def _post(self, *, factory, validation=([],), message: str = "帮我生成一份上肢训练计划"):
        intent = ChatIntent(
            create_training_plan=True,
            training_plan_subject="上肢",
            training_plan_title="上肢训练",
        )
        resolved = {
            "decision": "override_today", "effective_subject": "上肢训练",
            "blocking_reasons": [], "active_safety_constraints": [],
            "workflow_state": "PLAN_READY", "muscle_recovery": {},
        }
        with tempfile.TemporaryDirectory(prefix="fithealth-model-failure-") as directory:
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
                mock.patch.object(deps, "route_chat_intent", return_value=intent),
                mock.patch.object(deps, "create_fithealth_agent", factory),
                mock.patch.object(chat_workflow, "resolve_plan_context", return_value=resolved),
                mock.patch.object(
                    deps, "validate_plan_goal_alignment",
                    return_value={"passed": True, "matched_subjects": ["上肢训练"],
                                  "missing_subjects": [], "reason": "", "stage": "lite_llm"},
                ),
                mock.patch.object(
                    chat_workflow, "validate_generated_training_plan",
                    side_effect=list(validation),
                ),
            ]
            with ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                response = self.client.post(
                    "/chat", json={"message": message, "history": [], "source": "chat"}
                )
        return response

    @staticmethod
    def _factory(*, failure, answer=STEP_LIMIT_ANSWER):
        def factory(*, avoid_youtube_channels=None, role="agent"):
            return _SwallowingAgent(failure=failure, answer=answer)

        return factory

    def test_a_swallowed_llm_failure_becomes_a_503(self) -> None:
        response = self._post(factory=self._factory(failure=Boom("connect timeout")))
        body = response.json()
        self.assertEqual(response.status_code, 503)
        self.assertIn(MODEL_UNAVAILABLE_REPLY, body["reply"])
        # 那句"任务太复杂"绝不能是用户看到的答案。
        self.assertNotIn(STEP_LIMIT_ANSWER, body["reply"])

    def test_a_healthy_turn_is_untouched(self) -> None:
        """对照组：没有故障时这条检查不能改变任何东西。"""
        response = self._post(factory=self._factory(failure=None, answer=COMPLETE_PLAN))
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["artifact"]["type"], "training_plan")
        self.assertEqual(body["source"], "agent")

    def test_the_fix_does_not_depend_on_the_canned_text(self) -> None:
        """不靠匹配框架那句中文文案——它是框架的实现细节，随版本会变。"""
        response = self._post(
            factory=self._factory(failure=Boom("502"), answer="随便一句别的什么话")
        )
        self.assertEqual(response.status_code, 503)

    def test_a_failure_in_the_correction_loop_is_reported_as_a_call_failure(self) -> None:
        """修正循环的故障要报成"调用失败"，不是"结果不是完整训练计划"。

        两者的处理完全不同：一个查模型服务，一个改提示词或放宽约束。原先框架把异常吞
        掉之后，修正循环拿到的是那句套话，于是被判成"不是计划"——基础设施故障被误报
        成了模型能力问题。
        """
        calls: list[str] = []

        def factory(*, avoid_youtube_channels=None, role="agent"):
            calls.append(role)
            # 主循环正常产出计划，修正循环的 LLM 挂掉。
            if role == "correction_agent":
                return _SwallowingAgent(failure=Boom("connect timeout"), answer=STEP_LIMIT_ANSWER)
            return _SwallowingAgent(failure=None, answer=COMPLETE_PLAN)

        response = self._post(factory=factory, validation=(["组数低于恢复要求"], []))
        body = response.json()

        self.assertEqual(calls, ["agent", "correction_agent"])
        # 降级为明确的校验失败，而不是整次 500，也不是 503——主循环本身是好的。
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["plan_validation"]["violations"], ["组数低于恢复要求", "自动修正服务调用失败"])
        self.assertEqual(body["plan_auto_correction"]["passed"], False)
        self.assertIsNone(body.get("artifact"))


if __name__ == "__main__":
    unittest.main()
