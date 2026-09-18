"""HTTP 边界的 turn 生命周期（agent-trace 阶段 2）。

阶段 2 只做一件事：让 `/chat`、`/analyze_food`、`/logout` 三条路由各自开出一个
回合。这里验证四组不变量：

1. **接入清单**与实现一致——`observability/http.py` 里列了的路由真的开回合，
   没列的一个都不开（含同一个模块里的 `/upload_*`，那是最容易误伤的对照组）；
2. `turn_end` **恰好一条**，且成功 / 业务异常 / 取消三种结局分得开；
3. **响应序列化失败也在回合内**——`JSONResponse` 在 `__init__` 里就渲染 body，
   所以它必须构造在 `with` 之内，挪出去就看不见了；
4. 刻意排除的路径（请求体解析失败）确实不开回合。

用一个只挂这三个 router 的最小 app，不 import `main`：`main` 会在模块级把 store
绑到当时的数据目录上（`test_reset_transactionality.py` 开头那段注释解释了这个坑），
而这里要测的只是路由适配器本身。真实 app 的接线由 `test_route_snapshot.py` 守。
"""

from __future__ import annotations

import ast
import asyncio
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fithealth_agent.observability import TRACED_ROUTES
from fithealth_agent.observability import config as trace_config
from fithealth_agent.observability import sink as trace_sink
from fithealth_agent.routes import chat as chat_route
from fithealth_agent.routes import logout as logout_route
from fithealth_agent.routes import uploads as uploads_route
from fithealth_agent.workflows.chat_workflow import ChatResult
from fithealth_agent.workflows.upload_workflow import WorkflowResult

from tests import module_map
from tests.source_tools import module_tree
from tests.test_trace_infrastructure import event_of, read_turn


#: 同一个 uploads 模块里的对照组：跑在同一份代码旁边，但不该开回合。
#: `/upload_plan` 曾经在这里——阶段 4 的静态扫描发现它也在调模型（上传计划的语义
#: 分类），已移进白名单。这正是"排除表要有测试盯着"的价值。
UNTRACED_UPLOAD_ROUTES = ("/upload_fit", "/upload_health")


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_route.router)
    app.include_router(logout_route.router)
    app.include_router(uploads_route.food_router)
    app.include_router(uploads_route.router)
    return app


@contextmanager
def traced_client():
    """开着 trace 的 TestClient，trace 目录指向一个临时目录。

    只换 `FITHEALTH_TRACE*`，**不动** `FITHEALTH_DATA_DIR`：store 的位置与本文件
    无关，换它反而会碰上"谁先 import 谁定下数据目录"那个坑。
    """
    previous = {
        name: os.environ.get(name)
        for name in ("FITHEALTH_TRACE", "FITHEALTH_TRACE_DIR", "FITHEALTH_TRACE_DETAIL")
    }
    with tempfile.TemporaryDirectory(prefix="fithealth-trace-http-") as temp:
        root = Path(temp)
        os.environ["FITHEALTH_TRACE"] = "on"
        os.environ["FITHEALTH_TRACE_DIR"] = str(root / "traces")
        os.environ.pop("FITHEALTH_TRACE_DETAIL", None)
        trace_sink.reset_writable_cache()
        trace_config._warned.clear()
        try:
            with TestClient(build_app()) as client:
                yield client, root
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            trace_sink.reset_writable_cache()
            trace_config._warned.clear()


def turn_files(root: Path) -> list[Path]:
    return sorted((root / "traces").rglob("turn-*.jsonl"))


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def turn_scoped_calls(path: Path, function_name: str) -> set[str]:
    """`function_name` 里处在 `with start_turn(...)` 块内的调用名集合。

    纯结构提取：只看 AST 节点类型和名字，不把节点 unparse 回文本做字符串匹配
    （ARCH-09 允许前者，禁止后者）。
    """
    tree = module_tree(path)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    found: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.With):
            continue
        if not any(_call_name(item.context_expr) == "start_turn" for item in node.items):
            continue
        for inner in ast.walk(node):
            name = _call_name(inner)
            if name:
                found.add(name)
    return found


async def _ok_chat(payload: dict) -> ChatResult:
    return ChatResult(
        body={
            "reply": "已打开本地训练记录。",
            "source": "local_training_records",
            "artifact": {"type": "training_records", "default_date": "2026-09-04"},
        }
    )


async def _ok_food(file: object, context: str = "") -> WorkflowResult:
    return WorkflowResult(body={"confidence": "high"}, status_code=200)


async def _ok_plan(file: object, confirm_large: bool = False) -> WorkflowResult:
    return WorkflowResult(body={"is_plan": True, "reason": "ok"}, status_code=200)


def _ok_logout(payload: dict | None = None) -> WorkflowResult:
    return WorkflowResult(
        body={"status": "saved", "saved": True, "summary": "s"}, status_code=200
    )


