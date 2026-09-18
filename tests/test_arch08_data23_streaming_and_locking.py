"""ARCH-08 修法 (2)(3) 与 DATA-23 修法 (3) 的行为回归。

三件事各自都不看源码文本，只看行为：
* 导出/恢复按成员流式落盘，任何一刻都不把整份数据摊在内存里（DATA-23）；
* sqlite 快照发生在 JSON 文件锁**之外**，JSON 拷贝仍在锁内（ARCH-08 修法 2）；
* `JsonFileLock` 等不到锁时给的是带文件名的中文说明而不是裸
  `Permission denied`，且 `__enter__` 中途失败不泄漏句柄（ARCH-08 修法 3）。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fithealth_agent import backup_service
from fithealth_agent.backup_service import HEALTH_DB_NAME, LocalBackupService
from fithealth_agent.json_file_lock import JsonFileLock


#: 明显大于一个流式块的成员，用来证明"读了不止一次"。
BIG_MEMBER_BYTES = 3 * backup_service._STREAM_CHUNK_BYTES + 7


def _lock_is_free(path: Path, *, timeout: float = 0.3) -> bool:
    """在**另一个线程**里试着拿一次文件锁，拿到就说明没人持有。

    刻意不在当前线程试：POSIX 的 `flock` 在同进程不同 fd 之间也会阻塞，
    一旦被测代码回归成"锁内拍快照"，同线程的尝试会直接挂死而不是失败。
    """
    acquired: list[bool] = []

    def attempt() -> None:
        try:
            with JsonFileLock(path, timeout=timeout):
                acquired.append(True)
        except OSError:
            acquired.append(False)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout=timeout + 5)
    return acquired == [True]


class StreamingExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        (self.root / "daily_records.json").write_text("[]", encoding="utf-8")
        (self.root / "user_profile.json").write_text(
            json.dumps({"goal": "maintain"}), encoding="utf-8"
        )
        (self.root / "training_plans.json").write_text("[]", encoding="utf-8")
        (self.root / "info_store.json").write_text("[]", encoding="utf-8")
        imports = self.root / "health-imports"
        imports.mkdir()
        self.big_name = "health-imports/big.fit"
        self.big_payload = bytes(BIG_MEMBER_BYTES)
        (imports / "big.fit").write_bytes(self.big_payload)
        self.service = LocalBackupService(self.root)

    def test_collect_members_hands_back_disk_paths_not_payloads(self) -> None:
        """整条修法的支点：成员不再以 bytes 的形式攒在一个字典里。"""
        with tempfile.TemporaryDirectory() as workspace:
            sources = self.service._collect_members(Path(workspace))
            self.assertIn(self.big_name, sources)
            for name, source in sources.items():
                with self.subTest(member=name):
                    self.assertIsInstance(source, Path)
                    self.assertTrue(source.is_file(), source)
            # 大成员直接引用原文件，连拷贝都没有。
            self.assertEqual(
                sources[self.big_name], self.root / "health-imports" / "big.fit"
            )

    def test_export_reads_every_member_in_bounded_chunks(self) -> None:
        """3 MiB 的成员必须被切成多块读出来，否则峰值内存又回到数据量的量级。"""
        reads: list[int] = []
        real_stream = backup_service._stream_into

        class _Counting:
            def __init__(self, reader) -> None:
                self._reader = reader

            def read(self, size: int = -1) -> bytes:
                chunk = self._reader.read(size)
                reads.append(len(chunk))
                return chunk

        def counting_stream(reader, writer):
            return real_stream(_Counting(reader), writer)

        with patch.object(backup_service, "_stream_into", counting_stream):
            content = self.service.export_bytes()

        self.assertTrue(reads, "没有走流式路径")
        self.assertLessEqual(
            max(reads),
            backup_service._STREAM_CHUNK_BYTES,
            "有一次把整个成员读进了内存",
        )
        self.assertGreaterEqual(
            len([size for size in reads if size]), 4, "大成员没有被分块读取"
        )
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(archive.read(self.big_name), self.big_payload)

    def test_restore_extracts_members_to_disk_before_applying_them(self) -> None:
        content = self.service.export_bytes()
        seen: list[tuple[bool, bool]] = []
        real_apply = LocalBackupService._apply

        def observing_apply(self_, files, *, version):
            member = files.path(self.big_name)
            # 成员已经落在磁盘上、且映射本身不持有 bytes。
            seen.append((member.is_file(), member.stat().st_size == BIG_MEMBER_BYTES))
            return real_apply(self_, files, version=version)

        (self.root / "health-imports" / "big.fit").unlink()
        with patch.object(LocalBackupService, "_apply", observing_apply):
            self.service.restore(content)

        self.assertEqual(seen, [(True, True)])
        self.assertEqual(
            (self.root / "health-imports" / "big.fit").read_bytes(), self.big_payload
        )

    def test_validate_returns_a_lazy_mapping_that_still_reads_like_bytes(self) -> None:
        content = self.service.export_bytes()
        files = self.service.validate(content)
        self.assertIn(self.big_name, files)
        self.assertIn("user_profile.json", sorted(files))
        self.assertEqual(files[self.big_name], self.big_payload)
        self.assertEqual(
            json.loads(files["user_profile.json"].decode("utf-8")), {"goal": "maintain"}
        )

    def test_extracted_workspace_is_cleaned_up_after_release(self) -> None:
        files = self.service.validate(self.service.export_bytes())
        workspace = files.path(self.big_name).parent
        self.assertTrue(workspace.is_dir())
        files.release()
        self.assertFalse(workspace.exists(), "解压出来的临时目录没有被清理")


class SnapshotOutsideJsonLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        for name, empty in (
            ("daily_records.json", "[]"),
            ("user_profile.json", "{}"),
            ("training_plans.json", "[]"),
            ("info_store.json", "[]"),
        ):
            (self.root / name).write_text(empty, encoding="utf-8")
        connection = sqlite3.connect(self.root / HEALTH_DB_NAME)
        try:
            with connection:
                connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO sample (id) VALUES (1)")
        finally:
            # 必须显式 close，否则 Windows 上 TemporaryDirectory 清理会失败。
            connection.close()
        self.service = LocalBackupService(self.root)

    def test_database_snapshot_runs_outside_the_json_lock_window(self) -> None:
        """ARCH-08 修法 2：快照 + 压缩不该把 7 把 JSON 锁按住那么久。"""
        observed: list[bool] = []
        real_snapshot = LocalBackupService._snapshot_database

        def observing(database, destination):
            observed.append(_lock_is_free(self.root / "daily_records.json"))
            return real_snapshot(database, destination)

        with patch.object(
            LocalBackupService, "_snapshot_database", staticmethod(observing)
        ):
            content = self.service.export_bytes()

        self.assertEqual(observed, [True], "sqlite 快照仍在 JSON 锁窗口内")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn(HEALTH_DB_NAME, archive.namelist())

    def test_json_members_are_still_copied_inside_the_json_lock(self) -> None:
        """锁窗口只是被收窄，不是被删掉——JSON 拷贝期间并发写必须等。"""
        observed: list[bool] = []
        real_copyfile = shutil.copyfile

        def observing(source, destination, *args, **kwargs):
            if Path(destination).name == "daily_records.json":
                observed.append(_lock_is_free(self.root / "daily_records.json"))
            return real_copyfile(source, destination, *args, **kwargs)

        with patch.object(backup_service.shutil, "copyfile", observing):
            self.service.export_bytes()

        self.assertEqual(observed, [False], "导出读 JSON 时没有持锁")


class JsonFileLockTimeoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.target = self.root / "daily_records.json"

    @unittest.skipUnless(os.name == "nt", "只有 Windows 分支有等待上限")
    def test_timeout_explains_which_file_is_busy_in_chinese(self) -> None:
        with JsonFileLock(self.target):
            with self.assertRaises(OSError) as captured:
                with JsonFileLock(self.target, timeout=0.2):
                    pass
        message = str(captured.exception)
        self.assertIsInstance(captured.exception, TimeoutError)  # 仍是 OSError 子类
        self.assertIn("daily_records.json", message)
        self.assertIn("0.2 秒", message)
        self.assertIn("请稍后重试", message)
        self.assertNotIn("Permission denied", message)

    @unittest.skipUnless(os.name == "nt", "只有 Windows 分支有等待上限")
    def test_lock_is_granted_once_the_holder_releases_it(self) -> None:
        """重试循环真的在重试，而不是"占用即失败"。"""
        holder = JsonFileLock(self.target)
        holder.__enter__()
        released = threading.Event()

        def release_later() -> None:
            released.wait(5)
            holder.__exit__(None, None, None)

        worker = threading.Thread(target=release_later, daemon=True)
        worker.start()
        released.set()
        with JsonFileLock(self.target, timeout=5):
            pass
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_enter_failure_does_not_leak_the_file_handle(self) -> None:
        lock = JsonFileLock(self.target)
        if os.name == "nt":
            patcher = patch.object(
                JsonFileLock, "_acquire_windows", side_effect=OSError("boom")
            )
        else:
            import fcntl

            patcher = patch.object(fcntl, "flock", side_effect=OSError("boom"))
        with patcher:
            with self.assertRaises(OSError):
                lock.__enter__()
        self.assertIsNone(lock.handle)
        # Windows 上还开着的句柄会让 unlink 抛 PermissionError。
        lock.path.unlink()


if __name__ == "__main__":
    unittest.main()
