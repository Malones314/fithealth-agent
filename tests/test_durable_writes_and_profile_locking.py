"""DATA-06 / DATA-07 的回归测试。

DATA-06：`Path.write_text` + `replace` 只保证目录项替换原子，不保证内容落盘，
断电后可能得到 0 字节或半截 JSON。所有 JSON 写路径必须 flush + fsync 后再替换。

DATA-07：`UserProfileStore` 原先完全无锁且临时文件名固定，两个并发 PATCH 会
交错写同一个 tmp，replace 出去的是拼接垃圾；`update_profile` 的
read-modify-write 也会丢更新。

这里的取向是"钉住不变量"而不是"数一数调了几次"：断电本身没法在单元测试里
复现，所以改为验证 (a) fsync 确实发生在 replace **之前**、(b) 临时文件名互不
相同、(c) 并发写入既不产出垃圾也不丢更新。
"""

from __future__ import annotations

import ast
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fithealth_agent import atomic_json
from fithealth_agent.atomic_json import atomic_write_json, temp_write_path
from fithealth_agent.external_model_settings import ExternalModelSettingsStore
from fithealth_agent.hr_stream_store import HRStreamStore
from fithealth_agent.info_store import InfoStore
from fithealth_agent.plan_store import TrainingPlanStore
from fithealth_agent.storage import DailyRecordStore, UserProfileStore
from fithealth_agent import workout_store
from tests.module_map import consumer_home
from tests.source_tools import function_node


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


def union_equipment(profile: dict, updates: dict) -> dict:
    """测试用的追加式合并，模拟 main.py 里的器械并集逻辑。"""
    merged = dict(updates)
    existing = profile.get("equipment") or []
    incoming = updates.get("equipment") or []
    merged["equipment"] = list(dict.fromkeys([*existing, *incoming]))
    return merged


# ══════════════════════════════════════════════════════════════════════════
# DATA-06：写路径必须 fsync
# ══════════════════════════════════════════════════════════════════════════


class AtomicWriteDurabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def test_fsync_happens_before_the_replace(self) -> None:
        """顺序错了就等于没修：先 replace 再 fsync 保护不到任何东西。"""
        target = self.root / "payload.json"
        target.write_text('{"generation": 1}', encoding="utf-8")
        observed: list[str] = []
        real_fsync = os.fsync

        def recording_fsync(descriptor: int) -> None:
            # fsync 时目标文件必须还是旧内容——说明尚未 replace。
            observed.append(target.read_text(encoding="utf-8"))
            real_fsync(descriptor)

        with mock.patch.object(atomic_json.os, "fsync", recording_fsync):
            atomic_write_json(target, {"generation": 2})

        self.assertTrue(observed, "写路径完全没有调用 os.fsync")
        self.assertEqual(observed[0], '{"generation": 1}')
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"generation": 2})

    def test_temp_name_carries_pid_and_is_unique(self) -> None:
        target = self.root / "payload.json"
        names = {temp_write_path(target).name for _ in range(64)}
        self.assertEqual(len(names), 64, "临时文件名重复了，并发写入会互相踩")
        for name in names:
            self.assertTrue(name.startswith("payload.json."))
            self.assertIn(f".{os.getpid()}.", name)
            self.assertTrue(name.endswith(".tmp"))

    def test_failed_write_leaves_original_intact_and_no_tmp_garbage(self) -> None:
        target = self.root / "payload.json"
        target.write_text('{"generation": 1}', encoding="utf-8")

        with mock.patch.object(atomic_json.json, "dump", side_effect=OSError("磁盘满")):
            with self.assertRaises(OSError):
                atomic_write_json(target, {"generation": 2})

        self.assertEqual(target.read_text(encoding="utf-8"), '{"generation": 1}')
        self.assertEqual(list(self.root.glob("*.tmp")), [])


