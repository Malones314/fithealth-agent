"""保留策略、清理闭环与离线查看（agent-trace 阶段 6）。

## 这一阶段解决什么

阶段 1-5 之后 trace 已经可用，但两个治理缺口还开着：

* **TRACE-06（P0）**：`memory/traces` 不在 `/data/reset` 的步骤里，也不受备份事务管辖。
  "删除全部数据"之后，健康细节还留在盘上；恢复备份之后，旧轨迹与新数据混在一个目录里。
* **TRACE-09**：没有轮转、没有上限、没有查看工具。找"昨天那次计划校验失败"只能 grep。

## 四条不变量

1. **三条上限同时生效，从最旧开始删**，而且**永不删最新那个回合**——一个超大回合本身
   就超上限时，把刚出问题的那次对话抹掉是最坏的结果。
2. **清理不抛**。`TraceStore.clear` 同时挂在 `/data/reset` 的一步和备份恢复的
   `on_restored` 回调上；后者抛异常会让一次**已经生效**的恢复被报成失败。
3. **只删认得出来的产物**。`FITHEALTH_TRACE_DIR` 可以指向任何地方，用户放在那里的
   别的文件不该被顺手抹掉；反过来，`index.jsonl.lock` 与 `.tmp` 残片必须删净。
4. **查看工具全转义**。TRACE-08 的教训：payload 里是原始用户输入。
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fithealth_agent.backup_service import LocalBackupService
from fithealth_agent.observability import PruneResult, TraceStore, load_settings, prune
from fithealth_agent.observability import sink as trace_sink
from fithealth_agent.runtime import deps

from tests.test_trace_infrastructure import trace_env


REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 5)


def _load_cli():
    """按路径加载 `scripts/trace_report.py`。

    脚本不在包里（`scripts/` 没有 `__init__.py`），而它自己会把仓库根塞进
    `sys.path` 以便 import `fithealth_agent.observability`——所以这里直接执行它。
    """
    spec = importlib.util.spec_from_file_location(
        "trace_report_for_test", REPO_ROOT / "scripts" / "trace_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLI = _load_cli()


class TraceFixture:
    """在真实目录里造 trace 产物。所有断言都对着盘上的文件，不看内存态。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.rows: list[dict] = []
        self._serial = 0

    def add(
        self,
        day: date,
        *,
        payload_bytes: int = 1,
        events: list[dict] | None = None,
        started_at: datetime | None = None,
    ) -> Path:
        """造一个回合文件并追一行 index。同一天多次调用按秒递增，于是顺序可预期。

        `started_at` 只影响 index 那一行（`--since` 只看它）。给它一个相对**真实
        现在**的时刻，就能写出不依赖机器时钟的 `--since` 用例。
        """
        self._serial += 1
        day_name = day.isoformat()
        turn_id = (
            f"t-{day_name.replace('-', '')}-{120000 + self._serial:06d}"
            f"-{self._serial:06x}"
        )
        target = self.directory / day_name / f"{trace_sink.TURN_PREFIX}{turn_id}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        if events is None:
            events = [{"v": 1, "turn_id": turn_id, "seq": 1, "kind": "turn_start",
                       "span": "/chat", "payload": {"route": "/chat"}}]
            body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events)
            body += "x" * max(payload_bytes - len(body.encode("utf-8")), 0)
        else:
            body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events)
        target.write_text(body, encoding="utf-8", newline="\n")
        moment = started_at or datetime(
            day.year, day.month, day.day, 12, 0, self._serial, tzinfo=timezone.utc
        )
        self.rows.append({
            "v": 1, "turn_id": turn_id, "request_id": f"r{self._serial}", "route": "/chat",
            "started_at": moment.isoformat(timespec="milliseconds"),
            "status": "ok", "events": len(events), "detail_level": "meta",
            "file": target.name, "day": day_name,
        })
        self.write_index()
        return target

    def add_stale_index_row(self) -> None:
        """落盘失败时留下的 `file: null` 行。prune 应当在清理时顺手丢掉它。"""
        self.rows.append({
            "v": 1, "turn_id": "t-20260101-000000-ffffff", "route": "/chat",
            "started_at": "2026-01-01T12:00:00.000+00:00", "status": "error",
            "events": 0, "detail_level": "meta", "file": None, "day": "2026-01-01",
        })
        self.write_index()

    def write_index(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / trace_sink.INDEX_NAME).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows),
            encoding="utf-8", newline="\n",
        )

    def turn_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.directory).as_posix()
            for path in self.directory.rglob(f"{trace_sink.TURN_PREFIX}*.jsonl")
        )

    def indexed_turn_ids(self) -> list[str]:
        path = self.directory / trace_sink.INDEX_NAME
        if not path.is_file():
            return []
        return [
            json.loads(line)["turn_id"]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def everything(self) -> list[str]:
        return sorted(
            path.relative_to(self.directory).as_posix() for path in self.directory.rglob("*")
        )


def fixture_in(root: Path) -> TraceFixture:
    return TraceFixture(root / "traces")


class PruneLimitsTest(unittest.TestCase):
    """三条上限：各自生效、边界值、同时生效时的删除顺序。"""

    def test_the_turn_limit_keeps_the_newest_ones(self) -> None:
        # 断言必须在 with 内取：trace_env 退出时会删掉临时目录。
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_TURNS="3") as root:
            traces = fixture_in(root)
            paths = [traces.add(TODAY) for _ in range(5)]
            result = prune(load_settings(), today=TODAY)
            self.assertEqual(result.turns_removed, 2)
            self.assertEqual(
                traces.turn_files(),
                sorted(path.relative_to(traces.directory).as_posix() for path in paths[2:]),
            )

    def test_exactly_at_the_limit_deletes_nothing(self) -> None:
        """边界值：等于上限不算超限。差一个 off-by-one 就会每回合删掉最旧那个。"""
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_TURNS="3") as root:
            traces = fixture_in(root)
            for _ in range(3):
                traces.add(TODAY)
            result = prune(load_settings(), today=TODAY)
            self.assertEqual(result, PruneResult())
            self.assertEqual(len(traces.turn_files()), 3)

    def test_the_day_limit_counts_today_as_the_first_day(self) -> None:
        """`max_days=1` 表示"只留今天"。日期目录是本地日期，比较用字符串。"""
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_DAYS="1") as root:
            traces = fixture_in(root)
            traces.add(TODAY - timedelta(days=1))
            kept = traces.add(TODAY)
            prune(load_settings(), today=TODAY)
            self.assertEqual(traces.turn_files(), [kept.relative_to(traces.directory).as_posix()])

    def test_the_day_limit_keeps_the_whole_window(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_DAYS="3") as root:
            traces = fixture_in(root)
            for offset in (0, 1, 2, 3, 30):
                traces.add(TODAY - timedelta(days=offset))
            result = prune(load_settings(), today=TODAY)
            self.assertEqual(result.turns_removed, 2)
            self.assertEqual(result.days_removed, 2)
            self.assertEqual(
                sorted({path.split("/")[0] for path in traces.turn_files()}),
                ["2026-09-03", "2026-09-04", "2026-09-05"],
            )

    def test_the_byte_limit_deletes_oldest_first(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_BYTES="2500") as root:
            traces = fixture_in(root)
            paths = [traces.add(TODAY, payload_bytes=1000) for _ in range(4)]
            result = prune(load_settings(), today=TODAY)
            self.assertEqual(result.turns_removed, 2)
            self.assertFalse(result.over_budget)
            self.assertEqual(
                traces.turn_files(),
                sorted(path.relative_to(traces.directory).as_posix() for path in paths[2:]),
            )

    def test_a_single_oversized_turn_is_kept_and_flagged(self) -> None:
        """删到只剩最新一个仍然超限时不再继续删——那次对话恰恰是要看的那次。"""
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_BYTES="500") as root:
            traces = fixture_in(root)
            traces.add(TODAY, payload_bytes=400)
            newest = traces.add(TODAY, payload_bytes=4000)
            with self.assertLogs("fithealth", level="WARNING") as logs:
                result = prune(load_settings(), today=TODAY)
            self.assertEqual(traces.turn_files(),
                             [newest.relative_to(traces.directory).as_posix()])
            self.assertEqual([result.turns_removed, result.over_budget], [1, True])
            # 告警要能检索到，且不含回合内容。
            self.assertTrue(any("字节上限" in line for line in logs.output), logs.output)

    def test_all_three_limits_apply_together(self) -> None:
        """三条规则的目标都是"最旧那一批"，所以它们会重叠——重叠不该导致重复删或漏删。

        这里 4 个回合：两个 5 天前（撞天数上限，其中最旧那个还撞回合数上限）、
        一个昨天（前两条判完之后撞字节上限）、一个今天（必须留下）。
        """
        with trace_env(
            FITHEALTH_TRACE="on",
            FITHEALTH_TRACE_MAX_TURNS="3",
            FITHEALTH_TRACE_MAX_DAYS="2",
            FITHEALTH_TRACE_MAX_BYTES="900",
        ) as root:
            traces = fixture_in(root)
            traces.add(TODAY - timedelta(days=5), payload_bytes=600)
            traces.add(TODAY - timedelta(days=5), payload_bytes=600)
            traces.add(TODAY - timedelta(days=1), payload_bytes=600)
            kept = traces.add(TODAY, payload_bytes=600)
            result = prune(load_settings(), today=TODAY)
            self.assertEqual(result.turns_removed, 3)
            self.assertEqual(traces.turn_files(),
                             [kept.relative_to(traces.directory).as_posix()])

    def test_empty_day_directories_are_collected(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_DAYS="1") as root:
            traces = fixture_in(root)
            traces.add(TODAY - timedelta(days=2))
            traces.add(TODAY)
            prune(load_settings(), today=TODAY)
            self.assertNotIn("2026-09-03", traces.everything())
            self.assertIn("2026-09-05", traces.everything())


