"""DATA-09 / DATA-11 / DATA-12 的回归测试。

DATA-09：`health_store.py` 在 WAL 失败时静默切到 `journal_mode = MEMORY`，该模式
下回滚日志只在内存，崩溃即结构性损坏；数据目录不可用时切到系统临时目录后**仍
允许写入**，Windows 清理 %TEMP% 时新数据直接蒸发。

DATA-11：备份漏掉 `data/health-imports/`（唯一可重新解析的数据源）、
`pending_workout.json`、`external_model_settings.json`，且 manifest 没有每文件
校验和——"结构合法但内容被替换"检测不到。

DATA-12：`restore()` 换 JSON 时不持有 `JsonFileLock`，并发写请求会覆盖恢复结果；
删 `health.db` 的 `-wal`/`-shm` 时不保证没有在飞连接。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fithealth_agent import health_store as health_store_module
from fithealth_agent.backup_service import (
    BACKUP_VERSION,
    HEALTH_DB_NAME,
    JSON_FILES,
    LocalBackupService,
)
from fithealth_agent.health_store import (
    UNSAFE_JOURNAL_MODES,
    HealthStore,
    HealthStoreDegradedError,
)
from fithealth_agent.json_file_lock import JsonFileLock
from fithealth_agent.maintenance import MaintenanceBusyError, MaintenanceGate
from fithealth_agent.storage import DailyRecordStore
from tests.module_map import consumer_home
from tests.source_tools import module_tree


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"

RAW_IMPORT_NAME = "6debb86f49d8180d-2026-08-15.zip"
RAW_IMPORT_BYTES = b"PK\x03\x04 pretend garmin export"
HR_STREAM_NAME = "16e93f38.json"


def load_fresh_module(name: str, path: Path):
    """重新执行一份模块，用来让模块级路径常量在新的数据目录下重新求值。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def build_data_dir(root: Path) -> None:
    """搭一个内容齐全的数据目录：四个必备 JSON、两个可选 JSON、两个目录、一个库。"""
    (root / "daily_records.json").write_text(
        json.dumps([{"id": "r1", "date": "2026-08-15", "revision": 1, "record": {}}]),
        encoding="utf-8",
    )
    (root / "user_profile.json").write_text(json.dumps({"goal": "maintain"}), encoding="utf-8")
    (root / "training_plans.json").write_text("[]", encoding="utf-8")
    (root / "info_store.json").write_text("[]", encoding="utf-8")
    (root / "pending_workout.json").write_text(json.dumps({"version": 2}), encoding="utf-8")
    (root / "external_model_settings.json").write_text(
        json.dumps({"external_models_enabled": False}), encoding="utf-8"
    )
    raw_dir = root / "health-imports"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / RAW_IMPORT_NAME).write_bytes(RAW_IMPORT_BYTES)
    streams = root / "hr_streams"
    streams.mkdir(exist_ok=True)
    (streams / HR_STREAM_NAME).write_text(
        json.dumps({"record_id": "16e93f38", "samples": [{"heart_rate": 94}]}), encoding="utf-8"
    )
    store = HealthStore(db_path=root / HEALTH_DB_NAME, raw_dir=raw_dir)
    # 必须显式 close：`with sqlite3.connect(...)` 只提交事务、**不关连接**，
    # 留着的句柄会让 Windows 上的 TemporaryDirectory 清理直接 PermissionError。
    connection = sqlite3.connect(store.db_path)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO health_imports
                    (id, sha256, filename, kind, status, date_hint, warnings_json, raw_path, created_at)
                VALUES ('i1', 'deadbeef', '2026-08-15.zip', 'wellness', 'stored', '2026-08-15',
                        '[]', ?, '2026-08-15T00:00:00+00:00')
                """,
                (RAW_IMPORT_NAME,),
            )
    finally:
        connection.close()


def rebuild_archive(content: bytes, mutate) -> bytes:
    """解开一份备份、让 `mutate` 改动 {成员名: 字节}，再打回去。

    用来构造"结构合法但内容被替换"的攻击面——manifest 原样保留。
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    mutate(members)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return output.getvalue()