CHAT_BODY = {"message": "查看训练记录"}
FOOD_FILES = {"file": ("plate.jpg", b"\xff\xd8\xff", "image/jpeg")}
PLAN_FILES = {"file": ("plan.md", "# 训练计划\n热身 5 分钟\n".encode("utf-8"), "text/markdown")}


@contextmanager
def stubbed_workflows(*, chat=None, food=None, logout=None, plan=None):
    """把三个 workflow 换成不碰模型的桩。

    打在各 route 模块自己的属性上：它们是 `from ... import x as y` 绑进来的本地
    名字，这里要替换的正是那个绑定。
    """
    with mock.patch.object(chat_route, "run_chat_workflow", chat or _ok_chat), \
         mock.patch.object(uploads_route, "run_analyze_food", food or _ok_food), \
         mock.patch.object(uploads_route, "run_upload_plan", plan or _ok_plan), \
         mock.patch.object(logout_route, "run_logout", logout or _ok_logout):
        yield


class TracedRouteCoverageTest(unittest.TestCase):
    """接入清单与实现必须一致。"""

    #: 每条白名单路由怎么发请求。少一条就在下面第一条断言处红。
    SENDERS = {
        "/chat": lambda client: client.post("/chat", json=CHAT_BODY),
        "/analyze_food": lambda client: client.post(
            "/analyze_food", files=FOOD_FILES, data={"context": "午餐"}
        ),
        "/logout": lambda client: client.post("/logout", json={"messages": []}),
        "/upload_plan": lambda client: client.post("/upload_plan", files=PLAN_FILES),
    }

    def test_every_whitelisted_route_has_a_case_here(self) -> None:
        """白名单加了新路由但没在这里补一条发请求的方式，先在这里红。"""
        self.assertEqual(sorted(self.SENDERS), sorted(TRACED_ROUTES))

    def test_every_whitelisted_route_opens_exactly_one_turn(self) -> None:
        for route, send in self.SENDERS.items():
            with self.subTest(route=route):
                # 断言必须在 with 内取数：traced_client 退出时会删掉临时目录。
                with traced_client() as (client, root), stubbed_workflows():
                    response = send(client)
                    files = [path.name for path in turn_files(root)]
                    events = read_turn(root)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(files), 1, files)
                self.assertEqual(
                    [event_of(events, kind)["span"] for kind in ("turn_start", "turn_end")],
                    [route, route],
                )
                self.assertEqual(
                    [event["kind"] for event in events].count("turn_end"), 1
                )

    def test_untraced_upload_routes_open_no_turn(self) -> None:
        """同一个模块里的邻居不该被顺手带上：它们不碰外部模型。"""
        for route in UNTRACED_UPLOAD_ROUTES:
            with self.subTest(route=route):
                with traced_client() as (client, root):
                    client.post(route, files=FOOD_FILES)
                    files = turn_files(root)
                self.assertEqual(files, [])


class TurnEndFidelityTest(unittest.TestCase):
    """成功 / 业务异常 / 取消 / 响应序列化失败四种结局都要分得开。"""

    def _run(self, send, **stubs):
        with traced_client() as (client, root), stubbed_workflows(**stubs):
            raised: BaseException | None = None
            try:
                send(client)
            except BaseException as exc:  # noqa: BLE001 - 就是要观察它
                raised = exc
            events = read_turn(root)
        return events, raised

    def test_success_records_source_status_code_and_artifact(self) -> None:
        events, raised = self._run(lambda client: client.post("/chat", json=CHAT_BODY))
        self.assertIsNone(raised)
        payload = event_of(events, "turn_end")["payload"]
        self.assertEqual(
            [payload["status"], payload["complete"], payload["status_code"]],
            ["ok", True, 200],
        )
        self.assertEqual(
            [payload["source"], payload["artifact_type"]],
            ["local_training_records", "training_records"],
        )
        self.assertEqual(payload["trace_errors"], 0)

    def test_business_exception_is_recorded_as_error(self) -> None:
        async def boom(payload: dict) -> ChatResult:
            raise RuntimeError("模型层炸了")

        events, raised = self._run(
            lambda client: client.post("/chat", json=CHAT_BODY), chat=boom
        )
        self.assertIsInstance(raised, RuntimeError)
        payload = event_of(events, "turn_end")["payload"]
        self.assertEqual([payload["status"], payload["error_type"]], ["error", "RuntimeError"])

    def test_cancellation_is_recorded_as_aborted(self) -> None:
        """用户关页面 / 上游超时不该看起来像一次服务错误。"""

        async def cancelled(payload: dict) -> ChatResult:
            raise asyncio.CancelledError

        events, raised = self._run(
            lambda client: client.post("/chat", json=CHAT_BODY), chat=cancelled
        )
        self.assertIsInstance(raised, BaseException)
        payload = event_of(events, "turn_end")["payload"]
        self.assertEqual(
            [payload["status"], payload["error_type"]], ["aborted", "CancelledError"]
        )

    def test_response_serialisation_failure_is_inside_the_turn(self) -> None:
        """`JSONResponse.__init__` 就把 body 渲染成字节，所以它必须构造在 with 内。"""

        async def unserialisable(payload: dict) -> ChatResult:
            return ChatResult(body={"reply": object()})

        events, raised = self._run(
            lambda client: client.post("/chat", json=CHAT_BODY), chat=unserialisable
        )
        self.assertIsInstance(raised, TypeError)
        payload = event_of(events, "turn_end")["payload"]
        self.assertEqual([payload["status"], payload["error_type"]], ["error", "TypeError"])

    def test_turn_end_is_written_exactly_once_across_all_outcomes(self) -> None:
        async def boom(payload: dict) -> ChatResult:
            raise RuntimeError("x")

        for label, stub in (("ok", None), ("error", boom)):
            with self.subTest(outcome=label):
                events, _ = self._run(
                    lambda client: client.post("/chat", json=CHAT_BODY), chat=stub
                )
                kinds = [event["kind"] for event in events]
                self.assertEqual(kinds.count("turn_end"), 1)
                self.assertEqual(kinds.count("turn_start"), 1)
                # turn_end 必须是最后一条：之后再记的事件永远读不到。
                self.assertEqual(kinds[-1], "turn_end")

    def test_logout_business_status_goes_to_source_not_status(self) -> None:
        """`status` 是生命周期三态，被业务值覆盖就分不出"是不是崩了"。"""
        events, _ = self._run(
            lambda client: client.post("/logout", json={"messages": [{"role": "user", "text": "x"}]})
        )
        payload = event_of(events, "turn_end")["payload"]
        self.assertEqual([payload["status"], payload["source"]], ["ok", "saved"])
        self.assertEqual(payload["trace_errors"], 0)


