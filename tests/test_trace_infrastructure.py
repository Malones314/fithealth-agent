"""阶段 1 的 trace 基础设施测试（agent-trace）。

阶段 1 没有任何调用点，所以这里全部是对 `fithealth_agent.observability` 本身的行为
断言。四条硬不变量：

1. **默认关闭且零副作用**：不建目录、不写文件、不设 ContextVar；
2. **绝不影响主流程**：记录失败只降级为计数与限流告警，业务返回值不变；
3. **`turn_end` 恰好一条**：正常、异常、取消三条路径都写出且状态可区分；
4. **`meta` 级别不落原文**：字段级反向断言，覆盖 Unicode、嵌套对象与异常消息。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fithealth_agent import observability as obs
from fithealth_agent.observability import config as trace_config
from fithealth_agent.observability import sink as trace_sink


#: 含 Unicode、emoji 与标点的健康描述。任何一段出现在 meta 级别的产物里都算泄露。
SENTINELS = (
    "我左膝盖有点疼",
    "膝关节术后 6 周，禁止深蹲",
    "diagnosed with 高血压 🩺",
)

_TRACE_ENV = (
    "FITHEALTH_TRACE",
    "FITHEALTH_TRACE_DETAIL",
    "FITHEALTH_TRACE_DIR",
    "FITHEALTH_TRACE_STREAM",
    "FITHEALTH_TRACE_MAX_TURNS",
    "FITHEALTH_TRACE_MAX_DAYS",
    "FITHEALTH_TRACE_MAX_BYTES",
    "FITHEALTH_TRACE_PRICE_JSON",
    "FITHEALTH_DATA_DIR",
)


@contextmanager
def trace_env(**overrides: str):
    """把 trace 相关环境变量整体换掉，并给一个干净的数据目录。

    每次都清 `sink` 的目录校验缓存与 `config` 的告警去重集合——它们是模块级状态，
    不清会让"前一条用例已经探过盘/已经告警过"污染后一条。
    """
    previous = {name: os.environ.get(name) for name in _TRACE_ENV}
    with tempfile.TemporaryDirectory(prefix="fithealth-trace-test-") as temp:
        for name in _TRACE_ENV:
            os.environ.pop(name, None)
        os.environ["FITHEALTH_DATA_DIR"] = temp
        os.environ.update(overrides)
        trace_sink.reset_writable_cache()
        trace_config._warned.clear()
        try:
            yield Path(temp)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            trace_sink.reset_writable_cache()
            trace_config._warned.clear()


def read_turn(root: Path) -> list[dict]:
    """读出唯一那个回合文件的事件列表。"""
    files = sorted((root / "traces").rglob("turn-*.jsonl"))
    assert len(files) == 1, f"期望恰好一个回合文件，实际 {[p.name for p in files]}"
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def turn_blob(root: Path) -> str:
    """回合目录下所有产物的原始文本，用于反向断言。"""
    return "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (root / "traces").rglob("*")
        if path.is_file()
    )


def event_of(events: list[dict], kind: str) -> dict:
    return next(item for item in events if item["kind"] == kind)


class DisabledByDefaultTest(unittest.TestCase):
    """关闭状态必须是真正的零副作用，否则 trace 没法默认发布。"""

    def test_trace_event_outside_a_turn_is_a_no_op(self) -> None:
        with trace_env():
            self.assertIsNone(obs.trace_event("gate", "health_risk", level="urgent"))
            self.assertIsNone(obs.current_turn())

    def test_disabled_start_turn_creates_no_directory_and_no_file(self) -> None:
        with trace_env() as root:
            # 先造一个已存在的目录，确认"没有新文件"不是因为目录本来就没建起来。
            (root / "traces").mkdir()
            with obs.start_turn("/chat", source="chat", message=SENTINELS[0]) as trace:
                self.assertIsNone(trace, "关闭时不该产出 TurnTrace")
                self.assertIsNone(obs.current_turn(), "关闭时不该设置 ContextVar")
                obs.trace_event("gate", "health_risk", level="urgent")
                obs.set_turn_result(source="agent", status_code=200)
            self.assertEqual(sorted((root / "traces").rglob("*")), [])

    def test_an_invalid_enabled_value_stays_off(self) -> None:
        for raw in ("yes", "1", "true", "ON ", ""):
            with self.subTest(value=raw), trace_env(FITHEALTH_TRACE=raw) as root:
                with obs.start_turn("/chat") as trace:
                    pass
                created = list((root / "traces").rglob("*")) if (root / "traces").is_dir() else []
                # "ON " 去空格后等于 on，是合法的；其余一律安全关闭。
                if raw.strip().casefold() == "on":
                    self.assertIsNotNone(trace)
                else:
                    self.assertIsNone(trace)
                    self.assertEqual(created, [])


class ConfigValidationTest(unittest.TestCase):
    """非法配置一律安全关闭，不猜意图（否则会以为 trace 开着，其实记的是别的东西）。"""

    def test_an_unknown_detail_level_disables_tracing(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_DETAIL="verbose"):
            self.assertFalse(obs.load_settings().enabled)

    def test_non_positive_or_non_numeric_limits_disable_tracing(self) -> None:
        for name, raw in (
            ("FITHEALTH_TRACE_MAX_TURNS", "0"),
            ("FITHEALTH_TRACE_MAX_DAYS", "-1"),
            ("FITHEALTH_TRACE_MAX_BYTES", "lots"),
        ):
            with self.subTest(variable=name), trace_env(**{"FITHEALTH_TRACE": "on", name: raw}):
                self.assertFalse(
                    obs.load_settings().enabled,
                    "保留上限非法时不能'先记着以后再说'，那正是无限增长的开始",
                )

    def test_a_malformed_price_table_degrades_to_unknown_cost(self) -> None:
        for raw in ("not json", "[]", '{"m": {"in": -1, "out": 1}}', '{"m": {"in": true, "out": 1}}'):
            with self.subTest(value=raw), trace_env(
                FITHEALTH_TRACE="on", FITHEALTH_TRACE_PRICE_JSON=raw
            ):
                settings = obs.load_settings()
                self.assertTrue(settings.enabled, "价格表错误只该让成本未知，不该关掉 trace")
                self.assertEqual(dict(settings.prices), {})

    def test_a_valid_price_table_is_parsed(self) -> None:
        with trace_env(
            FITHEALTH_TRACE="on",
            FITHEALTH_TRACE_PRICE_JSON='{"deepseek-chat": {"in": 2e-7, "out": 8e-7}}',
        ):
            prices = obs.load_settings().prices
            self.assertEqual(prices["deepseek-chat"]["out"], 8e-7)

    def test_the_directory_defaults_under_the_data_dir(self) -> None:
        with trace_env() as root:
            self.assertEqual(obs.trace_dir(), root / "traces")

    def test_an_explicit_directory_wins(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            with trace_env(FITHEALTH_TRACE_DIR=elsewhere):
                self.assertEqual(obs.trace_dir(), Path(elsewhere))

    def test_settings_are_read_on_every_call(self) -> None:
        with trace_env():
            self.assertFalse(obs.load_settings().enabled)
            os.environ["FITHEALTH_TRACE"] = "on"
            self.assertTrue(obs.load_settings().enabled)


class RedactionTest(unittest.TestCase):
    """`meta` 是默认级别，也是 trace 能长期开着的前提。"""

    def test_meta_level_never_writes_free_text(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat", source="chat", message=SENTINELS[0]):
                obs.trace_event(
                    "gate", "plan_context",
                    decision="rest_today",
                    active_safety_constraints=[SENTINELS[1], SENTINELS[2]],
                )
                obs.trace_event("tool_result", "query_sleep", status="success", result=SENTINELS[1])
            blob = turn_blob(root)
            for sentinel in SENTINELS:
                with self.subTest(sentinel=sentinel):
                    self.assertNotIn(sentinel, blob)

    def test_meta_level_keeps_length_and_digest_per_field(self) -> None:
        """字段级正向断言：不落原文≠什么都不记，否则排障时长度对不上也查不出来。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat", source="chat", message=SENTINELS[0]):
                pass
            payload = event_of(read_turn(root), "turn_start")["payload"]
            self.assertEqual(payload["message_len"], len(SENTINELS[0]))
            self.assertRegex(payload["message_hmac12"], r"^[0-9a-f]{12}$")
            self.assertNotIn("message", payload)

    def test_the_digest_is_keyed_so_low_entropy_phrases_resist_lookup(self) -> None:
        """裸 sha256 对"膝盖疼"这类低熵短语等于明文，所以摘要必须带密钥。"""
        import hashlib

        with trace_env():
            os.environ["FITHEALTH_SIGNING_KEY"] = "key-a"
            first = obs.text_digest(SENTINELS[0])
            os.environ["FITHEALTH_SIGNING_KEY"] = "key-b"
            second = obs.text_digest(SENTINELS[0])
            os.environ.pop("FITHEALTH_SIGNING_KEY", None)
        self.assertNotEqual(first, second, "换密钥必须换摘要")
        self.assertNotEqual(
            first, hashlib.sha256(SENTINELS[0].encode()).hexdigest()[:12],
            "摘要不能等于裸 sha256",
        )

    def test_full_level_records_raw_text_and_warns(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_DETAIL="full") as root:
            with self.assertLogs("fithealth", level="WARNING") as captured:
                with obs.start_turn("/chat", source="chat", message=SENTINELS[0]):
                    pass
            self.assertIn(SENTINELS[0], turn_blob(root))
            self.assertTrue(
                any("FITHEALTH_TRACE_DETAIL" in line for line in captured.output),
                "full 是唯一会落原文的开关，每次生效都要留痕",
            )

    def test_unregistered_field_values_are_dropped_but_names_are_kept(self) -> None:
        """名字来自我们的源码（安全，且漏注册要看得见），值可能来自用户（一律丢）。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                obs.trace_event("gate", "health_risk", level="urgent", secret_note=SENTINELS[0])
            gate = event_of(read_turn(root), "gate")["payload"]
            self.assertEqual(gate["level"], "urgent")
            self.assertNotIn("secret_note", gate)
            self.assertIn("secret_note", gate["_dropped"])
            self.assertNotIn(SENTINELS[0], turn_blob(root))

    def test_an_unregistered_kind_drops_every_value(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                obs.trace_event("mystery_kind", "whatever", note=SENTINELS[0])
            payload = event_of(read_turn(root), "mystery_kind")["payload"]
            self.assertTrue(payload["_unknown_kind"])
            self.assertEqual(payload["_dropped"], ["note"])
            self.assertNotIn(SENTINELS[0], turn_blob(root))

    def test_an_unregistered_span_is_flagged_but_recorded(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                obs.trace_event("gate", "gate_that_does_not_exist", outcome="blocked")
            payload = event_of(read_turn(root), "gate")["payload"]
            self.assertTrue(payload["_unknown_span"])
            self.assertEqual(payload["outcome"], "blocked")

    def test_objects_and_nested_containers_never_leak_via_str(self) -> None:
        """structural 字段只接受基本类型：否则 Path / 自定义对象会顺手把原文 str 出来。"""
        class Leaky:
            def __str__(self) -> str:
                return SENTINELS[2]

        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                obs.trace_event("gate", "soreness", saved_count=Leaky())
                obs.trace_event("gate", "profile_gate", missing_fields={"deep": {"deeper": {"x": Leaky()}}})
            self.assertNotIn(SENTINELS[2], turn_blob(root))
            events = read_turn(root)
            self.assertIn("saved_count", event_of(events, "gate")["payload"]["_dropped"])


class LimitsTest(unittest.TestCase):
    """没有上限，一个回合就能写出几 MB——而 trace 是默认可在生产开启的。"""

    def test_events_beyond_the_buffer_cap_are_counted_not_written(self) -> None:
        from dataclasses import replace

        from fithealth_agent.observability import trace as trace_module

        with trace_env(FITHEALTH_TRACE="on") as root:
            with mock.patch.object(
                trace_module, "LIMITS", replace(obs.LIMITS, events_per_turn=5)
            ):
                with obs.start_turn("/chat"):
                    for _ in range(20):
                        obs.trace_event("gate", "health_risk", level="caution")
            events = read_turn(root)
            self.assertEqual(len(events), 5)
            # turn_end 自己也被上限挡在外面，所以要从 index 读状态。
            index = json.loads((root / "traces" / "index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(index["events"], 5)

    def test_the_turn_end_counters_report_degradation(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                obs.trace_event("gate", "health_risk", level="caution", bogus=1, alsobogus=2)
            end = event_of(read_turn(root), "turn_end")["payload"]
            self.assertEqual(end["fields_dropped"], 2)
            self.assertEqual(end["trace_errors"], 0)
            self.assertEqual(end["events_dropped"], 0)
            self.assertTrue(end["complete"])

    def test_a_long_text_field_is_truncated_with_a_flag(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_DETAIL="full") as root:
            long_text = "深蹲" * 5_000
            with obs.start_turn("/chat", message=long_text):
                pass
            payload = event_of(read_turn(root), "turn_start")["payload"]
            self.assertEqual(payload["message_len"], len(long_text))
            self.assertLessEqual(len(payload["message"]), obs.LIMITS.text_chars)
            self.assertTrue(payload["message_truncated"])

    def test_an_oversized_event_degrades_instead_of_growing(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_DETAIL="full") as root:
            with obs.start_turn("/chat"):
                obs.trace_event(
                    "gate", "plan_validation",
                    violations=["违规" * 1_500 for _ in range(20)],
                )
            payload = event_of(read_turn(root), "gate")["payload"]
            self.assertTrue(payload["_oversize"])
            line = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.assertLessEqual(len(line), obs.LIMITS.event_bytes)
            # 降级不能抹掉"这个字段存在过"这个事实。
            self.assertEqual(payload["violations_count"], 20)


class TurnLifecycleTest(unittest.TestCase):
    """`turn_end` 恰好一条，且三条退出路径的状态可区分。"""

    def _run_and_read(self, root: Path, raiser) -> list[dict]:
        with self.assertRaises(type(raiser)):
            with obs.start_turn("/chat", source="chat"):
                obs.trace_event("gate", "health_risk", level="urgent")
                raise raiser
        return read_turn(root)

    def test_a_normal_turn_records_exactly_one_turn_end(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat", source="chat"):
                obs.set_turn_result(source="agent", status_code=200, artifact_type="training_plan")
            events = read_turn(root)
            ends = [item for item in events if item["kind"] == "turn_end"]
            self.assertEqual(len(ends), 1)
            self.assertEqual(ends[0]["payload"]["status"], "ok")
            self.assertEqual(ends[0]["payload"]["source"], "agent")
            self.assertEqual(ends[0]["payload"]["status_code"], 200)
            self.assertIsInstance(ends[0]["dur_ms"], int)

    def test_a_business_exception_still_lands_with_status_error(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            events = self._run_and_read(root, ValueError("卧推重量不合法"))
            end = event_of(events, "turn_end")["payload"]
            self.assertEqual(end["status"], "error")
            self.assertEqual(end["error_type"], "ValueError")

    def test_error_type_is_the_class_name_not_the_message_or_traceback(self) -> None:
        """异常消息可能带健康原文；只记类名，不记 message、不记堆栈。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            events = self._run_and_read(root, ValueError(SENTINELS[0]))
            self.assertEqual(event_of(events, "turn_end")["payload"]["error_type"], "ValueError")
            blob = turn_blob(root)
            self.assertNotIn(SENTINELS[0], blob)
            self.assertNotIn("Traceback", blob)

    def test_cancellation_is_distinguishable_from_a_real_failure(self) -> None:
        """用户关页面导致的取消不该看起来像一次服务错误。"""
        for raiser in (asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(1)):
            with self.subTest(exc=type(raiser).__name__), trace_env(FITHEALTH_TRACE="on") as root:
                events = self._run_and_read(root, raiser)
                end = event_of(events, "turn_end")["payload"]
                self.assertEqual(end["status"], "aborted")
                self.assertEqual(end["error_type"], type(raiser).__name__)

    def test_seq_is_dense_and_monotonic(self) -> None:
        """事件顺序只看 seq，不看 ts（并行工具的墙钟会交错）。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                for level in ("caution", "urgent", "emergency"):
                    obs.trace_event("gate", "health_risk", level=level)
            self.assertEqual(
                [item["seq"] for item in read_turn(root)], [1, 2, 3, 4, 5]
            )

    def test_a_nested_start_turn_reuses_the_outer_turn(self) -> None:
        """HTTP 边界与 workflow 都可能开 turn，谁先谁定，绝不写出第二个文件。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat", source="chat") as outer:
                with obs.start_turn("/chat/inner") as inner:
                    self.assertIs(inner, outer)
                    obs.trace_event("gate", "health_risk", level="caution")
            # read_turn 自带"恰好一个文件"的断言。
            events = read_turn(root)
            self.assertEqual([item["kind"] for item in events].count("turn_start"), 1)
            self.assertEqual([item["kind"] for item in events].count("turn_end"), 1)

    def test_events_after_the_turn_closed_are_dropped_not_written(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat") as trace:
                pass
            self.assertIsNone(trace.add("gate", "health_risk", level="urgent"))
            self.assertNotIn("urgent", turn_blob(root))

    def test_the_index_line_matches_the_turn_file(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat", source="chat") as trace:
                obs.trace_event("gate", "health_risk", level="caution")
                turn_id = trace.turn_id
            index = json.loads((root / "traces" / "index.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(index["turn_id"], turn_id)
            self.assertEqual(index["route"], "/chat")
            self.assertEqual(index["status"], "ok")
            self.assertEqual(index["events"], len(read_turn(root)))
            self.assertEqual(index["file"], f"turn-{turn_id}.jsonl")
            self.assertTrue((root / "traces" / index["day"] / index["file"]).is_file())

    def test_a_colliding_turn_id_never_overwrites_the_earlier_turn(self) -> None:
        """turn_id 带 6 字节随机后缀，但时钟回拨或多进程仍可能撞名。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            fixed = "t-20260904-120000-abcdef"
            with mock.patch.object(obs, "new_turn_id", return_value=fixed), \
                 mock.patch("fithealth_agent.observability.trace.new_turn_id", return_value=fixed):
                for _ in range(2):
                    with obs.start_turn("/chat"):
                        obs.trace_event("gate", "health_risk", level="caution")
            names = sorted(path.name for path in (root / "traces").rglob("turn-*.jsonl"))
            self.assertEqual(names, [f"turn-{fixed}-1.jsonl", f"turn-{fixed}.jsonl"])

    def test_turn_ids_are_unpredictable(self) -> None:
        from datetime import datetime

        now = datetime.now(obs.TZ)
        suffixes = {obs.new_turn_id(now).rsplit("-", 1)[1] for _ in range(64)}
        self.assertEqual(len(suffixes), 64, "turn_id 出现在文件名里，可预测等于可预测路径")


class FailureIsolationTest(unittest.TestCase):
    """可观测性故障不能变成业务故障——这是 trace 敢默认开着的前提。"""

    def test_a_failing_add_never_propagates_and_keeps_the_return_value(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat") as trace:
                with mock.patch.object(
                    type(trace), "add", side_effect=RuntimeError("trace 内部炸了")
                ):
                    self.assertIsNone(obs.trace_event("gate", "health_risk", level="urgent"))
                    business_value = 42  # 业务代码在同一个 with 里继续跑
                self.assertEqual(business_value, 42)
            self.assertGreater(read_turn(root)[-1]["payload"]["trace_errors"], 0)

    def test_warnings_are_throttled_but_the_count_is_not(self) -> None:
        """告警限流是为了不刷日志；降级计数不能跟着一起被压掉，否则会静默累积。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with self.assertLogs("fithealth", level="WARNING") as captured:
                with obs.start_turn("/chat") as trace:
                    with mock.patch.object(type(trace), "add", side_effect=RuntimeError("boom")):
                        for _ in range(20):
                            obs.trace_event("gate", "health_risk", level="urgent")
            degraded = [line for line in captured.output if "trace 降级" in line]
            self.assertEqual(len(degraded), 3, "每回合最多 3 条降级告警")
            self.assertEqual(read_turn(root)[-1]["payload"]["trace_errors"], 20)

    def test_a_failure_inside_recording_does_not_recurse(self) -> None:
        """坏 payload 触发的失败若再走一次记录路径，一条事件就能把栈打穿。"""
        calls: list[str] = []

        class Recursive:
            def __str__(self) -> str:
                calls.append("str")
                obs.trace_event("gate", "health_risk", level="nested")
                raise RuntimeError("炸在 __str__ 里")

        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                obs.trace_event("tool_result", "query_sleep", result=Recursive())
            self.assertEqual(calls, ["str"], "递归护栏没拦住")
            self.assertNotIn("nested", turn_blob(root))

    def test_a_write_failure_does_not_break_the_business_path(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with mock.patch.object(
                trace_sink, "atomic_write_text", side_effect=OSError("磁盘满了")
            ):
                with obs.start_turn("/chat") as trace:
                    obs.trace_event("gate", "health_risk", level="caution")
                    self.assertIsNotNone(trace)
            self.assertEqual(list((root / "traces").rglob("turn-*.jsonl")), [])
            # 回合文件写失败时 index 仍要留一行，且明确标记没有文件。
            index = json.loads((root / "traces" / "index.jsonl").read_text(encoding="utf-8"))
            self.assertIsNone(index["file"])

    def test_low_disk_space_disables_tracing_instead_of_filling_the_disk(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            usage = mock.Mock(free=1024, total=1 << 30, used=(1 << 30) - 1024)
            with mock.patch.object(trace_sink.shutil, "disk_usage", return_value=usage):
                with obs.start_turn("/chat") as trace:
                    self.assertIsNone(trace, "剩余空间不足时不该开 trace")
            self.assertEqual(list((root / "traces").rglob("turn-*.jsonl")), [])

    def test_a_permission_error_on_the_directory_disables_tracing(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with mock.patch.object(Path, "mkdir", side_effect=PermissionError("拒绝访问")):
                with obs.start_turn("/chat") as trace:
                    self.assertIsNone(trace)
            self.assertFalse((root / "traces").exists())

    def test_a_symlinked_trace_dir_is_refused(self) -> None:
        """指向别处的软链会让"数据只落在数据目录内"这条承诺静默失效。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            with mock.patch.object(Path, "is_symlink", return_value=True):
                self.assertFalse(trace_sink.ensure_writable(root / "traces"))


class ContextPropagationTest(unittest.TestCase):
    """决定 A 的落地验证。这条测试红了，说明 ContextVar 方案的前提没了。"""

    def test_events_written_in_a_threadpool_land_in_the_same_turn(self) -> None:
        from starlette.concurrency import run_in_threadpool

        def worker() -> str:
            obs.trace_event("gate", "plan_context", decision="rest_today")
            return threading.current_thread().name

        async def scenario() -> str:
            with obs.start_turn("/chat", source="chat"):
                name = await run_in_threadpool(worker)
                # 工作线程写进去的事件，回到异步层必须在同一个 TurnTrace 里。
                self.assertEqual(
                    [item["span"] for item in obs.current_turn().events],
                    ["/chat", "plan_context"],
                )
            return name

        with trace_env(FITHEALTH_TRACE="on") as root:
            thread_name = asyncio.run(scenario())
            self.assertNotEqual(thread_name, threading.main_thread().name)
            self.assertEqual(
                [item["kind"] for item in read_turn(root)],
                ["turn_start", "gate", "turn_end"],
            )

    def test_concurrent_turns_never_write_into_each_other(self) -> None:
        async def one(index: int) -> str:
            with obs.start_turn("/chat", source=f"task-{index}") as trace:
                await asyncio.sleep(0)
                obs.trace_event("gate", "health_risk", level="caution")
                await asyncio.sleep(0)
                obs.set_turn_result(status_code=200 + index)
                return trace.turn_id

        async def scenario() -> list[str]:
            return await asyncio.gather(*(one(index) for index in range(4)))

        with trace_env(FITHEALTH_TRACE="on") as root:
            ids = asyncio.run(scenario())
            self.assertEqual(len(set(ids)), 4)
            files = sorted((root / "traces").rglob("turn-*.jsonl"))
            self.assertEqual(len(files), 4)
            for path in files:
                events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                # 每个文件都只含自己的 turn_id，且事件数一致——没有串写。
                self.assertEqual({item["turn_id"] for item in events}, {events[0]["turn_id"]})
                self.assertEqual([item["kind"] for item in events],
                                 ["turn_start", "gate", "turn_end"])

    def test_parallel_appends_lose_no_event(self) -> None:
        """框架的 `_execute_tools_async` 会并行跑工具，append 必须持锁。

        用 `copy_context().run` 起线程，因为**裸 `threading.Thread` 不继承
        ContextVar**（见下一条用例）。starlette 的 `run_in_threadpool` 与
        `asyncio.to_thread` 都会复制上下文，所以这才是真实并发的形状。
        """
        import contextvars

        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                def hammer() -> None:
                    for _ in range(50):
                        obs.trace_event("tool_call", "query_sleep", tool_call_id="x")

                threads = [
                    threading.Thread(target=contextvars.copy_context().run, args=(hammer,))
                    for _ in range(8)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            events = read_turn(root)
            self.assertEqual(len(events), 8 * 50 + 2)
            # seq 必须密集无重复：顺序信息只由它承载。
            self.assertEqual(
                sorted(item["seq"] for item in events), list(range(1, len(events) + 1))
            )

    def test_a_bare_thread_inherits_no_turn(self) -> None:
        """裸 `threading.Thread` 起的线程拿到的是空 Context，记不进当前回合。

        这是决定 A 的已知边界，钉在这里是为了**别人加后台线程时能查到**：要让
        事件进 turn，必须走 `run_in_threadpool` / `asyncio.to_thread` /
        `copy_context().run`，而不是 `Thread(target=...)`。
        """
        seen: list[object] = []

        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                thread = threading.Thread(target=lambda: seen.append(obs.current_turn()))
                thread.start()
                thread.join()
            self.assertEqual(seen, [None])
            self.assertEqual(
                [item["kind"] for item in read_turn(root)], ["turn_start", "turn_end"]
            )


class SpanNestingTest(unittest.TestCase):
    def test_a_span_records_duration_and_its_parent(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                with obs.span("react_step", "agent", step=1):
                    with obs.span("tool_call", "query_sleep", tool_call_id="c1"):
                        pass
            events = read_turn(root)
            inner = event_of(events, "tool_call")
            outer = event_of(events, "react_step")
            self.assertEqual(inner["parent"], outer["sid"])
            self.assertNotIn("parent", outer)
            # 内层先退出，所以 seq 更小；嵌套关系只由 sid/parent 表达。
            self.assertLess(inner["seq"], outer["seq"])
            self.assertIsInstance(inner["dur_ms"], int)

    def test_a_span_records_the_error_type_and_reraises(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with self.assertRaises(TimeoutError):
                with obs.start_turn("/chat"):
                    with obs.span("model_call", "route_chat_intent", ok=False):
                        raise TimeoutError(SENTINELS[0])
            payload = event_of(read_turn(root), "model_call")["payload"]
            self.assertEqual(payload["error_type"], "TimeoutError")
            self.assertNotIn(SENTINELS[0], turn_blob(root))

    def test_a_span_outside_a_turn_is_a_no_op(self) -> None:
        with trace_env():
            with obs.span("model_call", "route_chat_intent"):
                pass


class StreamModeTest(unittest.TestCase):
    """挂死排查用。默认必须关闭。"""

    def test_stream_mode_writes_each_event_immediately(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_STREAM="1") as root:
            with obs.start_turn("/chat") as trace:
                obs.trace_event("gate", "health_risk", level="caution")
                # 回合还没结束，事件就应该已经在盘上——这正是它存在的理由。
                lines = trace.path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 2)
            self.assertEqual(len(read_turn(root)), 3)

    def test_buffered_mode_writes_nothing_before_the_turn_ends(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat") as trace:
                obs.trace_event("gate", "health_risk", level="caution")
                self.assertFalse(trace.path.exists())
            self.assertTrue(trace.path.is_file())


class SchemaRegistryTest(unittest.TestCase):
    """契约表本身要有守门器：漏一项就等于那类事件永远记不全。"""

    def test_every_kind_declares_fields(self) -> None:
        for kind, spec in obs.EVENT_SPECS.items():
            with self.subTest(kind=kind):
                self.assertTrue(spec.fields or spec.span_fields, f"{kind} 没有任何字段")

    def test_every_field_class_is_known(self) -> None:
        from fithealth_agent.observability.schema import S, T, TL

        for kind, spec in obs.EVENT_SPECS.items():
            tables = [spec.fields, *(spec.span_fields or {}).values()]
            for table in tables:
                for name, field_class in table.items():
                    with self.subTest(kind=kind, field=name):
                        self.assertIn(field_class, (S, T, TL))

    def test_every_gate_span_has_its_own_field_table(self) -> None:
        """审查意见：每个闸门固定 schema，否则字段漂移会让查询静默失效。"""
        from fithealth_agent.observability.schema import GATE_SPANS

        spec = obs.EVENT_SPECS["gate"]
        self.assertEqual(spec.spans, GATE_SPANS)
        self.assertEqual(set(spec.span_fields), set(GATE_SPANS))

    def test_the_schema_version_is_recorded_on_every_event(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            with obs.start_turn("/chat"):
                obs.trace_event("gate", "health_risk", level="caution")
            for event in read_turn(root):
                with self.subTest(kind=event["kind"]):
                    self.assertEqual(event["v"], obs.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()