# ══════════════════════════════════════════════════════════════════════════
# DATA-09：journal 模式
# ══════════════════════════════════════════════════════════════════════════


class FakeCursor:
    def __init__(self, row: tuple) -> None:
        self._row = row

    def fetchone(self) -> tuple:
        return self._row


class FakeConnection:
    """只回答 PRAGMA journal_mode 的假连接。

    真实 SQLite 很难被逼到"WAL 被拒绝"的状态（那要网络盘或特殊挂载），而这条
    分支恰恰是本条缺陷的全部所在，所以这里直接构造它。
    """

    def __init__(self, *, refuse: set[str], current: str) -> None:
        self.refuse = {mode.lower() for mode in refuse}
        self.current = current
        self.statements: list[str] = []

    def execute(self, statement: str) -> FakeCursor:
        self.statements.append(statement)
        if "=" in statement:
            requested = statement.split("=", 1)[1].strip().lower()
            if requested not in self.refuse:
                self.current = requested
        return FakeCursor((self.current,))


class JournalModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def test_real_database_uses_wal(self) -> None:
        store = HealthStore(db_path=self.root / "h.db", raw_dir=self.root / "raw")
        self.assertEqual(store.journal_mode, "wal")
        self.assertTrue(store.storage_status()["journal_mode_safe"])

    def test_wal_refused_falls_back_to_delete_never_memory(self) -> None:
        store = HealthStore(db_path=self.root / "h.db", raw_dir=self.root / "raw")
        connection = FakeConnection(refuse={"wal"}, current="delete")
        self.assertEqual(store._apply_journal_mode(connection), "delete")
        requested = [item.split("=", 1)[1].strip().lower() for item in connection.statements if "=" in item]
        self.assertEqual(requested, ["wal", "delete"])
        self.assertNotIn("memory", requested, "绝不允许主动切到 MEMORY")

    def test_refusal_is_detected_from_the_return_value_not_an_exception(self) -> None:
        """PRAGMA 被拒绝时不抛异常、只返回当前模式——原实现的 try/except 抓不到。"""
        store = HealthStore(db_path=self.root / "h.db", raw_dir=self.root / "raw")
        connection = FakeConnection(refuse={"wal", "delete"}, current="memory")
        applied = store._apply_journal_mode(connection)
        self.assertEqual(applied, "memory")
        self.assertIn(applied, UNSAFE_JOURNAL_MODES)

    def test_unsafe_mode_is_surfaced_in_storage_status(self) -> None:
        store = HealthStore(db_path=self.root / "h.db", raw_dir=self.root / "raw")
        store._journal_mode = "memory"
        status = store.storage_status()
        self.assertFalse(status["journal_mode_safe"])
        self.assertEqual(status["journal_mode"], "memory")
        # 数据此刻还是好的，所以整体仍算可用——只是必须让用户看见。
        self.assertTrue(status["available"])


