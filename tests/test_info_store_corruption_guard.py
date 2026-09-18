"""DATA-08 回归测试：记忆库不可解析时必须只读降级，绝不静默清空。

原缺陷：`InfoStore._read_all` 的 `except Exception: data = []` 把任何读取失败
都伪装成"空记忆库"，用户随手触发一次保存就会 `_write_all([新条目])`，把整个
记忆库永久覆盖。本文件钉住修复后的三条不变量：

1. 读不出来 → 隔离一份副本（copy，不是 move）并进入只读降级；
2. 降级期间任何写入抛 MemoryStoreDegradedError，原文件字节不变；
3. 单条坏记录不连坐：其余记忆正常可用，坏记录在写入时原样保留。
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.module_map import consumer_home
from tests.source_tools import module_source


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_store_module():
    path = REPO_ROOT / "fithealth_agent" / "info_store.py"
    spec = importlib.util.spec_from_file_location("info_store_corruption_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load info_store")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CorruptionGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_store_module()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "info_store.json"
        self.store = self.module.InfoStore(self.path)
        self.expires = datetime.now(timezone.utc) + timedelta(days=1)

    def _seed(self, summary: str = "记住我不做深蹲") -> dict:
        return self.store.add_entry(
            summary, {}, self.expires, memory_type="constraint", importance=5
        )

    def _corrupt(self, content: str) -> None:
        self.path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # 不变量 1：读失败进入降级并留下隔离副本
    # ------------------------------------------------------------------

    def test_unparsable_file_enters_readonly_degraded(self) -> None:
        self._seed()
        self._corrupt('[{"summary": "半截写入')
        self.assertEqual(self.store._read_all(), [])
        status = self.store.storage_status()
        self.assertFalse(status["available"])
        self.assertIn("JSON", status["degraded_reason"])

    def test_quarantine_copy_keeps_the_original_in_place(self) -> None:
        self._seed()
        broken = '[{"summary": "半截写入'
        self._corrupt(broken)
        self.store._read_all()
        status = self.store.storage_status()
        # copy 而非 move：原文件仍在，用户可以直接修
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)
        quarantine = Path(status["quarantine_path"])
        self.assertTrue(quarantine.exists())
        self.assertEqual(quarantine.read_text(encoding="utf-8"), broken)
        self.assertIn(".corrupt-", quarantine.name)

    def test_zero_byte_file_is_treated_as_interrupted_write(self) -> None:
        # __init__ 至少写 "[]"，所以 0 字节只可能来自断电/中断的写入（DATA-06）
        self._seed()
        self._corrupt("")
        self.store._read_all()
        self.assertFalse(self.store.storage_status()["available"])

    def test_non_list_toplevel_is_degraded_not_emptied(self) -> None:
        self._seed()
        self._corrupt('{"memories": []}')
        self.store._read_all()
        self.assertFalse(self.store.storage_status()["available"])

    # ------------------------------------------------------------------
    # 不变量 2：降级期间写入被拒绝，原文件字节不变
    # ------------------------------------------------------------------

    def test_add_entry_refuses_to_overwrite_a_corrupt_store(self) -> None:
        self._seed()
        broken = '[{"summary": "半截写入'
        self._corrupt(broken)
        self.store._read_all()
        with self.assertRaises(self.module.MemoryStoreDegradedError):
            self._seed("新记忆")
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)

    def test_clear_also_refuses_while_degraded(self) -> None:
        # 连显式 clear 都不放行：降级期间用户看到的"0 条记忆"是假象，
        # 此时的清空意图不可信。
        self._seed()
        broken = "not json at all"
        self._corrupt(broken)
        self.store._read_all()
        with self.assertRaises(self.module.MemoryStoreDegradedError):
            self.store.clear()
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)

    def test_cleanup_expired_does_not_write_while_degraded(self) -> None:
        # /chat 每次都调 cleanup_expired；降级时它必须安静地什么都不做，
        # 否则整个聊天链路会因为记忆库坏掉而全挂。
        self._seed()
        broken = "not json at all"
        self._corrupt(broken)
        self.assertEqual(self.store.cleanup_expired(), 0)
        self.assertEqual(self.path.read_text(encoding="utf-8"), broken)

    def test_revalidate_recovers_after_the_file_is_fixed(self) -> None:
        entry = self._seed()
        good = self.path.read_text(encoding="utf-8")
        self._corrupt("broken")
        self.store._read_all()
        self.assertFalse(self.store.storage_status()["available"])
        self._corrupt(good)
        self.assertTrue(self.store.revalidate())
        self.assertEqual([item["id"] for item in self.store.get_all()], [entry["id"]])

    # ------------------------------------------------------------------
    # 不变量 3：坏记录不连坐，且写入时原样保留
    # ------------------------------------------------------------------

    def test_missing_created_at_does_not_break_every_memory(self) -> None:
        # 原实现里 datetime.fromisoformat 在 try 之外，一条缺 created_at
        # 的记忆会让所有记忆接口 500。
        good = self._seed()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw.append({"id": "no-created-at", "summary": "缺字段", "facts": []})
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        items = self.store.get_all()
        self.assertTrue(self.store.storage_status()["available"])
        ids = [item["id"] for item in items]
        self.assertIn(good["id"], ids)
        self.assertIn("no-created-at", ids)
        recovered = next(item for item in items if item["id"] == "no-created-at")
        # created_at 被回填成合法 ISO，而不是让整个接口炸掉
        datetime.fromisoformat(recovered["created_at"])

    def test_unreadable_entries_survive_subsequent_writes(self) -> None:
        self._seed()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw.append("这不是一个对象")
        self.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(len(self.store.get_all()), 1)
        self.store.add_entry("再记一条", {}, self.expires)
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("这不是一个对象", on_disk)
        self.assertEqual(len(on_disk), 3)

    def test_deleting_the_last_entry_is_still_allowed(self) -> None:
        # 防止"拒绝写空"这条保护把正常的删除最后一条挡掉
        entry = self._seed()
        self.assertTrue(self.store.delete_entry(entry["id"]))
        self.assertEqual(self.store.get_all(), [])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [])

    def test_clear_on_a_healthy_store_still_works(self) -> None:
        self._seed()
        self.assertEqual(self.store.clear(), 1)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [])

    def test_write_uses_a_unique_temp_name(self) -> None:
        # 固定 tmp 名会让并发写入交错（DATA-07 里 profile 就是这么坏的）
        self._seed()
        leftovers = list(self.path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])


class SourceInvariantTest(unittest.TestCase):
    """源码断言：防止修复退化成第二个 BUG-01（写了但没生效）。"""

    def setUp(self) -> None:
        self.source = (REPO_ROOT / "fithealth_agent" / "info_store.py").read_text(
            encoding="utf-8"
        )

    def test_no_blanket_except_returning_empty_list(self) -> None:
        self.assertNotIn("except Exception:  # noqa: BLE001\n            data = []", self.source)

    def test_write_path_fsyncs(self) -> None:
        from unittest import mock
        from fithealth_agent.info_store import InfoStore
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "fithealth_agent.atomic_json.os.fsync"
        ) as fsync:
            InfoStore(Path(directory) / "info_store.json")
        self.assertGreaterEqual(fsync.call_count, 1)

    def test_degraded_flag_blocks_writes(self) -> None:
        write_body = self.source.split("def _write_all(", 1)[1]
        guard = write_body.split("payload", 1)[0]
        self.assertIn("_degraded_reason is not None", guard)
        self.assertIn("MemoryStoreDegradedError", guard)

    def test_main_surfaces_the_degraded_state(self) -> None:
        # 防止这次修复变成第二个 BUG-01：写了异常类但没人处理，
        # 用户看到的还是一个没头没尾的 500。
        handler_source = module_source(consumer_home("degraded_error_handlers"))
        self.assertIn("MemoryStoreDegradedError", handler_source)
        self.assertIn("memory_store_degraded_handler", handler_source)
        self.assertIn("app.add_exception_handler", handler_source)


if __name__ == "__main__":
    unittest.main()