class StoreWritePathsAreDurableTest(unittest.TestCase):
    """每个 JSON store 的写路径都必须真的走到 fsync，而不是各自再造一遍。"""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def _count_fsync(self, action) -> int:
        calls: list[int] = []
        real_fsync = os.fsync

        def counting_fsync(descriptor: int) -> None:
            calls.append(descriptor)
            real_fsync(descriptor)

        with mock.patch.object(atomic_json.os, "fsync", counting_fsync):
            action()
        return len(calls)

    def test_daily_record_store_fsyncs(self) -> None:
        store = DailyRecordStore(self.root / "daily.json")
        calls = self._count_fsync(
            lambda: store.add_record("2026-08-21", "training", {"name": "a"})
        )
        self.assertGreaterEqual(calls, 1)

    def test_daily_record_store_bootstrap_fsyncs(self) -> None:
        calls = self._count_fsync(lambda: DailyRecordStore(self.root / "fresh.json"))
        self.assertGreaterEqual(calls, 1, "首次建库那一次写入同样会被断电截断")
        self.assertEqual(json.loads((self.root / "fresh.json").read_text(encoding="utf-8")), [])

    def test_training_plan_store_fsyncs(self) -> None:
        store = TrainingPlanStore(self.root / "plans.json")
        calls = self._count_fsync(
            lambda: store.add(
                date="2026-08-21",
                subject="胸部",
                title="t",
                content="c",
                source="agent_generated",
            )
        )
        self.assertGreaterEqual(calls, 1)

    def test_profile_store_fsyncs(self) -> None:
        store = UserProfileStore(self.root / "profile.json")
        calls = self._count_fsync(lambda: store.update_profile({"height_cm": 184}))
        self.assertGreaterEqual(calls, 1)

    def test_external_model_settings_store_fsyncs(self) -> None:
        store = ExternalModelSettingsStore(self.root / "external.json")
        calls = self._count_fsync(lambda: store.set_external_models_enabled(False))
        self.assertGreaterEqual(calls, 1)

    def test_info_store_fsyncs(self) -> None:
        store = InfoStore(self.root / "info.json")
        calls = self._count_fsync(
            lambda: store.add_entry(
                "测试记忆",
                {},
                datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        self.assertGreaterEqual(calls, 1)

    def test_hr_stream_store_fsyncs(self) -> None:
        store = HRStreamStore(self.root / "hr_streams")
        calls = self._count_fsync(
            lambda: store.save(
                "record-1",
                [{"timestamp": "2026-08-21T08:00:00+08:00", "heart_rate": 72}],
            )
        )
        self.assertGreaterEqual(calls, 1)

    def test_pending_workout_store_fsyncs(self) -> None:
        target = self.root / "pending_workout.json"
        with (
            mock.patch.object(workout_store, "_PERSIST_PATH", target),
            mock.patch.object(workout_store, "_activity_payload", return_value={"segments": []}),
            mock.patch.object(workout_store, "get_confirmation_context", return_value=None),
            mock.patch.object(workout_store, "_parsed_source", None),
            mock.patch.object(workout_store, "_last_edit_snapshot", None),
        ):
            calls = self._count_fsync(lambda: workout_store._persist_meta(object()))
        self.assertGreaterEqual(calls, 1)
        self.assertTrue(target.exists())

    def test_no_tmp_files_survive_normal_writes(self) -> None:
        DailyRecordStore(self.root / "daily.json").add_record("2026-08-21", "training", {})
        TrainingPlanStore(self.root / "plans.json").add(
            date="2026-08-21", subject="腿部", title="t", content="c", source="agent_generated"
        )
        UserProfileStore(self.root / "profile.json").update_profile({"goal": "减脂"})
        ExternalModelSettingsStore(self.root / "external.json").set_external_models_enabled(False)
        self.assertEqual(sorted(path.name for path in self.root.glob("*.tmp")), [])


# ══════════════════════════════════════════════════════════════════════════
# DATA-07：档案并发写入
# ══════════════════════════════════════════════════════════════════════════


class ProfileConcurrentWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.path = self.root / "user_profile.json"
        self.addCleanup(self._directory.cleanup)

    def _run(self, workers: list[threading.Thread]) -> None:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
        for worker in workers:
            self.assertFalse(worker.is_alive(), "并发写入卡死了——很可能是文件锁嵌套")

    def test_concurrent_writes_never_produce_spliced_garbage(self) -> None:
        """固定 tmp 名 + 无锁的原实现会 replace 出两份 JSON 拼在一起的文件。"""
        stores = [UserProfileStore(self.path) for _ in range(2)]
        errors: list[BaseException] = []

        def hammer(store: UserProfileStore, index: int) -> None:
            try:
                for round_index in range(8):
                    store.update_profile({"goal": f"g{index}-{round_index}"})
                    # 每次写入之后立刻读回：任何一次拼接垃圾都会在这里炸。
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    assert isinstance(loaded, dict), loaded
            except BaseException as exc:  # noqa: BLE001 - 汇总到主线程断言
                errors.append(exc)

        self._run([
            threading.Thread(target=hammer, args=(stores[index % 2], index))
            for index in range(6)
        ])
        self.assertEqual(errors, [])
        self.assertIsInstance(json.loads(self.path.read_text(encoding="utf-8")), dict)

    def test_merge_inside_the_lock_loses_no_update(self) -> None:
        """read-modify-write 必须整段在锁内：否则并发追加只剩最后一个。"""
        stores = [UserProfileStore(self.path) for _ in range(3)]
        expected = {f"器械{index}" for index in range(12)}
        errors: list[BaseException] = []

        def append(store: UserProfileStore, index: int) -> None:
            try:
                store.update_profile(
                    {"equipment": [f"器械{index}"]}, merge=union_equipment
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        self._run([
            threading.Thread(target=append, args=(stores[index % 3], index))
            for index in range(12)
        ])
        self.assertEqual(errors, [])
        equipment = set(stores[0].get_profile()["equipment"])
        self.assertTrue(
            expected <= equipment,
            f"丢了这些追加：{sorted(expected - equipment)}",
        )

    def test_reset_and_read_paths_do_not_deadlock(self) -> None:
        """JsonFileLock 不可重入；公开方法互相调用一旦嵌套就会卡死。"""
        store = UserProfileStore(self.path)
        store.update_profile({"height_cm": 184, "weekly_weight_kg": [80], "goal": "减脂"})
        result: list[object] = []

        def exercise() -> None:
            result.append(store.is_complete())          # 无参 → 内部走 get_profile
            result.append(store.missing_fields())
            result.append(store.reset())
            result.append(store.get_profile())

        self._run([threading.Thread(target=exercise)])
        self.assertEqual(len(result), 4)
        self.assertIs(result[0], False)  # birth_date / sex 仍缺，所以档案不完整
        self.assertEqual(result[3]["equipment"], UserProfileStore.DEFAULT_EQUIPMENT)


# ══════════════════════════════════════════════════════════════════════════
# 源码不变量：防止修复被后来的改动悄悄绕过
# ══════════════════════════════════════════════════════════════════════════


def _method(tree: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


class SourceInvariantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.storage_tree = ast.parse((PACKAGE_DIR / "storage.py").read_text(encoding="utf-8"))

    @staticmethod
    def _call_names(node: ast.AST) -> list[str]:
        return [
            ast.unparse(item.func)
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
        ]

    def test_public_profile_methods_hold_the_file_lock(self) -> None:
        for method_name in ("get_profile", "update_profile", "reset"):
            method = _method(self.storage_tree, "UserProfileStore", method_name)
            with_nodes = [item for item in ast.walk(method) if isinstance(item, ast.With)]
            context_calls = {
                ast.unparse(item.context_expr.func)
                for node in with_nodes
                for item in node.items
                if isinstance(item.context_expr, ast.Call)
            }
            with self.subTest(method=method_name):
                self.assertTrue(
                    any(
                        isinstance(item.context_expr, ast.Attribute)
                        and ast.unparse(item.context_expr) == "self._write_lock"
                        for node in with_nodes
                        for item in node.items
                    )
                )
                self.assertIn("JsonFileLock", context_calls)

    def test_private_profile_helpers_stay_lock_free(self) -> None:
        """JsonFileLock 每次进入都新开句柄、不可重入，私有方法里再套一层就死锁。"""
        for method_name in ("_read", "_write", "_normalized_profile"):
            with self.subTest(method=method_name):
                self.assertNotIn(
                    "JsonFileLock",
                    self._call_names(_method(self.storage_tree, "UserProfileStore", method_name)),
                )

    def test_profile_read_modify_write_happens_inside_the_lock(self) -> None:
        calls = self._call_names(_method(self.storage_tree, "UserProfileStore", "update_profile"))
        self.assertIn("self._normalized_profile", calls)
        self.assertNotIn("self.get_profile", calls)

    def test_confirm_endpoint_merges_inside_the_store(self) -> None:
        """确认端点不能再走 get_profile → 合并 → update_profile 这条丢更新的路。"""
        confirm = function_node(
            consumer_home("confirm_profile_update"), "confirm_profile_update"
        )
        update_calls = [
            node for node in ast.walk(confirm)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "deps.profile_store.update_profile"
        ]
        self.assertEqual(len(update_calls), 1)
        merge_keywords = [item for item in update_calls[0].keywords if item.arg == "merge"]
        self.assertEqual(len(merge_keywords), 1)
        self.assertEqual(ast.unparse(merge_keywords[0].value), "merge_profile_updates_with_existing")
        self.assertNotIn("deps.profile_store.get_profile", self._call_names(confirm))


if __name__ == "__main__":
    unittest.main()