class ExcludedPathsTest(unittest.TestCase):
    """刻意排除的路径确实不开回合（见 observability/http.py 的排除表）。"""

    def _no_turn(self, send) -> int:
        with traced_client() as (client, root), stubbed_workflows():
            response = send(client)
            files = turn_files(root)
        self.assertEqual(files, [], "这条路径不该产生回合文件")
        return response.status_code

    def test_oversized_chat_request_creates_no_turn(self) -> None:
        """413 发生在读 body 期间。在它之前开 turn，客户端就能用超大请求随意造文件。"""
        oversized = {"message": "长" * 200_000}
        self.assertEqual(self._no_turn(lambda client: client.post("/chat", json=oversized)), 413)

    def test_unsupported_media_type_creates_no_turn(self) -> None:
        self.assertEqual(
            self._no_turn(
                lambda client: client.post(
                    "/chat", content=b"{}", headers={"content-type": "text/plain"}
                )
            ),
            415,
        )

    def test_empty_and_malformed_bodies_create_no_turn(self) -> None:
        for label, kwargs in (
            ("empty", {"content": b"", "headers": {"content-type": "application/json"}}),
            ("malformed", {"content": b"{", "headers": {"content-type": "application/json"}}),
            ("missing_message", {"json": {"source": "chat"}}),
        ):
            with self.subTest(body=label):
                status = self._no_turn(lambda client: client.post("/chat", **kwargs))
                self.assertEqual(status, 400)


class RouteStructureTest(unittest.TestCase):
    """结构约束：回合的边界必须真的包住工作流和响应构造。

    纯 AST 提取，只比对节点名字集合（ARCH-09 禁止把节点 unparse 回文本做字符串
    匹配，但允许结构断言）。
    """

    def test_chat_runs_the_workflow_and_builds_the_response_inside_the_turn(self) -> None:
        inside = turn_scoped_calls(module_map.CHAT_ROUTE, "chat")
        watched = {"run_chat_workflow", "set_turn_result", "JSONResponse", "context_error_response"}
        self.assertEqual(
            sorted(inside & watched),
            # `context_error_response` 刻意在回合外——请求体解析失败不开回合。
            ["JSONResponse", "run_chat_workflow", "set_turn_result"],
        )

    def test_analyze_food_and_logout_have_the_same_shape(self) -> None:
        cases = {
            "analyze_food": (
                module_map.UPLOADS_ROUTE,
                {"run_analyze_food", "set_turn_result", "JSONResponse"},
            ),
            "logout": (
                module_map.LOGOUT_ROUTE,
                {"run_logout", "set_turn_result", "JSONResponse"},
            ),
        }
        for function_name, (path, expected) in cases.items():
            with self.subTest(endpoint=function_name):
                inside = turn_scoped_calls(path, function_name)
                self.assertEqual(sorted(inside & expected), sorted(expected))

    def test_untraced_upload_endpoints_have_no_turn_block(self) -> None:
        for function_name in ("upload_fit", "upload_health"):
            with self.subTest(endpoint=function_name):
                self.assertEqual(
                    sorted(turn_scoped_calls(module_map.UPLOADS_ROUTE, function_name)), []
                )


if __name__ == "__main__":
    unittest.main()





