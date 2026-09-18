"""6 个外部模型触点的可观测化（agent-trace 阶段 4）。

## 这一阶段解决什么

在阶段 4 之前，7 个模型触点里只有 ReAct 主循环可见。另外 6 个走裸
`requests.post`，全部 `except → 返回中性回落`：**静默降级、零信号**。
`chat_workflow` 里那段 BUG-02 注释已经承认"关闭联网模型、缺 key、超时、网络抖动"
都会让 `route_chat_intent` 返回全 False 的空意图，但现场分不清是哪一种。

## 三条不变量

1. **一次调用恰好一条事件**。计划原本是"装饰器记一条 + 每个 except 再记一条"，
   那会让任何按 span 的计数翻倍。改成一次调用一个记录器（`model_call`
   上下文管理器持有那唯一一条事件，内部只标注），所以重复计数在结构上不可能发生。
2. **`ok` 三态可分**：`None` 没调 / `False` 调了但回落 / `True` 结果被采用。
3. **回落契约不变**。trace 只让降级可见，绝不改变返回值与异常传播——每个触点都有
   一条"注入抛异常的 requester，断言返回值与未接 trace 时逐字段相同"的用例。

## 静态完整性

`ModelTouchpointInventoryTest` 扫描全包：任何往 `/chat/completions` 发请求的函数
都必须在 `model_call` 块内，且函数集合与冻结清单一致。新增第 7 个触点时它先红，
而不是等到某天有人发现 trace 里少了一截。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

import requests

from fithealth_agent import chat_intent_router, food_analysis, health_safety
from fithealth_agent import information_router, muscle_map, plan_classifier
from fithealth_agent import plan_goal_validator
from fithealth_agent.food_analysis import FoodAnalysisError
from fithealth_agent.observability import MODEL_CALL_SPANS, start_turn

from tests.test_trace_decision_coverage import traced_trace_dir
from tests.test_trace_infrastructure import read_turn, turn_blob


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "fithealth_agent"

#: 往 chat-completions 端点发请求的函数（模块名, 函数名）。冻住它，新增触点必须过一遍。
EXPECTED_TOUCHPOINTS = {
    ("chat_intent_router.py", "route_chat_intent"),
    ("food_analysis.py", "analyze_food_image"),
    ("health_safety.py", "classify_user_health_statement"),
    ("information_router.py", "_level3_llm_decide"),
    ("muscle_map.py", "query_muscles_with_lite_model"),
    # 计划书里没有这一条：它是本文件的静态扫描发现的第 7 个触点。上传的 Markdown
    # 要送轻量模型判断"是不是一份训练计划"，走的是 /upload_plan。
    ("plan_classifier.py", "_level2_llm_check"),
    ("plan_goal_validator.py", "validate_plan_goal_alignment"),
}


def _string_parts(node: ast.AST) -> list[str]:
    return [
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def _call_names(node: ast.AST) -> set[str]:
    names = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        if isinstance(item.func, ast.Name):
            names.add(item.func.id)
        elif isinstance(item.func, ast.Attribute):
            names.add(item.func.attr)
    return names


def scan_touchpoints() -> dict[tuple[str, str], set[str]]:
    """全包扫描：函数 -> 它内部的调用名集合，只保留碰 chat-completions 端点的。

    判据是**函数体里出现 `/chat/completions` 字面量**（f-string 的常量片段也算）。
    这比"含 requests.post"精确：`youtube_search.py` 也发 HTTP，但那不是模型调用。
    """
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any("/chat/completions" in part for part in _string_parts(node)):
                continue
            found[(path.name, node.name)] = _call_names(node)
    return found


class ModelTouchpointInventoryTest(unittest.TestCase):
    """静态完整性：不允许存在"没有接 trace 的模型触点"。"""

    def test_the_touchpoint_inventory_is_frozen(self) -> None:
        self.assertEqual(sorted(scan_touchpoints()), sorted(EXPECTED_TOUCHPOINTS))

    def test_every_touchpoint_is_wrapped_in_a_model_call(self) -> None:
        missing = sorted(
            key for key, calls in scan_touchpoints().items() if "model_call" not in calls
        )
        self.assertEqual(missing, [], "这些函数在发模型请求但没有 model_call 块")

    def test_every_touchpoint_span_is_registered_in_the_schema(self) -> None:
        """span 打错字会让事件带 `_unknown_span`，而统计按 span 分组就会漏掉它。"""
        spans: set[str] = set()
        for path in sorted(PACKAGE_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "model_call"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    spans.add(node.args[0].value)
        self.assertEqual(sorted(spans - MODEL_CALL_SPANS), [])
        # 反向：schema 里注册了但没有任何调用点，说明清单过期了。
        self.assertEqual(sorted(MODEL_CALL_SPANS - spans), [])


def in_turn(fn, *args, **kwargs):
    """在一个回合里跑 `fn`，返回 (返回值, 异常, model_call 事件列表, 落盘全文)。"""
    with traced_trace_dir() as root:
        with start_turn("/test"):
            result: object = None
            raised: BaseException | None = None
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - 就是要观察它
                raised = exc
        events = read_turn(root)
        blob = turn_blob(root)
    calls = [event for event in events if event["kind"] == "model_call"]
    return result, raised, calls, blob


def boom(*_args, **_kwargs):
    raise requests.ConnectTimeout("connect to https://api.example.com/chat/completions timed out")


class ExactlyOneEventPerCallTest(unittest.TestCase):
    """审查意见的头号关切：装饰器 + except 双记录会让计数翻倍。

    这里对**每个**触点的失败路径断言"恰好一条"，并且 `call_id` 唯一。
    """

    def _cases(self):
        return {
            "route_chat_intent": lambda: in_turn(
                chat_intent_router.route_chat_intent, "帮我练腿"
            ),
            "classify_user_health_statement": lambda: in_turn(
                health_safety.classify_user_health_statement, "膝盖疼", requester=boom
            ),
            "validate_plan_goal_alignment": lambda: in_turn(
                plan_goal_validator.validate_plan_goal_alignment,
                "计划正文", fallback_subject="腿部", requester=boom,
            ),
            "query_muscles_with_lite_model": lambda: in_turn(
                muscle_map.query_muscles_with_lite_model,
                "弯举", api_key="k", requester=boom,
            ),
            "route_information": lambda: in_turn(
                information_router._level3_llm_decide, "[user] 我膝盖疼"
            ),
            "validate_training_plan": lambda: in_turn(
                plan_classifier._level2_llm_check, "# 训练计划\n热身\n"
            ),
            "analyze_food_image": lambda: in_turn(
                food_analysis.analyze_food_image, b"\xff\xd8\xff", "image/jpeg"
            ),
        }

    def test_each_touchpoint_emits_exactly_one_event(self) -> None:
        with mock.patch.object(chat_intent_router, "ROUTER_API_KEY", "k"), \
             mock.patch.object(chat_intent_router.requests, "post", boom), \
             mock.patch.object(information_router, "LLM_LITE_API_KEY", "k"), \
             mock.patch.object(plan_classifier, "LLM_LITE_API_KEY", "k"), \
             mock.patch.object(requests, "post", boom), \
             mock.patch.dict(
                 "os.environ",
                 {"LLM_LITE_API_KEY": "k", "VISION_API_KEY": "k",
                  "VISION_MODEL_ID": "m", "VISION_BASE_URL": "https://api.example.com"},
             ):
            for span, run in self._cases().items():
                with self.subTest(span=span):
                    _result, _raised, calls, _blob = run()
                    self.assertEqual(len(calls), 1, [item["payload"] for item in calls])
                    self.assertEqual(calls[0]["span"], span)
                    payload = calls[0]["payload"]
                    self.assertEqual(len(payload["call_id"]), 8)
                    self.assertEqual([payload["ok"], payload["attempt"]], [False, 0 + 1])
                    self.assertIn("dur_ms", calls[0])

    def test_every_registered_span_is_exercised_here(self) -> None:
        """漏测一个 span 时先在这里红，而不是等到线上少一截。"""
        self.assertEqual(sorted(self._cases()), sorted(MODEL_CALL_SPANS))


class TriStateTest(unittest.TestCase):
    """BUG-02 的正解：没调 / 调了回落 / 调通，三种情况必须分得开。"""

    def test_disabled_external_models_is_skipped_not_failed(self) -> None:
        _result, _raised, calls, _blob = in_turn(
            chat_intent_router.route_chat_intent, "帮我练腿", allow_external_models=False
        )
        payload = calls[0]["payload"]
        self.assertIsNone(payload["ok"])
        self.assertEqual(payload["skipped_reason"], "external_models_disabled")
        self.assertNotIn("fallback_reason", payload)

    def test_missing_api_key_is_skipped_with_its_own_reason(self) -> None:
        with mock.patch.object(chat_intent_router, "ROUTER_API_KEY", ""):
            _result, _raised, calls, _blob = in_turn(
                chat_intent_router.route_chat_intent, "帮我练腿"
            )
        self.assertEqual(
            [calls[0]["payload"]["ok"], calls[0]["payload"]["skipped_reason"]],
            [None, "no_api_key"],
        )

    def test_skip_reason_priority_is_config_then_key(self) -> None:
        """两个条件同时成立时只记优先级更高的那个，统计口径才不会重复。"""
        with mock.patch.object(chat_intent_router, "ROUTER_API_KEY", ""):
            _result, _raised, calls, _blob = in_turn(
                chat_intent_router.route_chat_intent, "", allow_external_models=False
            )
        self.assertEqual(calls[0]["payload"]["skipped_reason"], "external_models_disabled")

    def test_an_empty_intent_from_a_healthy_call_is_a_success(self) -> None:
        """模型说"这句话没有可执行意图"是一次**成功的判断**，不是故障。"""
        response = mock.Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"tool_calls": []}}],
                                      "usage": {"total_tokens": 42, "prompt_tokens": 40,
                                                "completion_tokens": 2}}
        response.raise_for_status.return_value = None
        with mock.patch.object(chat_intent_router, "ROUTER_API_KEY", "k"), \
             mock.patch.object(chat_intent_router.requests, "post", return_value=response):
            result, _raised, calls, _blob = in_turn(
                chat_intent_router.route_chat_intent, "今天天气不错"
            )
        payload = calls[0]["payload"]
        self.assertEqual([payload["ok"], payload["result"]], [True, "none"])
        self.assertEqual(
            [payload["total_tokens"], payload["usage_source"], payload["http_status"]],
            [42, "provider", 200],
        )
        self.assertFalse(result.create_training_plan)

    def test_structural_parse_failure_is_not_a_call_failure(self) -> None:
        """HTTP 200 但内容不合约定：与网络故障分开统计（审查意见）。"""
        response = mock.Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "不是 JSON"}}]}
        response.raise_for_status.return_value = None
        with mock.patch.dict("os.environ", {"LLM_LITE_API_KEY": "k"}):
            result, _raised, calls, _blob = in_turn(
                plan_goal_validator.validate_plan_goal_alignment,
                "计划正文", fallback_subject="腿部",
                requester=mock.Mock(return_value=response),
            )
        payload = calls[0]["payload"]
        self.assertEqual(
            [payload["ok"], payload["fallback_reason"], payload["http_status"]],
            [False, "unparseable_json", 200],
        )
        self.assertNotIn("error_type", payload)
        # 回落到本地规则，`stage` 不是 lite_llm
        self.assertNotEqual(result["stage"], "lite_llm")

    def test_http_error_and_timeout_are_distinguishable(self) -> None:
        # 键是**期望记下的类名**：记的是 `type(exc).__name__`，所以用 ValueError
        # 抛出时记的就是 ValueError（json 的 JSONDecodeError 是它的子类）。
        cases = {
            "HTTPError": requests.HTTPError("502 Server Error"),
            "ConnectTimeout": requests.ConnectTimeout("timed out"),
            "ValueError": ValueError("Expecting value"),
        }
        for expected, exc in cases.items():
            with self.subTest(error=expected):
                _result, _raised, calls, _blob = in_turn(
                    health_safety.classify_user_health_statement,
                    "膝盖疼", api_key="k",
                    requester=mock.Mock(side_effect=exc),
                )
                payload = calls[0]["payload"]
                self.assertEqual(
                    [payload["ok"], payload["error_type"], payload["fallback_reason"]],
                    [False, expected, expected],
                )


class FallbackContractTest(unittest.TestCase):
    """回落契约不能被 trace 改动：返回值与异常传播都必须逐字段相同。"""

    def test_return_values_on_failure_are_unchanged(self) -> None:
        expected = {
            "route_chat_intent": (
                lambda: chat_intent_router.route_chat_intent("帮我练腿"),
                lambda value: value == chat_intent_router.ChatIntent(),
            ),
            "classify_user_health_statement": (
                lambda: health_safety.classify_user_health_statement(
                    "膝盖疼", api_key="k", requester=boom
                ),
                lambda value: value is None,
            ),
            "query_muscles_with_lite_model": (
                lambda: muscle_map.query_muscles_with_lite_model(
                    "弯举", api_key="k", requester=boom
                ),
                lambda value: value == [],
            ),
            "validate_plan_goal_alignment": (
                lambda: plan_goal_validator.validate_plan_goal_alignment(
                    "计划正文", fallback_subject="腿部", requester=boom
                ),
                lambda value: value["stage"] != "lite_llm",
            ),
            "route_information": (
                lambda: information_router._level3_llm_decide("[user] 我膝盖疼"),
                lambda value: value["save"] is False,
            ),
            "validate_training_plan": (
                lambda: plan_classifier._level2_llm_check("# 训练计划\n热身\n"),
                lambda value: value[0] is False,
            ),
        }
        with mock.patch.object(chat_intent_router, "ROUTER_API_KEY", "k"), \
             mock.patch.object(chat_intent_router.requests, "post", boom), \
             mock.patch.object(information_router, "LLM_LITE_API_KEY", "k"), \
             mock.patch.object(plan_classifier, "LLM_LITE_API_KEY", "k"), \
             mock.patch.object(requests, "post", boom), \
             mock.patch.dict("os.environ", {"LLM_LITE_API_KEY": "k"}):
            for span, (run, holds) in expected.items():
                with self.subTest(span=span):
                    # 不在回合内跑：这条断言要证明的是"有没有 trace 都一样"。
                    without_trace = run()
                    with_trace, _raised, calls, _blob = in_turn(run)
                    self.assertTrue(holds(without_trace), without_trace)
                    self.assertEqual(with_trace, without_trace)
                    self.assertEqual(len(calls), 1)

    def test_food_analysis_still_raises_the_same_error(self) -> None:
        """这个触点是**抛异常**而不是回落，异常类型不能被 trace 改掉。"""
        with mock.patch.dict(
            "os.environ",
            {"VISION_API_KEY": "k", "VISION_MODEL_ID": "m",
             "VISION_BASE_URL": "https://api.example.com"},
        ), mock.patch.object(requests, "post", boom):
            _result, raised, calls, _blob = in_turn(
                food_analysis.analyze_food_image, b"\xff\xd8\xff", "image/jpeg"
            )
        self.assertIsInstance(raised, FoodAnalysisError)
        payload = calls[0]["payload"]
        self.assertEqual([payload["ok"], payload["error_type"]], [False, "ConnectTimeout"])
        # 图片只记字节数，不记内容。
        self.assertEqual(payload["request_bytes"], 3)

    def test_missing_vision_config_is_skipped_even_though_it_raises(self) -> None:
        """配置缺失是"从未调用"。随后抛出的业务异常不该把 ok 改写成 False。"""
        with mock.patch.dict("os.environ", {}, clear=False) as _env:
            with mock.patch.object(food_analysis.os, "getenv", return_value=None):
                _result, raised, calls, _blob = in_turn(
                    food_analysis.analyze_food_image, b"\xff\xd8\xff", "image/jpeg"
                )
        self.assertIsInstance(raised, FoodAnalysisError)
        payload = calls[0]["payload"]
        self.assertEqual([payload["ok"], payload["skipped_reason"]], [None, "no_api_key"])


class NoLeakageTest(unittest.TestCase):
    """密钥、URL 参数、异常消息、提示词都不该出现在落盘内容里。"""

    def test_secrets_and_messages_never_reach_the_trace_file(self) -> None:
        secret = "sk-super-secret-key-do-not-log"
        with mock.patch.object(chat_intent_router, "ROUTER_API_KEY", secret), \
             mock.patch.object(chat_intent_router, "ROUTER_BASE_URL", "https://api.example.com/v1"), \
             mock.patch.object(chat_intent_router.requests, "post", boom):
            _result, _raised, calls, blob = in_turn(
                chat_intent_router.route_chat_intent, "我的左膝十字韧带术后三个月"
            )
        for forbidden in (
            secret,
            "十字韧带",                      # 用户原话（meta 级别只有摘要）
            "timed out",                     # 异常消息
            "/chat/completions",             # 完整 URL 路径
            "Bearer",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, blob)
        # 主机名是有用的（分得清打到了哪个供应商），而且不含路径与参数。
        self.assertEqual(calls[0]["payload"]["endpoint_host"], "api.example.com")


if __name__ == "__main__":
    unittest.main()




