"""桩可达性验证（main.py 拆分：阶段 1 的专项验收）。

拆分计划里最危险的一类回归是**假绿**：`from x import f` 在调用方绑了一个副本，
于是 `patch.object(deps, "f", fake)` 执行成功、测试全绿，而被测代码走的还是原实现。
这种失效不报错、不留痕，只是让一整组用例悄悄失去意义。

本文件是那条风险的解药，三层验证：

1. **静态穷举**（`DepsBindingTest`）——覆盖全部 22 个符号：
   - 实现模块里不得再持有同名绑定，否则 `patch.object(main, "route_chat_intent", …)`
     会成功却无效；现在这么写会抛 AttributeError，响亮地失败。
   - 每个符号必须至少有一处 `deps.X` 属性访问，否则说明它已经没人用了（或者被谁
     改回了直接 import），deps 的打桩点也就名存实亡。
2. **动态·函数桩**（`RaisingStubReachabilityTest`）——把桩换成"一调用就抛哨兵异常"，
   驱动真实端点，确认哨兵真的炸出来。桩没接上的话请求会正常返回，测试变红。
3. **动态·实例替换**（`StoreReplacementTest`）——替换 `deps.info_store` /
   `deps.soreness_store` 后，走 HTTP 确认读写都落到**新**实例上，旧实例一个字节没动。

第 2 层没有逐个覆盖 FIT / 健康导入那几条链路的桩，因为
`test_checkin_fit_and_editor_fixes.py`（`inspect_fit_source`、`parse_fit_file`）、
`test_zip_skip_reporting.py`（`extract_activity_fits`）、
`test_seventh_batch.py` 与 `test_remaining_partials.py`（`analyze_food_image`）、
`test_immediate_memory_capture.py`（`route_information`、`parse_soreness_reply`）、
`test_f3_memory_features.py`（`build_current_week_reply`）里的现有用例都**断言了桩的
返回值如何改变响应**——桩接不上那些用例就会红。第 1 层保证它们打的是同一个属性。
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import main
from fithealth_agent.info_store import InfoStore
from fithealth_agent.runtime import deps
from fithealth_agent.soreness_store import SorenessStore
from tests.module_map import DEPS_CONSUMERS, module_import_name
from tests.source_tools import module_tree


#: `deps` 持有的、**可能被整体替换**的符号。改这张表时同步改 deps.py 的分组注释。
DEPS_STORES = (
    "profile_store",
    "daily_record_store",
    "info_store",
    "external_model_settings_store",
    "soreness_store",
    "plan_store",
    "plan_draft_cache",
    "health_store",
    "hr_stream_store",
    "health_import_service",
    "backup_service",
    # agent-trace 阶段 6：`/data/reset` 的第 12 步与"恢复备份后清空"都走它。
    "trace_store",
)
DEPS_FUNCTIONS = (
    "route_chat_intent",
    "route_information",
    "classify_user_health_statement",
    "create_fithealth_agent",
    "build_current_week_reply",
    "analyze_food_image",
    "parse_soreness_reply",
    "parse_fit_file",
    "inspect_fit_source",
    "extract_activity_fits",
)
DEPS_SYMBOLS = DEPS_STORES + DEPS_FUNCTIONS


def _deps_attribute_uses(path: Path) -> set[str]:
    """模块里所有 `deps.<name>` 形式的属性访问。"""
    return {
        node.attr
        for node in ast.walk(module_tree(path))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "deps"
    }


class DepsBindingTest(unittest.TestCase):
    """静态穷举：22 个符号，一个都不能漏。"""

    def test_deps_owns_every_declared_symbol(self) -> None:
        for name in DEPS_SYMBOLS:
            with self.subTest(symbol=name):
                self.assertTrue(
                    hasattr(deps, name),
                    f"deps 里没有 {name!r}；本文件的清单与 deps.py 漂移了",
                )

    def test_consumers_keep_no_shadow_binding(self) -> None:
        """消费者模块不得再持有同名绑定——那是一条静默的打桩暗道。

        留着 `from fithealth_agent.chat_intent_router import route_chat_intent` 的话，
        `patch.object(main, "route_chat_intent", fake)` 会成功执行却毫无效果：
        被测代码读的是 `deps.route_chat_intent`。
        """
        import importlib

        for path in DEPS_CONSUMERS:
            module = importlib.import_module(module_import_name(path))
            for name in DEPS_SYMBOLS:
                with self.subTest(module=path.name, symbol=name):
                    self.assertFalse(
                        hasattr(module, name),
                        f"{path.name} 又持有了 {name!r} 的本地绑定。"
                        f"改成 `deps.{name}`，否则打在这个模块上的桩会静默失效。",
                    )

    def test_every_symbol_is_actually_reached_through_deps(self) -> None:
        """每个符号至少有一处 `deps.X`，否则 deps 的打桩点名存实亡。"""
        reached: set[str] = set()
        for path in DEPS_CONSUMERS:
            reached |= _deps_attribute_uses(path)
        unused = sorted(set(DEPS_SYMBOLS) - reached)
        self.assertEqual(
            unused,
            [],
            "这些符号没有任何一处 `deps.X` 属性访问，打在 deps 上的桩不会生效：\n"
            + "\n".join(unused),
        )


class RaisingStubReachabilityTest(unittest.TestCase):
    """动态：桩一被调用就抛哨兵异常，且必须真的被调用到。

    桩里同时**记录调用**并**抛异常**，两件事各有分工：

    - 记录：绑定成了副本的话桩根本不会被调用，`calls` 为空 → 红。这是要抓的假绿。
    - 抛异常：确保用例不会因为"真实实现恰好也返回了个能让断言通过的东西"而蒙混过关。

    只断言"被调用过"而不断言异常怎么冒出来：几个端点各有各的兜底（`/analyze_food`
    回 502、`/logout` 回 status=error），把断言绑在具体状态码上只会让这个验证器
    随实现漂移。
    """

    class Sentinel(RuntimeError):
        """只可能来自本文件的桩，不会和实现里的异常混淆。"""

    @classmethod
    def setUpClass(cls) -> None:
        # raise_server_exceptions=False：哨兵穿透到测试进程也不影响判断，
        # 这里只关心桩有没有被调用到。
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    def assert_stub_is_reached(self, name: str, drive) -> None:
        calls: list[tuple] = []

        def stub(*args, **kwargs):
            calls.append((args, kwargs))
            raise self.Sentinel(name)

        with mock.patch.object(deps, name, stub):
            try:
                drive()
            except self.Sentinel:
                pass
        self.assertTrue(
            calls,
            f"deps.{name} 的桩一次都没有被调用到——说明消费者读的不是 deps 的这个属性，"
            f"打在 deps 上的桩会静默失效。",
        )

    def _external_models_on(self):
        return mock.patch.object(
            deps.external_model_settings_store,
            "get",
            return_value={"external_models_enabled": True},
        )

    def _chat(self):
        self.client.post(
            "/chat",
            json={"message": "帮我安排今天的胸部训练", "history": [], "source": "chat"},
        )

    def test_chat_path_stubs_are_reached(self) -> None:
        with self._external_models_on():
            for name in ("classify_user_health_statement", "route_chat_intent"):
                with self.subTest(symbol=name):
                    self.assert_stub_is_reached(name, self._chat)

    def test_agent_factory_stub_is_reached(self) -> None:
        from fithealth_agent.chat_intent_router import ChatIntent

        # 三件事都要绕开，否则 /chat 走不到真正调 Agent 的那一步：
        # 档案不完整会短路成 onboarding 引导；健康风险闸和意图路由各有短路分支。
        # 这里 patch 的是共享实例上的**方法**，不涉及绑定方式，不影响本文件要验证的东西。
        with self._external_models_on(), mock.patch.object(
            deps.profile_store, "is_complete", return_value=True
        ), mock.patch.object(
            deps, "classify_user_health_statement", return_value=False
        ), mock.patch.object(deps, "route_chat_intent", return_value=ChatIntent()):
            self.assert_stub_is_reached("create_fithealth_agent", self._chat)

    def test_logout_stub_is_reached(self) -> None:
        def drive():
            self.client.post(
                "/logout",
                json={"messages": [{"role": "user", "text": "以后不要安排跳绳，我膝盖不舒服"}]},
            )

        with self._external_models_on():
            self.assert_stub_is_reached("route_information", drive)

    def test_food_analysis_stub_is_reached(self) -> None:
        def drive():
            self.client.post(
                "/analyze_food",
                files={"file": ("plate.jpg", b"\xff\xd8\xff fake jpeg", "image/jpeg")},
                data={"context": ""},
            )

        with self._external_models_on():
            self.assert_stub_is_reached("analyze_food_image", drive)


class StoreReplacementTest(unittest.TestCase):
    """动态：替换 `deps.<store>` 之后，读写必须全部落到新实例上。

    计划里点名了这两个 store，因为它们是唯二被**整体替换**（而不是只 patch 方法）
    的实例——只 patch 方法的那些不受绑定方式影响，整体替换才会暴露副本问题。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_replacing_the_memory_store_redirects_reads_and_writes(self) -> None:
        replacement = InfoStore(self.root / "info_store.json")
        original = deps.info_store
        original_before = original.get_all()
        with mock.patch.object(deps, "info_store", replacement):
            replacement.add_entry(
                "替换验证用记忆",
                {},
                datetime.now(timezone.utc) + timedelta(days=1),
                memory_type="preference",
                importance=3,
            )
            # 读：/data/overview 直接吐 info_store.get_all()
            overview = self.client.get("/data/overview")
            self.assertEqual(overview.status_code, 200, overview.text)
            summaries = [item["summary"] for item in overview.json()["memories"]]
            # 写：清空必须清掉新实例，而不是旧的
            cleared = self.client.delete("/data/memories")
            self.assertEqual(cleared.status_code, 200, cleared.text)

        self.assertEqual(summaries, ["替换验证用记忆"], "读没有落到替换后的实例上")
        self.assertEqual(cleared.json()["deleted"], 1)
        self.assertEqual(replacement.get_all(), [], "写没有落到替换后的实例上")
        self.assertEqual(original.get_all(), original_before, "旧实例被误伤了")

    def test_replacing_the_soreness_store_redirects_reads_and_writes(self) -> None:
        replacement = SorenessStore(self.root / "muscle_soreness.json")
        original = deps.soreness_store
        original_before = original.list_reports()
        with mock.patch.object(deps, "soreness_store", replacement):
            created = self.client.post(
                "/data/soreness", json={"region": "胸部", "level": "sore", "evidence": "替换验证"}
            )
            self.assertEqual(created.status_code, 200, created.text)
            overview = self.client.get("/data/overview")
            self.assertEqual(overview.status_code, 200, overview.text)
            reported = [item["region"] for item in overview.json()["soreness_reports"]]

        self.assertEqual(len(replacement.list_reports()), 1, "写没有落到替换后的实例上")
        self.assertEqual(reported, ["胸部"], "读没有落到替换后的实例上")
        self.assertEqual(original.list_reports(), original_before, "旧实例被误伤了")


if __name__ == "__main__":
    unittest.main()