class PruneIndexTest(unittest.TestCase):
    """index 必须与盘上的文件一致，而且不能因为 prune 而无限增长。"""

    def test_only_rows_whose_file_survives_are_kept(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_TURNS="2") as root:
            traces = fixture_in(root)
            ids = [traces.add(TODAY) for _ in range(4)]
            traces.add_stale_index_row()
            prune(load_settings(), today=TODAY)
            surviving = [path.name for path in sorted(traces.directory.rglob("turn-*.jsonl"))]
            self.assertEqual(len(surviving), 2)
            # 落盘失败留下的 `file: null` 行一起丢掉：留着会指向不存在的文件。
            self.assertEqual(
                traces.indexed_turn_ids(),
                [name[len(trace_sink.TURN_PREFIX):-len(".jsonl")] for name in surviving],
            )
            self.assertEqual(len(ids), 4)

    def test_the_index_is_left_alone_when_nothing_was_deleted(self) -> None:
        """index 重写是 O(N) 原子写，而 prune 每个回合都跑。没删东西就不能碰它。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            traces.add_stale_index_row()
            index = traces.directory / trace_sink.INDEX_NAME
            before = index.read_text(encoding="utf-8")
            self.assertEqual(prune(load_settings(), today=TODAY), PruneResult())
            # 陈旧行还在：它要等到下一次真删了东西才会被顺手清掉。
            self.assertEqual(index.read_text(encoding="utf-8"), before)

    def test_a_half_written_index_line_does_not_break_the_rewrite(self) -> None:
        """stream 模式崩溃可能留半行。重写要能跳过它，而不是整份索引作废。"""
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_TURNS="1") as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            kept = traces.add(TODAY)
            index = traces.directory / trace_sink.INDEX_NAME
            with index.open("a", encoding="utf-8") as handle:
                handle.write('{"turn_id": "t-2026')
            prune(load_settings(), today=TODAY)
            self.assertEqual(
                traces.indexed_turn_ids(),
                [kept.name[len(trace_sink.TURN_PREFIX):-len(".jsonl")]],
            )


class PruneFailureTest(unittest.TestCase):
    """清理失败只降级为告警——一次已经答完的对话不该被保留策略毁掉。"""

    def test_a_locked_directory_degrades_to_a_warning(self) -> None:
        """只读目录 / 锁超时。Windows 上 chmod 对目录无效，所以直接注入 PermissionError。"""
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_TURNS="1") as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            traces.add(TODAY)
            with mock.patch.object(
                trace_sink, "_prune_locked", side_effect=PermissionError("拒绝访问")
            ):
                with self.assertLogs("fithealth", level="WARNING") as logs:
                    result = prune(load_settings(), today=TODAY)
            self.assertEqual(result, PruneResult())
            self.assertEqual(len(traces.turn_files()), 2, "失败的 prune 不该删掉任何东西")
            self.assertTrue(any("保留策略未能执行" in line for line in logs.output))
            # 权限恢复之后，下一次 prune 照常工作。
            self.assertEqual(prune(load_settings(), today=TODAY).turns_removed, 1)

    def test_one_undeletable_file_does_not_stop_the_others(self) -> None:
        with trace_env(FITHEALTH_TRACE="on", FITHEALTH_TRACE_MAX_TURNS="1") as root:
            traces = fixture_in(root)
            stubborn = traces.add(TODAY)
            traces.add(TODAY)
            traces.add(TODAY)
            real_unlink = Path.unlink

            def selective(self, *args, **kwargs):
                if self.name == stubborn.name:
                    raise PermissionError("文件正被占用")
                return real_unlink(self, *args, **kwargs)

            with mock.patch.object(Path, "unlink", selective):
                with self.assertLogs("fithealth", level="WARNING"):
                    result = prune(load_settings(), today=TODAY)
            self.assertEqual(result.turns_removed, 1)
            self.assertIn(
                stubborn.relative_to(traces.directory).as_posix(), traces.turn_files()
            )

    def test_prune_is_a_no_op_while_trace_is_disabled(self) -> None:
        """关掉 trace 之后不该继续删已有产物——那属于 `/data/reset` 的职责。"""
        with trace_env(FITHEALTH_TRACE_MAX_TURNS="1") as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            traces.add(TODAY)
            self.assertEqual(prune(load_settings(), today=TODAY), PruneResult())
            self.assertEqual(len(traces.turn_files()), 2)


class TraceStoreClearTest(unittest.TestCase):
    """TRACE-06 的清理闭环：删净、幂等、不误伤、绝不抛。"""

    def _populated(self, root: Path) -> TraceFixture:
        """造齐四类产物：回合文件、index、锁文件、原子写残片。"""
        traces = fixture_in(root)
        traces.add(TODAY)
        traces.add(TODAY - timedelta(days=1))
        (traces.directory / f"{trace_sink.INDEX_NAME}.lock").write_text("\0", encoding="utf-8")
        (traces.directory / "index.jsonl.1234.abcdef.tmp").write_text("half", encoding="utf-8")
        (traces.directory / f"{TODAY.isoformat()}" / "turn-x.jsonl.99.ff.tmp").write_text(
            "half", encoding="utf-8"
        )
        (traces.directory / ".write-probe-4321").write_text("", encoding="utf-8")
        return traces

    def test_clear_removes_every_artefact_including_the_index_and_lock(self) -> None:
        """`index.jsonl.lock` 与 `.tmp` 残片是最容易漏的两类——漏了就等于目录没清干净。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = self._populated(root)
            before = sorted(
                path.name for path in traces.directory.rglob("*") if path.is_file()
            )
            # 先确认这几类真的造出来了，否则本用例会静默失去覆盖。
            self.assertEqual(
                sorted(name for name in before if not name.startswith(trace_sink.TURN_PREFIX)),
                [".write-probe-4321", "index.jsonl", "index.jsonl.1234.abcdef.tmp",
                 "index.jsonl.lock"],
            )
            removed = TraceStore().clear()
            self.assertEqual(removed, len(before), before)
            self.assertEqual(traces.everything(), [])
            self.assertTrue(traces.directory.is_dir(), "目录本身该留着")

    def test_clear_is_idempotent(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            self._populated(root)
            store = TraceStore()
            self.assertGreater(store.clear(), 0)
            self.assertEqual(store.clear(), 0)

    def test_clear_works_while_trace_is_disabled(self) -> None:
        """关掉 trace 之前留下的产物同样要能删——否则"删除全部数据"会漏一整个目录。"""
        with trace_env() as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            self.assertFalse(load_settings().enabled)
            self.assertGreater(TraceStore().clear(), 0)
            self.assertEqual(traces.turn_files(), [])

    def test_clear_leaves_unrelated_files_alone(self) -> None:
        """`FITHEALTH_TRACE_DIR` 可以指向任何地方，别人的东西不该被顺手抹掉。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = self._populated(root)
            keeper = traces.directory / "notes.md"
            keeper.write_text("keep me", encoding="utf-8")
            nested = traces.directory / "not-a-day"
            nested.mkdir()
            (nested / "turn-t-20260905-120000-aaaaaa.jsonl").write_text("x", encoding="utf-8")
            TraceStore().clear()
            self.assertEqual(
                traces.everything(),
                ["not-a-day", "not-a-day/turn-t-20260905-120000-aaaaaa.jsonl", "notes.md"],
            )

    def test_clear_on_a_missing_directory_is_a_no_op(self) -> None:
        with trace_env():
            self.assertEqual(TraceStore().clear(), 0)

    def test_clear_never_raises_when_a_file_cannot_be_deleted(self) -> None:
        """它挂在恢复备份的回调上：抛异常会让一次**已经生效**的恢复被报成失败。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = self._populated(root)
            with mock.patch.object(Path, "unlink", side_effect=PermissionError("占用中")):
                with self.assertLogs("fithealth", level="WARNING"):
                    self.assertEqual(TraceStore().clear(), 0)
            self.assertGreater(len(traces.turn_files()), 0, "一个都没删掉，但也没抛")

    def test_clear_refuses_a_symlinked_directory(self) -> None:
        """一条指向别处的软链会让"只删数据目录内的东西"这条承诺静默失效。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            real = root / "elsewhere"
            real.mkdir()
            (real / "keep.txt").write_text("x", encoding="utf-8")
            link = root / "traces"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("这个环境不允许创建符号链接（Windows 需要开发者模式）")
            self.assertEqual(TraceStore().clear(), 0)
            self.assertTrue((real / "keep.txt").is_file())


class ResetAndRestoreWiringTest(unittest.TestCase):
    """两条治理路径：「删除全部数据」与「恢复备份之后」。"""

    def _steps(self):
        from fithealth_agent.routes import maintenance_ops

        return maintenance_ops._reset_steps()

    def test_the_two_diagnostic_artefacts_are_the_last_two_steps(self) -> None:
        """工具输出与执行轨迹都是**别处数据的副本/诊断产物**：不被任何东西引用，
        所以排在最后；两者之间的先后无所谓，但都必须在。"""
        keys = [key for key, _label, _action in self._steps()]
        self.assertEqual(keys[-2:], ["tool_output_removed", "traces_removed"])
        self.assertEqual(len(keys), 12)

    def test_the_trace_step_goes_through_the_replaceable_dependency(self) -> None:
        """必须是 `deps.trace_store.clear`：绑到别的实例上，测试替换就静默失效。"""
        action = dict((key, action) for key, _label, action in self._steps())["traces_removed"]
        self.assertEqual(action, deps.trace_store.clear)

    def test_the_trace_step_deletes_the_files(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            traces.add(TODAY - timedelta(days=1))
            action = dict(
                (key, action) for key, _label, action in self._steps()
            )["traces_removed"]
            self.assertEqual(action(), 3)  # 两个回合文件 + index
            self.assertEqual(traces.turn_files(), [])

    def test_memory_revalidation_stays_the_first_restore_callback(self) -> None:
        """`maintenance_ops.import_backup` 按**位置**取 `callback_results[0]` 当作记忆库
        重校验结果。回调顺序反了，响应里的 `memory_store_revalidated` 就会变成一个数字。"""
        callbacks = deps.backup_service._on_restored
        self.assertEqual(
            [getattr(callback, "__name__", "") for callback in callbacks],
            ["revalidate", "_clear_traces_after_restore"],
        )

    def test_the_restore_callback_resolves_the_store_at_call_time(self) -> None:
        """写成函数而不是把绑定方法塞进回调列表——否则替换 `deps.trace_store` 无效。"""
        replacement = mock.Mock(clear=mock.Mock(return_value=7))
        with mock.patch.object(deps, "trace_store", replacement):
            self.assertEqual(deps._clear_traces_after_restore(), 7)
        replacement.clear.assert_called_once_with()

    def test_a_real_restore_clears_the_traces(self) -> None:
        """恢复回来的是**恢复之前**那份数据；旧轨迹留着会与新数据矛盾。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            data_dir = root / "restore-target"
            data_dir.mkdir()
            (data_dir / "daily_records.json").write_text("[]", encoding="utf-8")
            service = LocalBackupService(
                data_dir, on_restored=[deps._clear_traces_after_restore]
            )
            archive = service.export_bytes()
            result = service.restore(archive)
            self.assertTrue(result["restore_callbacks"], result)
            self.assertEqual(traces.turn_files(), [])

    def test_a_restore_still_succeeds_when_the_traces_cannot_be_cleared(self) -> None:
        """清不掉 trace 不该让一次**已经生效**的恢复被报成失败。

        故障注入打在 `_unlink_matching` 上（而不是 `clear` 本身）：要验证的正是
        `clear` 的"绝不抛"——它把底层的 OSError 咽下去并返回 0。
        """
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            traces.add(TODAY)
            data_dir = root / "restore-target"
            data_dir.mkdir()
            (data_dir / "daily_records.json").write_text("[]", encoding="utf-8")
            service = LocalBackupService(
                data_dir, on_restored=[deps._clear_traces_after_restore]
            )
            archive = service.export_bytes()
            with mock.patch.object(
                trace_sink, "_unlink_matching", side_effect=PermissionError("占用中")
            ):
                with self.assertLogs("fithealth", level="WARNING"):
                    result = service.restore(archive)
            self.assertEqual(result["restore_callbacks"], [0])
            self.assertEqual(len(traces.turn_files()), 1, "没删掉，但恢复照样成功")


#: 一条同时踩四个坑的 payload：标签闭合、属性闭合、危险 URL、控制字符。
#: 不可见字符用 `chr()` 拼，不写字面量——写进源码就看不见，别的编辑器还会
#: "顺手修好"它们，而修好之后这条用例就不再覆盖那种输入了。
BIDI_OVERRIDE = chr(0x202E)
LINE_SEPARATOR = chr(0x2028)
NASTY = (
    '</pre><script>alert(1)</script>'
    '" onmouseover="alert(2)'
    "' onfocus='alert(3)"
    ' javascript:alert(4) &lt;script&gt; '
    + chr(0x00) + chr(0x1B) + BIDI_OVERRIDE + LINE_SEPARATOR
)


class TraceReportEscapingTest(unittest.TestCase):
    """TRACE-08 不复发。判据是"渲染出的文本里没有能改变结构的字符"。"""

    def test_no_structural_character_survives_unescaped(self) -> None:
        """比逐个 payload 断言更强：`<`、`>`、引号一个都不许原样出现。

        这一条同时覆盖文本节点、属性、危险 URL 三种上下文——它们的共同前提就是
        "攻击者能写出一个未转义的定界符"。
        """
        rendered = CLI._safe(NASTY)
        for forbidden in ("<", ">", '"', "'"):
            with self.subTest(character=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_double_encoding_is_not_decoded(self) -> None:
        """输入里本来就有的 `&lt;` 必须变成 `&amp;lt;`，否则浏览器会解出一层来。"""
        self.assertEqual(CLI._safe("&lt;script&gt;"), "&amp;lt;script&amp;gt;")

    def test_control_characters_become_visible_escapes(self) -> None:
        """BiDi 覆写能把一行文本整段反转显示；控制字符能让终端错位。"""
        raw = 'a' + chr(0x00) + 'b' + chr(0x1B) + 'c' + LINE_SEPARATOR + 'd' + BIDI_OVERRIDE
        self.assertEqual(CLI._safe(raw), r'a\x00b\x1Bc\u2028d\u202E')

    def test_tabs_and_newlines_are_kept(self) -> None:
        """它们是 `<pre>` 里的正常排版，替换掉只会让报告变得难读。"""
        self.assertEqual(CLI._safe("a\tb\nc"), "a\tb\nc")

    def test_the_rendered_report_has_no_executable_or_remote_content(self) -> None:
        events = [
            {"v": 1, "turn_id": "t-20260905-120001-000001", "seq": 1, "kind": "turn_start",
             "span": "/chat", "payload": {"route": "/chat", "message": NASTY}},
        ]
        report = CLI.render_html("t-20260905-120001-000001", Path("turn.jsonl"), events)
        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertIn("&lt;/pre&gt;&lt;script&gt;", report)
        # 报告必须自包含：没有 script、没有远程样式、没有图片、没有 @import。
        for forbidden in ("<script", "src=", "href=", "url(", "@import", "<iframe"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, report)
        self.assertIn("default-src 'none'", report)


FAILED_PLAN_EVENTS = [
    {"v": 1, "seq": 1, "kind": "turn_start", "span": "/chat", "payload": {"route": "/chat"}},
    {"v": 1, "seq": 2, "kind": "gate", "span": "plan_validation",
     "payload": {"outcome": "failed", "violation_count": 2}},
    {"v": 1, "seq": 3, "kind": "turn_end", "span": "/chat",
     "payload": {"status": "ok", "status_code": 200}},
]


class TraceReportCliTest(unittest.TestCase):
    """CLI 的三条子命令，以及"只读 + 输入不接受路径 + 输出不覆盖"这三条约束。"""

    def run_cli(self, *argv: str) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(CLI.main(list(argv)), 0)
        return buffer.getvalue()

    def test_list_shows_the_newest_last(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            old = traces.add(TODAY - timedelta(days=2))
            new = traces.add(TODAY)
            output = self.run_cli("--list", "--last", "5")
            lines = [line for line in output.splitlines() if line.startswith("t-")]
            self.assertEqual(
                [line.split()[0] for line in lines],
                [path.stem[len(trace_sink.TURN_PREFIX):] for path in (old, new)],
            )
            self.assertIn("共 2 个回合", output)

    def test_last_limits_the_listing(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            for _ in range(4):
                traces.add(TODAY)
            output = self.run_cli("--list", "--last", "2")
            self.assertEqual(len([line for line in output.splitlines() if line.startswith("t-")]), 2)

    def test_turn_prints_the_events_in_seq_order(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            path = traces.add(TODAY, events=FAILED_PLAN_EVENTS)
            turn_id = path.stem[len(trace_sink.TURN_PREFIX):]
            output = self.run_cli("--turn", turn_id)
            kinds = [
                match.group(1) for match in
                (re.match(r"^\s*\d+ (\w+)", line) for line in output.splitlines())
                if match is not None
            ]
            self.assertEqual(kinds, ["turn_start", "gate", "turn_end"])
            self.assertIn("共 3 个事件", output)

    def test_failed_plans_finds_the_failing_turn_and_respects_since(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            traces.add(TODAY)  # 正常回合，不该出现在结果里
            # `started_at` 相对**真实现在**给，于是 --since 的窗口断言不依赖机器时钟。
            failing = traces.add(
                TODAY,
                events=FAILED_PLAN_EVENTS,
                started_at=datetime.now().astimezone() - timedelta(days=30),
            )
            turn_id = failing.stem[len(trace_sink.TURN_PREFIX):]
            self.assertIn(turn_id, self.run_cli("--failed-plans"))
            self.assertIn(turn_id, self.run_cli("--failed-plans", "--since", "60d"))
            self.assertIn("共 0 个回合", self.run_cli("--failed-plans", "--since", "7d"))

    def test_an_invalid_since_is_rejected(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            fixture_in(root).add(TODAY)
            with self.assertRaises(SystemExit):
                CLI.main(["--failed-plans", "--since", "上周"])

    def test_a_turn_id_that_looks_like_a_path_is_rejected(self) -> None:
        """`--turn` 只收 id。这是"输入不接受路径"那条约束的落地点。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            fixture_in(root).add(TODAY)
            for candidate in ("../../etc/passwd", "index.jsonl", "t-bad", "/absolute/path"):
                with self.subTest(turn=candidate):
                    with self.assertRaises(SystemExit):
                        CLI.main(["--turn", candidate])

    def test_a_missing_directory_is_reported_instead_of_crashing(self) -> None:
        with trace_env():
            self.assertEqual(self.run_cli("--list").count("trace 目录不存在"), 1)

    def test_the_html_report_is_not_silently_overwritten(self) -> None:
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            turn_id = traces.add(TODAY).stem[len(trace_sink.TURN_PREFIX):]
            target = root / "out" / "report.html"
            self.run_cli("--turn", turn_id, "--html", str(target))
            first = target.read_text(encoding="utf-8")
            with self.assertRaises(SystemExit):
                CLI.main(["--turn", turn_id, "--html", str(target)])
            self.assertEqual(target.read_text(encoding="utf-8"), first)
            self.run_cli("--turn", turn_id, "--html", str(target), "--force")

    def test_the_report_may_not_be_written_into_the_trace_directory(self) -> None:
        """报告是二次产物。写进 trace 目录会被保留策略与清理当成自己的东西处理。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            turn_id = traces.add(TODAY).stem[len(trace_sink.TURN_PREFIX):]
            for target in (
                traces.directory / "report.html",
                traces.directory / TODAY.isoformat() / "report.html",
            ):
                with self.subTest(target=target.name):
                    with self.assertRaises(SystemExit):
                        CLI.main(["--turn", turn_id, "--html", str(target)])

    def test_the_cli_never_touches_the_trace_directory(self) -> None:
        """只读是硬约束：三条子命令跑完，目录里的文件名、大小、mtime 都不能变。"""
        with trace_env(FITHEALTH_TRACE="on") as root:
            traces = fixture_in(root)
            turn_id = traces.add(TODAY, events=FAILED_PLAN_EVENTS).stem[
                len(trace_sink.TURN_PREFIX):
            ]

            def snapshot() -> list[tuple[str, int, int]]:
                return sorted(
                    (path.relative_to(traces.directory).as_posix(),
                     path.stat().st_size, path.stat().st_mtime_ns)
                    for path in traces.directory.rglob("*")
                    if path.is_file()
                )

            before = snapshot()
            self.run_cli("--list")
            self.run_cli("--turn", turn_id)
            self.run_cli("--failed-plans", "--since", "30d")
            self.run_cli("--turn", turn_id, "--html", str(root / "out.html"))
            self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
