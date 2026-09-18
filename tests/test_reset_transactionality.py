"""DATA-14 的回归测试：`/data/reset` 非事务，中途失败留下半删状态。

原实现把六个 store 顺序清空，`except` 只在最外层。第 3 步抛错时前 2 步已经
**永久**删掉了，而响应里连"删掉了多少"都没有，用户只看到一句 503。这不是理论
风险：`info_store.clear()` 在记忆库只读降级时抛 `MemoryStoreDegradedError`，
`health_store.clear()` 在数据库只读降级时抛 `HealthStoreDegradedError`，两者都是
`RuntimeError`，窄 `except (OSError, sqlite3.Error)` 抓不到。

修法两条都做了，因为哪一条单独都不够：
* **删除前先落恢复点**，写不出来就一个字节都不删（给出退路）；
* **逐项 try/except 并返回明细**（把删除做完，并把成败说清楚）。

恢复点刻意就是一份普通备份 zip，所以"怎么还原"不需要任何新代码——下载后走
已有的 `/data/backup/import`。这里的测试把这个前提钉住，它一旦不成立，恢复点
就变成一份谁也用不了的文件。
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import os
import re
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest import mock

from fithealth_agent import backup_service as backup_service_module
from fithealth_agent.backup_service import (
    RECOVERY_POINT_DIR,
    RECOVERY_POINT_KEEP,
    LocalBackupService,
)
from fithealth_agent.health_store import HealthStore
from fithealth_agent.hr_stream_store import HRStreamStore
from fithealth_agent.info_store import InfoStore, MemoryStoreDegradedError
from fithealth_agent.maintenance import MaintenanceGate
from fithealth_agent.plan_store import TrainingPlanStore
from fithealth_agent.soreness_store import SorenessStore
from fithealth_agent.storage import DailyRecordStore, UserProfileStore
from fithealth_agent.runtime import deps
from tests.module_map import consumer_home
from tests.source_tools import function_node

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"

RAW_IMPORT_NAME = "6debb86f49d8180d-2026-08-15.zip"
HR_STREAM_NAME = "16e93f38.json"

#: 专供"首次 import main"使用的进程级目录，详见 ResetEndpointTest.setUpClass。
_IMPORT_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def build_data_dir(root: Path) -> None:
    """搭一个内容齐全的数据目录，每一类数据都放一条可辨认的内容。"""
    (root / "daily_records.json").write_text(
        json.dumps(
            [{"id": "r1", "date": "2026-08-21", "revision": 1, "record": {"note": "卧推 60kg"}}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "user_profile.json").write_text(
        json.dumps({"height_cm": 175, "goal": "增肌"}, ensure_ascii=False), encoding="utf-8"
    )
    (root / "training_plans.json").write_text(
        json.dumps([{"id": "p1", "date": "2026-08-21", "content": "腿部训练"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "info_store.json").write_text(
        json.dumps([{"id": "m1", "content": "不喜欢某频道"}], ensure_ascii=False), encoding="utf-8"
    )
    raw_dir = root / "health-imports"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / RAW_IMPORT_NAME).write_bytes(b"PK\x03\x04 pretend garmin export")
    streams = root / "hr_streams"
    streams.mkdir(exist_ok=True)
    (streams / HR_STREAM_NAME).write_text(
        json.dumps({"record_id": "16e93f38", "samples": [{"heart_rate": 94}]}), encoding="utf-8"
    )


class RecoveryPointTest(unittest.TestCase):
    """恢复点本身：写得出来、认得回去、不会把自己撑爆磁盘。"""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        build_data_dir(self.root)
        self.service = LocalBackupService(self.root, gate=MaintenanceGate())

    def _names(self) -> list[str]:
        return [item["name"] for item in self.service.list_recovery_points()]

    def test_snapshot_is_an_ordinary_backup_and_needs_no_new_restore_code(self) -> None:
        """整条修法的支点：恢复点能被现成的 validate/restore 吃下去。"""
        described = self.service.write_recovery_point()
        self.assertRegex(described["name"], r"^pre-reset-\d{14}\.zip$")

        content = self.service.read_recovery_point(described["name"])
        self.assertEqual(described["bytes"], len(content))
        payloads = self.service.validate(content)  # 不抛就说明校验和与成员表都对得上
        self.assertIn("daily_records.json", payloads)

    def test_snapshot_holds_the_data_that_is_about_to_be_deleted(self) -> None:
        described = self.service.write_recovery_point()
        content = self.service.read_recovery_point(described["name"])

        # 模拟"删除全部数据"之后再还原。
        (self.root / "daily_records.json").write_text("[]", encoding="utf-8")
        (self.root / "training_plans.json").write_text("[]", encoding="utf-8")
        (self.root / "user_profile.json").write_text("{}", encoding="utf-8")
        (self.root / "hr_streams" / HR_STREAM_NAME).unlink()

        self.service.restore(content)
        records = json.loads((self.root / "daily_records.json").read_text(encoding="utf-8"))
        self.assertEqual(records[0]["record"]["note"], "卧推 60kg")
        self.assertEqual(
            json.loads((self.root / "user_profile.json").read_text(encoding="utf-8"))["height_cm"],
            175,
        )
        self.assertTrue((self.root / "hr_streams" / HR_STREAM_NAME).is_file())

    def test_snapshots_do_not_nest_inside_each_other(self) -> None:
        """`recovery-points/` 不进备份目录清单，否则第 N 份会套进第 N+1 份。"""
        self.service.write_recovery_point()
        second = self.service.write_recovery_point()
        content = self.service.read_recovery_point(second["name"])
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            nested = [name for name in archive.namelist() if RECOVERY_POINT_DIR in name]
        self.assertEqual(nested, [])
        self.assertNotIn(RECOVERY_POINT_DIR, backup_service_module.BACKUP_DIRECTORIES)

    def test_same_second_writes_never_overwrite_each_other(self) -> None:
        """半失败后立刻重试会撞到同一秒，而后一份快照拍的是已经被清空的数据。"""
        fixed = datetime(2026, 8, 21, 13, 19, 45)
        with mock.patch.object(backup_service_module, "datetime") as clock:
            clock.now.return_value = fixed
            clock.strptime = datetime.strptime
            names = [self.service.write_recovery_point()["name"] for _ in range(4)]

        self.assertEqual(len(set(names)), 4)
        self.assertEqual(names, sorted(names), "名字必须随创建顺序单调递增")

    def test_the_snapshot_just_written_is_never_pruned(self) -> None:
        """曾经真的踩到：prune 腾出的空位在过去，新快照填进去后立刻被当成最老的删掉。"""
        fixed = datetime(2026, 8, 21, 13, 19, 45)
        with mock.patch.object(backup_service_module, "datetime") as clock:
            clock.now.return_value = fixed
            clock.strptime = datetime.strptime
            for _ in range(RECOVERY_POINT_KEEP + 3):
                described = self.service.write_recovery_point()
                path = self.service.recovery_dir / described["name"]
                self.assertTrue(path.is_file(), f"{described['name']} 写完就不见了")

    def test_only_the_newest_ones_are_kept_newest_first(self) -> None:
        for index in range(RECOVERY_POINT_KEEP + 2):
            with mock.patch.object(backup_service_module, "datetime") as clock:
                clock.now.return_value = datetime(2026, 8, 21, 13, 0, index)
                clock.strptime = datetime.strptime
                self.service.write_recovery_point()

        names = self._names()
        self.assertEqual(len(names), RECOVERY_POINT_KEEP)
        self.assertEqual(names, sorted(names, reverse=True))
        self.assertEqual(names[0], "pre-reset-20260821130004.zip")

    def test_prune_orders_reset_and_restore_points_by_timestamp(self) -> None:
        self.service.recovery_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "pre-restore-20260103000000.zip",
            "pre-restore-20260102000000.zip",
            "pre-restore-20260101000000.zip",
            "pre-reset-20260824120000.zip",
        ):
            (self.service.recovery_dir / name).write_bytes(b"x")

        self.service._prune_recovery_points(keep="pre-reset-20260824120000.zip")

        self.assertEqual(self._names(), [
            "pre-reset-20260824120000.zip",
            "pre-restore-20260103000000.zip",
            "pre-restore-20260102000000.zip",
        ])

    def test_next_name_is_newer_than_points_with_the_other_prefix(self) -> None:
        self.service.recovery_dir.mkdir(parents=True, exist_ok=True)
        (self.service.recovery_dir / "pre-restore-20260824120000.zip").write_bytes(b"x")

        path = self.service._next_recovery_point_path(
            datetime(2026, 1, 1), prefix="pre-reset"
        )

        self.assertEqual(path.name, "pre-reset-20260824120001.zip")

    def test_prune_keeps_the_named_file_even_when_it_is_the_oldest(self) -> None:
        self.service.recovery_dir.mkdir(parents=True, exist_ok=True)
        for index in range(RECOVERY_POINT_KEEP + 2):
            (self.service.recovery_dir / f"pre-reset-2026082113000{index}.zip").write_bytes(b"x")
        oldest = "pre-reset-20260821130000.zip"

        self.service._prune_recovery_points(keep=oldest)
        self.assertIn(oldest, self._names())

    def test_content_is_fsynced_before_the_rename(self) -> None:
        """否则"恢复点已生成"只是页缓存里的一句空话，断电时和数据一起消失。"""
        observed: list[bool] = []
        real_fsync = os.fsync

        def recording_fsync(fd: int) -> None:
            # fsync 时目标文件还不该存在——先落内容，再原子替换。
            observed.append(any(self.service.recovery_dir.glob("pre-reset-*.zip")))
            real_fsync(fd)

        with mock.patch.object(os, "fsync", recording_fsync):
            self.service.write_recovery_point()

        self.assertTrue(observed, "写恢复点必须调用 fsync")
        self.assertEqual(observed, [False] * len(observed))

    def test_a_failed_write_leaves_neither_a_target_nor_a_temp_file(self) -> None:
        with mock.patch.object(os, "fsync", side_effect=OSError("磁盘已满")):
            with self.assertRaises(OSError):
                self.service.write_recovery_point()

        # 抛出而不是返回失败标记，调用方才会因此中止删除。
        self.assertEqual(self._names(), [])
        self.assertEqual(list(self.service.recovery_dir.glob("*.tmp")), [])

    def test_names_from_outside_are_treated_as_untrusted_input(self) -> None:
        for name in (
            "../health.db",
            "..",
            "health-imports/a.csv",
            "pre-reset-1.zip",
            "pre-reset-20260821130000.zip.tmp",
            "pre-reset-20260821130000.ZIP",
            "",
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.service.read_recovery_point(name)
                with self.assertRaises(ValueError):
                    self.service.delete_recovery_point(name)

    def test_deleting_an_unknown_point_reports_false_instead_of_raising(self) -> None:
        self.assertFalse(self.service.delete_recovery_point("pre-reset-20260101000000.zip"))
        described = self.service.write_recovery_point()
        self.assertTrue(self.service.delete_recovery_point(described["name"]))
        self.assertEqual(self._names(), [])

    def test_listing_ignores_foreign_files_in_the_directory(self) -> None:
        self.service.write_recovery_point()
        (self.service.recovery_dir / "notes.txt").write_text("hi", encoding="utf-8")
        (self.service.recovery_dir / "subdir").mkdir()
        self.assertEqual(len(self._names()), 1)


class ResetEndpointTest(unittest.TestCase):
    """走真实端点验证三种失败形态，因为 DATA-14 描述的缺陷全在编排层。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls._directory.name)
        cls._previous = os.environ.get("FITHEALTH_DATA_DIR")
        # **不能把本用例自己的目录用于这次 import**：main 会在模块级把各个 store
        # 建好并永久绑定这个路径，而 tearDownClass 结束时目录就被删了。如果整套
        # 测试里恰好是本文件第一个 import main（取决于执行顺序），后面任何一条读
        # 档案的测试都会 FileNotFoundError——实测在某些顺序下会让
        # test_journal_backup_and_restore_safety 里一条无关的中间件测试变红。
        # 用一个进程级目录，由解释器退出时回收。
        os.environ["FITHEALTH_DATA_DIR"] = _IMPORT_DIR.name
        try:
            import importlib

            from fastapi.testclient import TestClient

            cls.main = importlib.import_module("main")
        finally:
            if cls._previous is None:
                os.environ.pop("FITHEALTH_DATA_DIR", None)
            else:
                os.environ["FITHEALTH_DATA_DIR"] = cls._previous

        # store 是 `deps` 的模块级单例，**谁先 import deps 谁定下数据目录**。整套
        # 测试跑下来时先 import 的可能是别的模块，它的临时目录还会在自己 tearDown
        # 时被删掉——于是这里会对着一个已经不存在的目录跑删除测试。所以不依赖环境
        # 变量，显式把要用到的 store 指到本用例自己的目录，跑完再换回去。
        cls._originals = {
            name: getattr(deps, name)
            for name in (
                "daily_record_store",
                "profile_store",
                "plan_store",
                "info_store",
                "health_store",
                "hr_stream_store",
                "backup_service",
                "soreness_store",
            )
        }
        cls._original_workout_persist_path = cls.main.workout_store._PERSIST_PATH
        deps.daily_record_store = DailyRecordStore(cls.root / "daily_records.json")
        deps.profile_store = UserProfileStore(cls.root / "user_profile.json")
        deps.plan_store = TrainingPlanStore(cls.root / "training_plans.json")
        deps.info_store = InfoStore(cls.root / "info_store.json")
        deps.health_store = HealthStore(cls.root / "health.db", cls.root / "health-imports")
        deps.hr_stream_store = HRStreamStore(cls.root / "hr_streams")
        deps.soreness_store = SorenessStore(cls.root / "muscle_soreness.json")
        cls.main.workout_store._PERSIST_PATH = cls.root / "pending_workout.json"
        cls.main.workout_store.clear_current()
        deps.backup_service = LocalBackupService(
            cls.root, gate=cls.main.MAINTENANCE, database=deps.health_store
        )
        cls.client = TestClient(cls.main.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.main.workout_store.clear_current()
        cls.main.workout_store._PERSIST_PATH = cls._original_workout_persist_path
        for name, value in cls._originals.items():
            setattr(deps, name, value)
        cls._directory.cleanup()

    def setUp(self) -> None:
        # 每条用例都从"恰好一条记录"开始，否则"删了多少""还剩多少"这类断言会
        # 被上一条用例的残留数据带偏（恢复点回灌那条尤其明显）。
        deps.daily_record_store.clear()
        deps.plan_store.clear()
        deps.info_store.clear()
        deps.soreness_store.clear()
        deps.daily_record_store.add_record("2026-08-21", "training", {"note": "卧推 60kg"})
        deps.profile_store.update_profile({"height_cm": 175})
        for item in deps.backup_service.list_recovery_points():
            deps.backup_service.delete_recovery_point(item["name"])

    def _counts(self) -> tuple[int, int]:
        return (
            len(deps.daily_record_store.list_records()),
            len(deps.plan_store.list_plans()),
        )

    def _reset(self, confirmation: str = "删除全部数据"):
        return self.client.post("/data/reset", json={"confirmation": confirmation})

    def test_wrong_confirmation_deletes_nothing_and_writes_no_snapshot(self) -> None:
        before = self._counts()
        response = self._reset("删掉吧")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._counts(), before)
        self.assertEqual(deps.backup_service.list_recovery_points(), [])

    def test_successful_reset_reports_every_step_and_the_recovery_point(self) -> None:
        response = self._reset()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertTrue(body["deleted"])
        self.assertFalse(body["partial"])
        self.assertRegex(body["recovery_point"]["name"], r"^pre-reset-\d{14}\.zip$")
        self.assertGreater(body["recovery_point"]["bytes"], 0)
        self.assertEqual([step["error"] for step in body["steps"]], [None] * len(body["steps"]))
        self.assertEqual(body["records_removed"], 1)
        self.assertEqual(body["profile_reset"], 1)
        self.assertEqual(self._counts(), (0, 0))

    def test_step_keys_are_flattened_for_the_old_response_shape(self) -> None:
        body = self._reset().json()
        for step in body["steps"]:
            with self.subTest(key=step["key"]):
                self.assertEqual(body[step["key"]], step["removed"])

    def test_reset_deletes_the_tool_output_overflow_files(self) -> None:
        """工具输出超限时，未截断原文（含训练与健康记录）落在
        `data/tool-output/tool_*.json`。"删除全部数据"原先不管它，于是十项 store
        清空之后健康原文还留在盘上。"""
        from fithealth_agent import tool_output

        overflow_dir = tool_output.tool_output_dir()
        overflow_dir.mkdir(parents=True, exist_ok=True)
        overflow = overflow_dir / "tool_20260821_000000_000000_query_daily_records.json"
        overflow.write_text('{"output": "卧推 60kg"}', encoding="utf-8")
        keeper = overflow_dir / "notes.md"
        keeper.write_text("keep", encoding="utf-8")
        self.addCleanup(lambda: keeper.unlink(missing_ok=True))

        body = self._reset().json()

        self.assertEqual(body["tool_output_removed"], 1)
        self.assertFalse(overflow.exists())
        self.assertTrue(keeper.exists(), "清理只该动 tool_*.json")

    def test_reset_deletes_the_agent_traces(self) -> None:
        """TRACE-06：agent 执行轨迹里有用户消息摘要、闸门结论与健康信号。它不是用户
        数据（不进备份、不需要恢复点），但"删除全部数据"必须覆盖它，否则十一项清空
        之后健康细节还留在 data/traces/ 里。"""
        from fithealth_agent.observability import trace_dir

        traces = trace_dir() / "2026-09-05"
        traces.mkdir(parents=True, exist_ok=True)
        turn = traces / "turn-t-20260905-101929-74ca3f.jsonl"
        turn.write_text('{"kind": "turn_start"}\n', encoding="utf-8")
        index = trace_dir() / "index.jsonl"
        index.write_text('{"turn_id": "t-20260905-101929-74ca3f"}\n', encoding="utf-8")

        body = self._reset().json()

        self.assertEqual(body["traces_removed"], 2)
        self.assertFalse(turn.exists())
        self.assertFalse(index.exists())
        # 幂等：第二次 reset 不该因为目录已空而报错。
        self.assertEqual(self._reset().json()["traces_removed"], 0)

    def test_a_failing_step_no_longer_aborts_the_remaining_ones(self) -> None:
        """DATA-14 的核心：只读降级异常是 RuntimeError，旧的窄 except 抓不到它。"""
        with mock.patch.object(
            deps.info_store, "clear", side_effect=MemoryStoreDegradedError("记忆库只读降级")
        ):
            response = self._reset()

        # 半删状态必须是 200 + partial：5xx 会让前端只显示"操作失败"，正是原缺陷。
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["deleted"])
        self.assertTrue(body["partial"])

        failed = [step["label"] for step in body["steps"] if step["error"]]
        self.assertEqual(failed, ["临时记忆"])
        self.assertIn("临时记忆", body["error"])
        self.assertIn(body["recovery_point"]["name"], body["recovery_hint"])

        # 其余项确实执行到了，而且盘上真的删了。
        self.assertEqual(self._counts(), (0, 0))
        self.assertEqual(len([step for step in body["steps"] if not step["error"]]), 11)

    def test_the_recovery_point_of_a_partial_reset_holds_the_pre_failure_data(self) -> None:
        with mock.patch.object(
            deps.info_store, "clear", side_effect=MemoryStoreDegradedError("记忆库只读降级")
        ):
            body = self._reset().json()

        content = deps.backup_service.read_recovery_point(body["recovery_point"]["name"])
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            records = json.loads(archive.read("daily_records.json"))
        self.assertEqual(records[0]["record"]["note"], "卧推 60kg")

    def test_nothing_is_deleted_when_the_snapshot_cannot_be_written(self) -> None:
        before = self._counts()
        with mock.patch.object(
            deps.backup_service, "write_recovery_point", side_effect=OSError("磁盘已满")
        ):
            response = self._reset()

        self.assertEqual(response.status_code, 500)
        self.assertIn("数据未做任何改动", response.json()["error"])
        self.assertEqual(self._counts(), before)

    def test_reset_is_refused_while_another_maintenance_runs(self) -> None:
        before = self._counts()
        with self.main.MAINTENANCE.exclusive("恢复备份", timeout=5):
            response = self.client.post("/data/reset", json={"confirmation": "删除全部数据"})
            # 中间件先看维护开关，所以这里是 503；端点自身的 409 在下一条。
            self.assertEqual(response.status_code, 503)
            from fithealth_agent.routes.maintenance_ops import reset_all_data

            direct = reset_all_data({"confirmation": "删除全部数据"})
        self.assertEqual(direct.status_code, 409)
        self.assertEqual(self._counts(), before)

    def test_reset_does_not_count_itself_as_an_inflight_request(self) -> None:
        """否则它持开关后排空会等自己，直接死等到超时。"""
        from fithealth_agent.runtime.middleware import MAINTENANCE_UNTRACKED_PATHS

        self.assertIn("/data/reset", MAINTENANCE_UNTRACKED_PATHS)

    def test_recovery_point_round_trip_over_http_restores_everything(self) -> None:
        name = self._reset().json()["recovery_point"]["name"]
        self.assertEqual(self._counts(), (0, 0))

        listed = self.client.get("/data/recovery-points")
        self.assertEqual(listed.status_code, 200)
        self.assertIn(name, [item["name"] for item in listed.json()["points"]])

        download = self.client.get(f"/data/recovery-points/{name}")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["content-type"], "application/zip")
        self.assertIn(name, download.headers["content-disposition"])

        # 关键：还原走的是现成的导入备份接口，没有为恢复点新开代码路径。
        restored = self.client.post(
            "/data/backup/import",
            files={"file": (name, download.content, "application/zip")},
            data={"confirm_restore": "true"},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(self._counts()[0], 1)
        self.assertEqual(
            deps.daily_record_store.list_records()[0]["record"]["note"], "卧推 60kg"
        )

    def test_recovery_points_can_be_deleted_over_http(self) -> None:
        name = self._reset().json()["recovery_point"]["name"]
        self.assertEqual(self.client.delete(f"/data/recovery-points/{name}").status_code, 200)
        self.assertEqual(self.client.get("/data/recovery-points").json()["points"], [])
        self.assertEqual(self.client.delete(f"/data/recovery-points/{name}").status_code, 404)

    def test_traversal_through_the_path_parameter_cannot_reach_other_files(self) -> None:
        for name in ("../../main.py", "..%2f..%2fmain.py", "%2e%2e/health.db", "health.db"):
            with self.subTest(name=name):
                self.assertIn(
                    self.client.get(f"/data/recovery-points/{name}").status_code, (400, 404)
                )
                self.assertIn(
                    self.client.delete(f"/data/recovery-points/{name}").status_code, (400, 404)
                )
        self.assertTrue((REPO_ROOT / "main.py").is_file())


class StructuralInvariantTest(unittest.TestCase):
    """只保留控制流与调用边界这类 AST 结构不变量。"""

    #: 每个被检查的函数分别属于哪个消费者接线点（见 tests/module_map.py）。
    #: 端点与两个 reset 步骤函数将来会一起搬到 routes/maintenance_ops.py，
    #: 但它们各自登记，搬迁时不必是同一步。
    FUNCTION_CONSUMERS = {
        "reset_all_data": "reset_all_data",
        "_run_reset_steps": "reset_steps",
        "_reset_steps": "reset_steps",
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.main = importlib.import_module("main")

    def _function(self, name: str) -> ast.FunctionDef:
        return function_node(consumer_home(self.FUNCTION_CONSUMERS[name]), name)

    def _calls(self, name: str) -> list[str]:
        calls = [node for node in ast.walk(self._function(name)) if isinstance(node, ast.Call)]
        calls.sort(key=lambda node: (node.lineno, node.col_offset))
        return [ast.unparse(node.func) for node in calls]

    def test_the_snapshot_is_taken_before_any_deletion(self) -> None:
        calls = self._calls("reset_all_data")
        self.assertIn("MAINTENANCE.exclusive", calls)
        self.assertLess(
            calls.index("deps.backup_service.write_recovery_point"),
            calls.index("_run_reset_steps"),
            "快照必须排在删除之前，否则它拍的是已经被清空的数据",
        )

    def test_the_endpoint_does_not_clear_any_store_directly(self) -> None:
        """全部清空动作都必须走 _reset_steps，否则又会有一条没有容错的路径。"""
        calls = self._calls("reset_all_data")
        # 带 `deps.` 前缀：阶段 1 之后 store 都从 runtime.deps 属性访问取用，写成
        # 不带前缀的旧形式会让这条 assertNotIn 恒真，静默失去意义。
        for call in (
            "deps.daily_record_store.clear",
            "deps.plan_store.clear",
            "deps.info_store.clear",
            "deps.health_store.clear",
            "deps.hr_stream_store.clear",
            "deps.profile_store.reset",
        ):
            with self.subTest(call=call):
                self.assertNotIn(call, calls)

    def test_every_step_is_wrapped_in_its_own_handler(self) -> None:
        runner = self._function("_run_reset_steps")
        loop = next(node for node in ast.walk(runner) if isinstance(node, ast.For))
        tries = [node for node in loop.body if isinstance(node, ast.Try)]
        self.assertEqual(len(tries), 1, "每一步都要单独 try/except，一项失败不该跳过其余项")
        handlers = [
            ast.unparse(handler.type) if handler.type else None for handler in tries[0].handlers
        ]
        # 只读降级异常是 RuntimeError；(OSError, sqlite3.Error) 这类窄 except 抓不到。
        self.assertEqual(handlers, ["Exception"])

    def test_referring_data_is_cleared_before_what_it_references(self) -> None:
        """中途失败该留下孤儿文件，而不是悬空引用（后者是静默的数据损坏）。"""
        # 从源码读键序而不是 import main：那会在真实 data/ 目录上建 store 实例。
        steps = self._function("_reset_steps")
        returned = next(node for node in ast.walk(steps) if isinstance(node, ast.Return))
        keys = [
            entry.elts[0].value
            for entry in returned.value.elts
            if isinstance(entry, ast.Tuple) and isinstance(entry.elts[0], ast.Constant)
        ]
        self.assertEqual(len(keys), 12, keys)
        self.assertLess(
            keys.index("records_removed"),
            keys.index("hr_streams_removed"),
            "daily_records 按 id 引用 hr_streams/",
        )
        self.assertLess(keys.index("records_removed"), keys.index("profile_reset"))
        self.assertIn("plan_drafts_removed", keys)
        # 最后两项是两类**诊断产物**：工具输出溢出文件（data/tool-output/tool_*.json，
        # 别处数据的完整副本）与 agent 执行轨迹（data/traces/，含消息摘要与健康信号）。
        # 它们不被任何东西引用，所以排在最后；但都必须在——否则十项 store 清空之后
        # 健康细节还留在盘上。两者之间的先后无所谓。
        self.assertEqual(keys[-2:], ["tool_output_removed", "traces_removed"])

    def test_recovery_points_are_not_packed_into_backups(self) -> None:
        self.assertNotIn(RECOVERY_POINT_DIR, backup_service_module.BACKUP_DIRECTORIES)
        self.assertNotIn(RECOVERY_POINT_DIR, backup_service_module.JSON_FILES)
        self.assertEqual(RECOVERY_POINT_KEEP, 3)

    def test_recovery_point_endpoints_are_wired_up(self) -> None:
        routes = {route.path for route in self.main.app.routes}
        for route in ("/data/recovery-points", "/data/recovery-points/{name}"):
            with self.subTest(route=route):
                self.assertIn(route, routes)


if __name__ == "__main__":
    unittest.main()