class ReadOnlyDegradationTest(unittest.TestCase):
    """DATA-09：退到临时目录之后必须拒绝写入，而不是假装导入成功。"""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self._previous = os.environ.get("FITHEALTH_DATA_DIR")
        os.environ["FITHEALTH_DATA_DIR"] = str(self.root)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._previous is None:
            os.environ.pop("FITHEALTH_DATA_DIR", None)
        else:
            os.environ["FITHEALTH_DATA_DIR"] = self._previous

    def _degraded_store(self) -> HealthStore:
        """走真实的 `_open` 回落分支：第一次 _initialize 报磁盘 I/O 错误。"""
        real_initialize = HealthStore._initialize
        calls = {"count": 0}

        def flaky(self_: HealthStore) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise sqlite3.OperationalError("disk I/O error")
            real_initialize(self_)

        with mock.patch.object(HealthStore, "_initialize", flaky):
            store = HealthStore()
        self.assertEqual(calls["count"], 2, "应当在临时目录上重试一次初始化")
        return store

    def test_fallback_is_read_only(self) -> None:
        store = self._degraded_store()
        self.assertTrue(store.using_temporary_storage)
        self.assertFalse(store.writable)
        status = store.storage_status()
        self.assertFalse(status["available"])
        self.assertIn("只读", status["degraded_reason"])

        # 读接口照常可用——降级的目的是别让整个应用挂掉。
        self.assertEqual(store.list_imports(), [])
        self.assertIsNone(store.find_import_by_hash("deadbeef"))

        # 写接口一律显式失败。
        for label, action in (
            ("save_import", lambda: store.save_import({"id": "x"})),
            ("delete_import", lambda: store.delete_import("x")),
            ("clear", store.clear),
        ):
            with self.subTest(method=label):
                with self.assertRaises(HealthStoreDegradedError):
                    action()

    def test_revalidate_clears_the_degradation(self) -> None:
        store = self._degraded_store()
        self.assertTrue(store.revalidate())
        self.assertTrue(store.writable)
        self.assertFalse(store.using_temporary_storage)
        self.assertEqual(store.db_path, self.root / "health.db")
        # 解除降级之后写入必须真的能落地。
        self.assertEqual(store.clear(), 0)

    def test_revalidate_keeps_degradation_when_still_broken(self) -> None:
        store = self._degraded_store()
        with mock.patch.object(
            HealthStore, "_initialize", side_effect=sqlite3.OperationalError("disk I/O error")
        ):
            self.assertFalse(store.revalidate())
        self.assertFalse(store.writable)
        self.assertTrue(store.using_temporary_storage)


# ══════════════════════════════════════════════════════════════════════════
# DATA-11：备份内容与校验和
# ══════════════════════════════════════════════════════════════════════════


class BackupContentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        build_data_dir(self.root)
        self.service = LocalBackupService(self.root)
        self.backup = self.service.export_bytes()

    def _manifest(self, content: bytes | None = None) -> dict:
        with zipfile.ZipFile(io.BytesIO(content or self.backup)) as archive:
            return json.loads(archive.read("manifest.json"))

    def test_backup_contains_the_previously_missing_members(self) -> None:
        files = self.service.validate(self.backup)
        # 原实现只装这 4 个 JSON + health.db，恢复后 raw_path 全部悬空。
        self.assertIn(f"health-imports/{RAW_IMPORT_NAME}", files)
        self.assertIn(f"hr_streams/{HR_STREAM_NAME}", files)
        self.assertIn("pending_workout.json", files)
        self.assertIn("external_model_settings.json", files)
        self.assertIn(HEALTH_DB_NAME, files)
        self.assertEqual(files[f"health-imports/{RAW_IMPORT_NAME}"], RAW_IMPORT_BYTES)

    def test_manifest_records_sha256_and_size_for_every_member(self) -> None:
        manifest = self._manifest()
        self.assertEqual(manifest["version"], BACKUP_VERSION)
        members = manifest["members"]
        with zipfile.ZipFile(io.BytesIO(self.backup)) as archive:
            names = [name for name in archive.namelist() if name != "manifest.json"]
            self.assertEqual(sorted(members), sorted(names))
            for name in names:
                payload = archive.read(name)
                self.assertEqual(members[name]["bytes"], len(payload))
                self.assertEqual(
                    members[name]["sha256"], hashlib.sha256(payload).hexdigest()
                )

    def test_replaced_content_is_rejected_even_though_the_zip_is_valid(self) -> None:
        """本条缺陷的核心：结构合法但内容被换掉，原实现完全检测不到。"""
        tampered = rebuild_archive(
            self.backup,
            lambda members: members.__setitem__("user_profile.json", b'{"goal":"tampered"}'),
        )
        with self.assertRaisesRegex(ValueError, "user_profile.json"):
            self.service.validate(tampered)

    def test_replaced_raw_import_is_rejected(self) -> None:
        name = f"health-imports/{RAW_IMPORT_NAME}"
        tampered = rebuild_archive(
            self.backup, lambda members: members.__setitem__(name, b"swapped payload")
        )
        with self.assertRaisesRegex(ValueError, "health-imports"):
            self.service.validate(tampered)

    def test_dropped_member_is_rejected(self) -> None:
        name = f"hr_streams/{HR_STREAM_NAME}"
        tampered = rebuild_archive(self.backup, lambda members: members.pop(name))
        with self.assertRaisesRegex(ValueError, "但备份里没有这个文件"):
            self.service.validate(tampered)

    def test_unregistered_extra_member_is_rejected(self) -> None:
        tampered = rebuild_archive(
            self.backup,
            lambda members: members.__setitem__("hr_streams/sneaky.json", b"{}"),
        )
        with self.assertRaisesRegex(ValueError, "未登记"):
            self.service.validate(tampered)

    def test_path_traversal_member_names_are_rejected(self) -> None:
        for evil in (
            "../evil.json",
            "health-imports/../../evil.json",
            "health-imports/nested/evil.json",
            "/etc/passwd",
            "secrets/evil.json",
        ):
            with self.subTest(name=evil):
                tampered = rebuild_archive(
                    self.backup, lambda members, n=evil: members.__setitem__(n, b"{}")
                )
                with self.assertRaisesRegex(ValueError, "不支持的内容"):
                    self.service.validate(tampered)


class RestoreContentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        build_data_dir(self.root)
        self.service = LocalBackupService(self.root)
        self.backup = self.service.export_bytes()

    def test_restore_brings_back_raw_imports_and_hr_streams(self) -> None:
        raw = self.root / "health-imports" / RAW_IMPORT_NAME
        stream = self.root / "hr_streams" / HR_STREAM_NAME
        raw.unlink()
        stream.unlink()

        result = self.service.restore(self.backup)

        self.assertEqual(raw.read_bytes(), RAW_IMPORT_BYTES)
        self.assertTrue(stream.exists())
        self.assertEqual(
            result["restored_directories"],
            ["health-imports", "hr_streams", "workout-quarantine"],
        )
        self.assertEqual(result["restored_directory_files"], 2)

    def test_restored_database_raw_path_still_resolves_to_a_real_file(self) -> None:
        """DATA-11 的验收点：恢复后 raw_path 不再悬空。

        库里存的已经是裸文件名（DATA-02），`resolve_raw_file` 也只信任文件名，
        所以不需要改写 DB——这条测试就是用来钉住"不需要改写"这个前提的。
        """
        (self.root / "health-imports" / RAW_IMPORT_NAME).unlink()
        self.service.restore(self.backup)

        store = HealthStore(
            db_path=self.root / HEALTH_DB_NAME, raw_dir=self.root / "health-imports"
        )
        row = store.list_imports()[0]
        resolved = store.resolve_raw_file(row["raw_path"])
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.exists(), "恢复之后 raw_path 依然悬空")

    def test_restore_replaces_the_directory_wholesale(self) -> None:
        """恢复 = 回到那份备份的状态，所以备份里没有的原始文件不该留下。

        库是整份换掉的，留着不在库里的 raw 文件只会变成永久孤儿。
        """
        orphan = self.root / "health-imports" / "not-in-backup.zip"
        orphan.write_bytes(b"orphan")
        self.service.restore(self.backup)
        self.assertFalse(orphan.exists())
        self.assertTrue((self.root / "health-imports" / RAW_IMPORT_NAME).exists())

    def test_v2_restore_removes_optional_json_absent_from_the_backup(self) -> None:
        (self.root / "pending_workout.json").unlink()
        backup = self.service.export_bytes()
        (self.root / "pending_workout.json").write_text('{"version": 2}', encoding="utf-8")

        result = self.service.restore(backup)

        self.assertFalse((self.root / "pending_workout.json").exists())
        self.assertEqual(result["removed_files"], ["pending_workout.json"])

    def test_v1_backup_still_restores_and_leaves_optional_files_alone(self) -> None:
        """老备份不能作废，而且它没带的可选文件不该被当成"应当删除"。"""
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": "fithealth-agent-backup",
                        "version": 1,
                        "created_at": "2026-08-19T00:00:00+00:00",
                        "files": sorted(JSON_FILES),
                    }
                ),
            )
            archive.writestr("daily_records.json", b"[]")
            archive.writestr("user_profile.json", json.dumps({"goal": "v1"}).encode())
            archive.writestr("training_plans.json", b"[]")
            archive.writestr("info_store.json", b"[]")

        result = self.service.restore(output.getvalue())

        self.assertEqual(result["restored_files"], 4)
        self.assertEqual(result["removed_files"], [])
        self.assertTrue((self.root / "pending_workout.json").exists())
        profile = json.loads((self.root / "user_profile.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["goal"], "v1")

    def test_failed_directory_swap_rolls_everything_back(self) -> None:
        original_profile = (self.root / "user_profile.json").read_bytes()
        original_raw = sorted(path.name for path in (self.root / "health-imports").iterdir())
        (self.root / "user_profile.json").write_bytes(b'{"goal":"changed"}')

        real_replace = os.replace

        def fail_on_directory(source, target):
            # 只打掉"暂存目录换进去"这一步；回滚用的 rename 必须放过，
            # 否则测的就变成"回滚也坏了"而不是"回滚能兜住"。
            if Path(source).parent.name == "staged" and Path(target).name == "hr_streams":
                raise OSError("simulated directory swap failure")
            return real_replace(source, target)

        with mock.patch("fithealth_agent.backup_service.os.replace", fail_on_directory):
            with self.assertRaisesRegex(OSError, "已回滚"):
                self.service.restore(self.backup)

        self.assertEqual((self.root / "user_profile.json").read_bytes(), b'{"goal":"changed"}')
        self.assertNotEqual((self.root / "user_profile.json").read_bytes(), original_profile)
        self.assertEqual(
            sorted(path.name for path in (self.root / "health-imports").iterdir()), original_raw
        )
        self.assertTrue((self.root / "hr_streams" / HR_STREAM_NAME).exists())
        self.assertFalse(list(self.root.glob(".restore-*")))


# ══════════════════════════════════════════════════════════════════════════
# DATA-12：恢复过程的并发隔离
# ══════════════════════════════════════════════════════════════════════════


class SpyDatabase:
    """记录 exclusive_access / reopen 的调用时序。"""

    def __init__(self) -> None:
        self.events: list[str] = []

    def exclusive_access(self):
        events = self.events

        class _Scope:
            def __enter__(self_inner):
                events.append("enter")
                return self_inner

            def __exit__(self_inner, *exc):
                events.append("exit")
                return False

        return _Scope()

    def reopen(self) -> None:
        self.events.append("reopen")


class RestoreIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        build_data_dir(self.root)
        self.backup = LocalBackupService(self.root).export_bytes()

    def test_database_is_reopened_inside_the_exclusive_window(self) -> None:
        """换库与 reopen 之间不能有别人插进来，否则会读到没校验过的 schema。"""
        spy = SpyDatabase()
        LocalBackupService(self.root, database=spy).restore(self.backup)
        self.assertEqual(spy.events, ["enter", "exit", "enter", "reopen", "exit"])

    def test_restore_creates_a_pre_restore_recovery_point(self) -> None:
        result = LocalBackupService(self.root).restore(self.backup)
        point = result["recovery_point"]
        self.assertRegex(point["name"], r"^pre-restore-\d{14}\.zip$")
        self.assertTrue((self.root / "recovery-points" / point["name"]).exists())

    def test_restore_holds_the_json_file_lock(self) -> None:
        """原实现换 JSON 时不持锁，并发写请求会把恢复结果整份覆盖掉。"""
        service = LocalBackupService(self.root)
        started = threading.Event()
        blocked_for: list[float] = []
        real_apply = LocalBackupService._apply

        def slow_apply(self_, files, *, version):
            started.set()
            time.sleep(0.4)
            return real_apply(self_, files, version=version)

        def competitor() -> None:
            started.wait(5)
            begin = time.monotonic()
            with JsonFileLock(self.root / "daily_records.json"):
                blocked_for.append(time.monotonic() - begin)

        worker = threading.Thread(target=competitor)
        with mock.patch.object(LocalBackupService, "_apply", slow_apply):
            worker.start()
            service.restore(self.backup)
        worker.join(timeout=10)

        self.assertFalse(worker.is_alive())
        self.assertTrue(blocked_for, "竞争线程没能拿到锁")
        self.assertGreater(
            blocked_for[0], 0.2, "并发写入没有被挡住——恢复期间没有持有 JsonFileLock"
        )

    def test_concurrent_writer_sees_the_restored_content(self) -> None:
        """排在恢复之后的写入必须基于**恢复后**的内容做 read-modify-write。"""
        service = LocalBackupService(self.root)
        store = DailyRecordStore(self.root / "daily_records.json")
        # 备份里是 1 条记录；先把盘上改成 3 条，恢复应当把它退回 1 条。
        store.add_record("2026-08-20", "training", {"name": "b"})
        store.add_record("2026-08-20", "training", {"name": "c"})
        self.assertEqual(len(store.list_records()), 3)

        started = threading.Event()
        real_apply = LocalBackupService._apply

        def slow_apply(self_, files, *, version):
            started.set()
            time.sleep(0.4)
            return real_apply(self_, files, version=version)

        def competitor() -> None:
            started.wait(5)
            store.add_record("2026-08-21", "training", {"name": "after-restore"})

        worker = threading.Thread(target=competitor)
        with mock.patch.object(LocalBackupService, "_apply", slow_apply):
            worker.start()
            service.restore(self.backup)
        worker.join(timeout=10)

        records = store.list_records()
        names = {item["record"].get("name") for item in records}
        self.assertEqual(len(records), 2, f"恢复结果被并发写入覆盖了：{records}")
        self.assertIn("after-restore", names)
        self.assertNotIn("b", names)
        self.assertNotIn("c", names)

    def test_gate_is_raised_before_touching_anything(self) -> None:
        gate = MaintenanceGate()
        seen: list[tuple[str, bool]] = []
        real_apply = LocalBackupService._apply
        real_write_recovery_point = LocalBackupService.write_recovery_point

        def observing_recovery_point(self_, *, prefix="pre-reset"):
            seen.append(("recovery_point", gate.active))
            return real_write_recovery_point(self_, prefix=prefix)

        def observing_apply(self_, files, *, version):
            seen.append(("apply", gate.active))
            return real_apply(self_, files, version=version)

        def observing_callback():
            seen.append(("callback", gate.active))
            return {"available": True}

        with (
            mock.patch.object(LocalBackupService, "write_recovery_point", observing_recovery_point),
            mock.patch.object(LocalBackupService, "_apply", observing_apply),
        ):
            LocalBackupService(
                self.root, gate=gate, on_restored=[observing_callback]
            ).restore(self.backup)

        self.assertEqual(
            seen,
            [("recovery_point", True), ("apply", True), ("callback", True)],
        )
        self.assertFalse(gate.active, "维护开关没有落下")


class MaintenanceGateTest(unittest.TestCase):
    def test_status_reports_reason_and_inflight(self) -> None:
        gate = MaintenanceGate()
        self.assertEqual(gate.status["active"], False)
        with gate.exclusive("恢复备份"):
            status = gate.status
            self.assertTrue(status["active"])
            self.assertEqual(status["reason"], "恢复备份")
            self.assertIsNotNone(status["since"])
        self.assertFalse(gate.status["active"])
        self.assertIsNone(gate.status["reason"])

    def test_second_maintenance_is_refused(self) -> None:
        gate = MaintenanceGate()
        with gate.exclusive("恢复备份"):
            with self.assertRaisesRegex(MaintenanceBusyError, "恢复备份"):
                with gate.exclusive("再来一次"):
                    pass
        # 被拒绝的那次不能把第一次的状态清掉。
        self.assertFalse(gate.status["active"])

    def test_drain_times_out_instead_of_hanging(self) -> None:
        """在飞请求卡住时宁可干净地失败，也不要与它循环等待。"""
        gate = MaintenanceGate()
        release = threading.Event()
        holding = threading.Event()

        def in_flight() -> None:
            with gate.track_request():
                holding.set()
                release.wait(10)

        worker = threading.Thread(target=in_flight)
        worker.start()
        try:
            holding.wait(5)
            with self.assertRaisesRegex(MaintenanceBusyError, "个请求在处理中"):
                with gate.exclusive("恢复备份", timeout=0.3):
                    self.fail("排空失败时不应进入维护体内")
        finally:
            release.set()
            worker.join(timeout=10)
        # 失败之后开关必须落下，否则整个服务永久 503。
        self.assertFalse(gate.active)
        self.assertEqual(gate.inflight, 0)

    def test_drain_succeeds_once_inflight_requests_finish(self) -> None:
        gate = MaintenanceGate()
        holding = threading.Event()

        def in_flight() -> None:
            with gate.track_request():
                holding.set()
                time.sleep(0.3)

        worker = threading.Thread(target=in_flight)
        worker.start()
        holding.wait(5)
        with gate.exclusive("恢复备份", timeout=5):
            self.assertEqual(gate.inflight, 0)
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())

    def test_tracking_is_released_even_when_the_request_raises(self) -> None:
        gate = MaintenanceGate()
        with self.assertRaises(RuntimeError):
            with gate.track_request():
                raise RuntimeError("boom")
        self.assertEqual(gate.inflight, 0)


class MaintenanceMiddlewareTest(unittest.TestCase):
    """维护期间必须真的挡住请求——写了开关但没人读，就是又一个 BUG-01。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._previous = os.environ.get("FITHEALTH_DATA_DIR")
        os.environ["FITHEALTH_DATA_DIR"] = cls._directory.name
        try:
            import importlib

            from fastapi.testclient import TestClient

            cls.main = importlib.import_module("main")
            cls.client = TestClient(cls.main.app)
        finally:
            if cls._previous is None:
                os.environ.pop("FITHEALTH_DATA_DIR", None)
            else:
                os.environ["FITHEALTH_DATA_DIR"] = cls._previous

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def test_requests_pass_when_no_maintenance_is_running(self) -> None:
        self.assertEqual(self.client.get("/profile/status").status_code, 200)

    def test_other_requests_get_503_during_maintenance(self) -> None:
        with self.main.MAINTENANCE.exclusive("恢复备份", timeout=5):
            response = self.client.get("/profile/status")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertIn("维护", body["error"])
        self.assertEqual(body["maintenance"]["reason"], "恢复备份")

    def test_diagnostics_stay_reachable_during_maintenance(self) -> None:
        with self.main.MAINTENANCE.exclusive("恢复备份", timeout=5):
            response = self.client.get("/health/storage-status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["maintenance"]["active"])

    def test_restore_endpoint_is_not_counted_as_inflight(self) -> None:
        """否则排空会等它自己，恢复请求直接死等。"""
        from fithealth_agent.runtime.middleware import MAINTENANCE_UNTRACKED_PATHS

        self.assertIn("/data/backup/import", MAINTENANCE_UNTRACKED_PATHS)


class PendingWorkoutReloadTest(unittest.TestCase):
    """DATA-11：备份带上 pending_workout.json 之后，恢复必须按盘重载而非清空。"""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        self._previous = os.environ.get("FITHEALTH_DATA_DIR")
        os.environ["FITHEALTH_DATA_DIR"] = str(self.root)
        self.addCleanup(self._restore_env)
        # _PERSIST_PATH 在模块导入时就定下来了，所以必须在设好数据目录之后
        # 重新加载一份，否则会写到真实的 data/ 里去。
        self.store = load_fresh_module(
            "fithealth_agent.workout_store_reload_probe", PACKAGE_DIR / "workout_store.py"
        )
        self.persist_path = self.root / "pending_workout.json"

    def _restore_env(self) -> None:
        sys.modules.pop("fithealth_agent.workout_store_reload_probe", None)
        if self._previous is None:
            os.environ.pop("FITHEALTH_DATA_DIR", None)
        else:
            os.environ["FITHEALTH_DATA_DIR"] = self._previous

    def _activity(self):
        from fithealth_agent.fit_parser import ActivitySegment, ParsedActivity, SessionSummary

        start = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
        return ParsedActivity(
            SessionSummary("跳绳", "jump_rope", start_time=start),
            [ActivitySegment(1, "lap", start, start + timedelta(seconds=60), 60)],
            [],
            source_file="a.fit",
            source_sha256="a" * 64,
        )

    def test_reload_from_disk_picks_up_the_restored_file(self) -> None:
        self.store.set_current(self._activity())
        persisted = self.persist_path.read_bytes()

        # 模拟恢复备份：内存里那份属于恢复前的世界，磁盘上换成备份里的那份。
        self.store.clear_current()
        self.assertIsNone(self.store.get_current())
        self.persist_path.write_bytes(persisted)

        self.store.reload_from_disk()

        self.assertIsNotNone(self.store.get_current(), "恢复回来的待确认训练没有被载入")
        self.assertTrue(self.store.was_restored_from_disk())
        self.assertTrue(self.persist_path.exists(), "重载不该删掉文件")
        self.assertEqual(self.store.get_current().session.sport, "跳绳")

    def test_reload_from_disk_clears_state_when_the_backup_had_none(self) -> None:
        self.store.set_current(self._activity())
        self.persist_path.unlink()

        self.store.reload_from_disk()

        self.assertIsNone(self.store.get_current())
        self.assertFalse(self.store.was_restored_from_disk())


# ══════════════════════════════════════════════════════════════════════════
# 源码不变量
# ══════════════════════════════════════════════════════════════════════════


class StructuralInvariantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.health_source = (PACKAGE_DIR / "health_store.py").read_text(encoding="utf-8")
        cls.backup_source = (PACKAGE_DIR / "backup_service.py").read_text(encoding="utf-8")

    @staticmethod
    def _call_names(node: ast.AST) -> list[str]:
        return [ast.unparse(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)]

    def test_memory_journal_mode_is_gone_for_good(self) -> None:
        """DATA-09 的根因就这一行；只在注释里出现是可以的。"""
        self.assertEqual(health_store_module.SAFE_JOURNAL_MODES, ("WAL", "DELETE"))
        self.assertIn("memory", health_store_module.UNSAFE_JOURNAL_MODES)

    def test_middleware_is_registered_explicitly_without_import_side_effects(self) -> None:
        main_tree = module_tree(REPO_ROOT / "main.py")
        middleware_tree = module_tree(consumer_home("degraded_error_handlers"))

        main_calls = self._call_names(main_tree)
        self.assertIn("register_middleware", main_calls)

        register = next(
            node
            for node in middleware_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "register"
        )
        register_calls = self._call_names(register)
        self.assertIn("app.middleware", register_calls)
        self.assertEqual(register_calls.count("app.add_exception_handler"), 2)

    def test_health_write_paths_check_the_degraded_flag(self) -> None:
        tree = ast.parse(self.health_source)
        class_node = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HealthStore"
        )
        for name in ("save_import", "delete_import", "clear"):
            method = next(
                node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef) and node.name == name
            )
            with self.subTest(method=name):
                self.assertIn("self._require_writable", self._call_names(method))

    def test_backup_packs_the_previously_missing_members(self) -> None:
        from fithealth_agent import backup_service

        self.assertEqual(
            backup_service.BACKUP_DIRECTORIES,
            ("health-imports", "hr_streams", "workout-quarantine"),
        )
        self.assertEqual(
            backup_service.OPTIONAL_JSON_FILES,
            (
                "pending_workout.json",
                "external_model_settings.json",
                "muscle_soreness.json",
            ),
        )
        self.assertEqual(backup_service.BACKUP_VERSION, 2)
        self.assertIn(1, backup_service.SUPPORTED_VERSIONS)

    def test_restore_acquires_locks_before_touching_files(self) -> None:
        tree = ast.parse(self.backup_source)
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "LocalBackupService"
        )
        restore = next(
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == "restore"
        )
        calls = self._call_names(restore)
        for required in (
            "self._gate.exclusive",
            "self._locked_json_targets",
            "self._database.exclusive_access",
            "self._database.reopen",
        ):
            self.assertIn(required, calls)

    def test_restore_reloads_pending_workout_instead_of_clearing_it(self) -> None:
        """备份现在带 pending_workout.json，clear_current() 会把它删掉。"""
        tree = module_tree(consumer_home("import_backup"))
        endpoint = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "import_backup"
        )
        calls = self._call_names(endpoint)
        self.assertIn("workout_store.reload_from_disk", calls)
        self.assertNotIn("workout_store.clear_current", calls)


if __name__ == "__main__":
    unittest.main()
