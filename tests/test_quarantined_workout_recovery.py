"""DATA-05 回归测试：合法数据不得被当成"损坏"单向隔离掉。

原缺陷链：一条 `if not segments_data: raise ValueError("训练分段为空")` 把
空 sets（有氧/跳绳/无 lap 的 FIT 完全正常）判为损坏 → 隔离动作用
`_PERSIST_PATH.replace()` 单向搬走 → 既没有列表接口也没有重放接口，
合法数据等于永久丢失。另外确认保存时会丢弃整段 1Hz 心率流。

本文件钉住四条不变量：
1. 隔离用 copy-then-unlink，且隔离失败时**保留**原文件（不再 unlink）；
2. 严格恢复失败先降级为"部分可用"，只有连 session 都读不出来才隔离；
3. 隔离文件可列出、可重放，且重放接口拒绝路径穿越；
4. 确认保存时 1Hz 心率流旁挂落盘，训练记录里只留摘要。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import module_source


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class HRStreamStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(
            "hr_stream_store_for_test", PACKAGE_DIR / "hr_stream_store.py"
        )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = self.module.HRStreamStore(Path(self.temp_dir.name) / "hr_streams")
        base = datetime(2026, 8, 15, 10, 52, 53, tzinfo=timezone.utc)
        self.samples = [
            {"timestamp": (base + timedelta(seconds=i)).isoformat(), "heart_rate": 90 + i % 10}
            for i in range(50)
        ]

    def test_summary_is_constant_size_and_stream_is_recoverable(self) -> None:
        summary = self.store.save("rec-1", self.samples)
        # 摘要是唯一允许进入 LLM 上下文的部分，字段数固定
        self.assertEqual(summary["samples"], 50)
        self.assertEqual(summary["min"], 90)
        self.assertEqual(summary["max"], 99)
        self.assertEqual(summary["file"], "rec-1.json")
        self.assertNotIn("samples_list", summary)
        # 原始流仍可按 id 完整取回，供 Python 重算区间心率
        self.assertEqual(self.store.load("rec-1"), self.samples)

    def test_empty_stream_writes_no_file(self) -> None:
        summary = self.store.save("rec-empty", [])
        self.assertEqual(summary, {"samples": 0})
        self.assertEqual(list(self.store.stream_dir.glob("*.json")), [])

    def test_path_traversal_in_record_id_is_refused(self) -> None:
        # 沿用 DATA-02 的归属校验：只取 basename 且父目录必须是 stream_dir
        outside = Path(self.temp_dir.name) / "escaped.json"
        summary = self.store.save("../escaped", self.samples)
        self.assertFalse(outside.exists())
        # basename 化之后落在 stream_dir 内，绝不越界
        self.assertEqual(summary.get("file"), "escaped.json")
        self.assertTrue((self.store.stream_dir / "escaped.json").exists())

    def test_summarize_skips_malformed_samples(self) -> None:
        summary = self.module.HRStreamStore.summarize(
            [
                {"timestamp": "2026-08-15T10:00:00+00:00", "heart_rate": 100},
                {"timestamp": "2026-08-15T10:00:01+00:00", "heart_rate": None},
                {"timestamp": None, "heart_rate": 120},
                "不是对象",
                {"timestamp": "2026-08-15T10:00:02+00:00", "heart_rate": "坏值"},
            ]
        )
        self.assertEqual(summary["samples"], 1)
        self.assertEqual(summary["avg"], 100.0)

    def test_delete_and_clear(self) -> None:
        self.store.save("rec-1", self.samples)
        self.store.save("rec-2", self.samples)
        self.assertTrue(self.store.delete("rec-1"))
        self.assertFalse(self.store.delete("rec-1"))
        self.assertEqual(self.store.clear(), 1)

    def test_leaves_no_temp_files(self) -> None:
        self.store.save("rec-1", self.samples)
        self.assertEqual(list(self.store.stream_dir.glob("*.tmp")), [])


class QuarantineAndReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_cwd = Path.cwd()
        self.previous_data_dir = os.environ.get("FITHEALTH_DATA_DIR")
        self.addCleanup(self._cleanup)
        os.chdir(self.temp_dir.name)
        os.environ["FITHEALTH_DATA_DIR"] = str(Path(self.temp_dir.name) / "data")
        self._module_names = (
            "fithealth_agent.workout_store",
            "fithealth_agent.hr_stream_store",
            "fithealth_agent.storage",
            "fithealth_agent.fit_parser",
            "fithealth_agent",
        )
        self._saved_modules = {name: sys.modules.get(name) for name in self._module_names}
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
        self.fit_parser = load_module(
            "fithealth_agent.fit_parser", PACKAGE_DIR / "fit_parser.py"
        )

    def _cleanup(self) -> None:
        if self.previous_data_dir is None:
            os.environ.pop("FITHEALTH_DATA_DIR", None)
        else:
            os.environ["FITHEALTH_DATA_DIR"] = self.previous_data_dir
        os.chdir(self.previous_cwd)
        self.temp_dir.cleanup()
        for name in self._module_names:
            original = self._saved_modules[name]
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def load_store(self):
        sys.modules.pop("fithealth_agent.workout_store", None)
        return load_module(
            "fithealth_agent.workout_store", PACKAGE_DIR / "workout_store.py"
        )

    def _write_pending(self, payload: dict) -> Path:
        path = Path("data") / "pending_workout.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _cardio_payload(self) -> dict:
        """一段完全合法的有氧活动：sets 为空、只有 session 与心率流。

        这正是被误隔离的那两个文件的形状。
        """
        base = datetime(2026, 8, 15, 10, 52, 53, tzinfo=timezone.utc)
        return {
            "version": 2,
            "source_file": "cardio.fit",
            "source_sha256": "abc123",
            "parsed_at": "2026-08-16T08:54:22+00:00",
            "note": "",
            "session": {
                "sport": "有氧运动",
                "sport_raw": "training",
                "sub_sport": "cardio_training",
                "start_time": base.isoformat(),
                "total_timer_s": 3853.5,
                "total_calories": 232,
                "avg_hr": 92,
                "max_hr": 147,
            },
            "sets": [],
            "hr_records": [
                {"timestamp": (base + timedelta(seconds=i)).isoformat(), "heart_rate": 90 + i % 10}
                for i in range(30)
            ],
        }

    # ------------------------------------------------------------------
    # 不变量 1 & 2：合法的空 sets 不该被隔离；坏字段先降级
    # ------------------------------------------------------------------

    def test_empty_sets_cardio_restores_normally(self) -> None:
        self._write_pending(self._cardio_payload())
        store = self.load_store()
        restored = store.get_current()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.segments, [])
        self.assertEqual(len(restored.hr_records), 30)
        self.assertIsNone(store.get_restore_issue())
        self.assertEqual(store.list_quarantined(), [])

    def test_one_bad_segment_degrades_instead_of_quarantining(self) -> None:
        payload = self._cardio_payload()
        payload["sets"] = [{"index": 1, "segment_type": "set_active"}]  # 缺 start_time
        self._write_pending(payload)
        store = self.load_store()
        restored = store.get_current()
        # session 与心率流都保住了，只是丢了那个读不出来的分段
        self.assertIsNotNone(restored)
        self.assertEqual(len(restored.hr_records), 30)
        self.assertEqual(restored.segments, [])
        issue = store.get_restore_issue()
        self.assertEqual(issue["code"], "PARTIAL_PENDING_WORKOUT")
        self.assertIn("分段", issue["reason"])
        # 没有产生隔离文件
        self.assertEqual(store.list_quarantined(), [])

    def test_unreadable_file_is_copied_aside_then_removed(self) -> None:
        path = self._write_pending(self._cardio_payload())
        broken = "{不是合法 JSON"
        path.write_text(broken, encoding="utf-8")
        store = self.load_store()
        self.assertIsNone(store.get_current())
        issue = store.get_restore_issue()
        self.assertEqual(issue["action"], "quarantined")
        quarantine = Path("data") / issue["quarantine_file"]
        # 副本内容与原文件逐字节一致，且原文件已让位（避免下次启动反复重试）
        self.assertEqual(quarantine.read_text(encoding="utf-8"), broken)
        self.assertFalse(path.exists())

    def test_quarantine_failure_keeps_the_original_file(self) -> None:
        # 原实现在隔离失败时直接 unlink，等于销毁唯一一份数据
        path = self._write_pending(self._cardio_payload())
        broken = "{不是合法 JSON"
        path.write_text(broken, encoding="utf-8")
        store = self.load_store()
        store.clear_current()
        path.write_text(broken, encoding="utf-8")

        def explode(*_args, **_kwargs):
            raise OSError("磁盘满了")

        # 只重新绑定 workout_store 模块内的 shutil 名字，绝不改真正的
        # shutil 模块——否则会污染同一进程里其他测试。
        store.shutil = types.SimpleNamespace(copy2=explode)
        issue = store._quarantine_corrupt_persisted(ValueError("坏了"))
        self.assertEqual(issue["action"], "kept")
        self.assertIsNone(issue["quarantine_file"])
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), broken)

    # ------------------------------------------------------------------
    # 不变量 3：隔离文件可列出、可重放
    # ------------------------------------------------------------------

    def _make_quarantine_file(self, store, payload: dict) -> str:
        path = Path("data") / "pending_workout.corrupt-20260816T085939230364Z.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path.name

    def test_list_quarantined_previews_recoverable_content(self) -> None:
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        listed = store.list_quarantined()
        self.assertEqual(len(listed), 1)
        entry = listed[0]
        self.assertEqual(entry["name"], name)
        self.assertTrue(entry["recoverable"])
        self.assertEqual(entry["sport"], "有氧运动")
        self.assertEqual(entry["hr_records"], 30)
        self.assertEqual(entry["segments"], 0)
        self.assertEqual(entry["max_hr"], 147)

    def test_list_quarantined_reports_truly_broken_files(self) -> None:
        store = self.load_store()
        path = Path("data") / "pending_workout.corrupt-20260101T000000000000Z.json"
        path.write_text("{坏", encoding="utf-8")
        entry = store.list_quarantined()[0]
        self.assertFalse(entry["recoverable"])
        self.assertTrue(entry["reason"])

    def test_replay_restores_quarantined_workout(self) -> None:
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        result = store.replay_quarantined(name)
        self.assertTrue(result["replayed"])
        self.assertEqual(result["hr_records"], 30)
        current = store.get_current()
        self.assertIsNotNone(current)
        self.assertEqual(len(current.hr_records), 30)
        # 重放后走的是正常确认流程，因此必须有一套可用的确认凭据
        self.assertIsNotNone(store.get_confirmation_context())
        # 隔离文件保留在原处，重放不是破坏性操作
        self.assertTrue((Path("data") / name).exists())
        # 已经载入内存并落盘成当前待确认状态
        self.assertTrue((Path("data") / "pending_workout.json").exists())

    def test_replay_rejects_path_traversal_and_foreign_names(self) -> None:
        store = self.load_store()
        self._make_quarantine_file(store, self._cardio_payload())
        for bad in (
            "../secrets.json",
            "pending_workout.json",
            "notes.txt",
            "",
            "pending_workout.corrupt-does-not-exist.json",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    store.replay_quarantined(bad)

    def test_replay_refuses_to_clobber_a_pending_workout(self) -> None:
        self._write_pending(self._cardio_payload())
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        self.assertIsNotNone(store.get_current())
        with self.assertRaises(ValueError):
            store.replay_quarantined(name)

    # ------------------------------------------------------------------
    # 「不再提醒」与永久删除：启动提示不能变成永久拦路的弹窗
    # ------------------------------------------------------------------

    def test_dismiss_hides_it_from_the_default_listing(self) -> None:
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        self.assertEqual(len(store.list_quarantined()), 1)
        store.dismiss_quarantined(name)
        # 启动提示走默认列表 → 不再被打扰
        self.assertEqual(store.list_quarantined(), [])
        # 数据管理面板仍能看到，并标出已忽略
        full = store.list_quarantined(include_dismissed=True)
        self.assertEqual(len(full), 1)
        self.assertTrue(full[0]["dismissed"])

    def test_dismiss_does_not_touch_the_data(self) -> None:
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        path = Path("data") / name
        before = path.read_bytes()
        store.dismiss_quarantined(name)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), before)

    def test_replaying_also_dismisses_so_it_stops_nagging(self) -> None:
        # 这是用户报的那个 bug 的核心：重放刻意不删文件，但也没有任何"已处理"
        # 标记，于是同一份文件每次启动都会再提示一次。
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        store.replay_quarantined(name)
        self.assertEqual(store.list_quarantined(), [])
        self.assertTrue((Path("data") / name).exists())

    def test_delete_removes_the_file_and_its_marker(self) -> None:
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        store.dismiss_quarantined(name)
        store.delete_quarantined(name)
        self.assertFalse((Path("data") / name).exists())
        self.assertFalse((Path("data") / (name + ".dismissed")).exists())
        self.assertEqual(store.list_quarantined(include_dismissed=True), [])

    def test_dismiss_and_delete_reject_path_traversal(self) -> None:
        store = self.load_store()
        self._make_quarantine_file(store, self._cardio_payload())
        for bad in ("../secrets.json", "pending_workout.json", "notes.txt", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    store.dismiss_quarantined(bad)
                with self.assertRaises(ValueError):
                    store.delete_quarantined(bad)

    def test_dismiss_marker_is_not_mistaken_for_a_quarantine_file(self) -> None:
        store = self.load_store()
        name = self._make_quarantine_file(store, self._cardio_payload())
        store.dismiss_quarantined(name)
        # glob 只匹配 .json 结尾，标记文件是 .json.dismissed
        names = [item["name"] for item in store.list_quarantined(include_dismissed=True)]
        self.assertEqual(names, [name])

    # ------------------------------------------------------------------
    # 不变量 4：确认保存时心率流旁挂，而不是丢弃
    # ------------------------------------------------------------------

    def test_confirming_attaches_hr_stream_summary_not_raw_samples(self) -> None:
        self._write_pending(self._cardio_payload())
        store = self.load_store()
        context = store.get_confirmation_context()
        result = store.confirm_workout(
            workout_id=context["workout_id"],
            version=context["version"],
            confirmation_token=context["confirmation_token"],
        )
        self.assertTrue(result["saved"], result)

        records = json.loads((Path("data") / "daily_records.json").read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)
        record = records[0]["record"]
        stream = record.get("hr_stream")
        self.assertIsInstance(stream, dict)
        self.assertEqual(stream["samples"], 30)
        self.assertEqual(stream["max"], 99)
        # 关键：原始采样点绝不能进 daily_records.json，否则 ReAct 观察会被撑爆
        serialized = json.dumps(records, ensure_ascii=False)
        self.assertNotIn("heart_rate", serialized.replace("hr_stream", ""))
        # 流本身按记录 id 旁挂，可完整取回
        stream_path = Path("data") / "hr_streams" / f"{records[0]['id']}.json"
        self.assertTrue(stream_path.exists())
        payload = json.loads(stream_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["samples"]), 30)


class SourceInvariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PACKAGE_DIR / "workout_store.py").read_text(encoding="utf-8")

    def test_quarantine_does_not_use_replace(self) -> None:
        body = self.source.split("def _quarantine_corrupt_persisted(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("_PERSIST_PATH.replace(", body)
        self.assertIn("shutil.copy2", body)

    def test_quarantine_failure_does_not_delete_the_original(self) -> None:
        body = self.source.split("def _quarantine_corrupt_persisted(", 1)[1].split("\ndef ", 1)[0]
        failure_branch = body.split("except OSError as quarantine_error:", 1)[1]
        code_only = "\n".join(
            line for line in failure_branch.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("unlink", code_only)

    def test_list_and_replay_are_exposed(self) -> None:
        self.assertIn("def list_quarantined(", self.source)
        self.assertIn("def replay_quarantined(", self.source)
        route_source = module_source(consumer_home("workout_state_routes"))
        self.assertIn("workout_store.list_quarantined(", route_source)
        self.assertIn("workout_store.replay_quarantined(", route_source)
        self.assertIn("workout_store.dismiss_quarantined(", route_source)
        self.assertIn("workout_store.delete_quarantined(", route_source)

if __name__ == "__main__":
    unittest.main()
