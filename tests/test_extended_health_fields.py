"""扩展 Garmin wellness 解析范围后的回归测试。

原先包里躺着但一条都没入库的四类数据：

* **静息代谢率** —— `daily_health_summary` 早就有 `resting_calories` / `total_calories`
  两列却 16 天全空，因为读取方去 `monitoring` 里找 `resting_calories`/`bmr_calories`，
  而这个数其实在 `monitoring_info.resting_metabolic_rate` 里，而那条消息整个没被处理。
* **静息心率** —— Garmin 私有消息 `unknown_211`，**逆向推断**。证据记在
  `health_importer._RESTING_HR_MESSAGE` 的注释里；关键一条是 2026-08-14 取到 56，
  与那天睡眠 CSV 的「静息心率 56 bpm」精确一致。
* **强度分钟** —— `monitoring.moderate_activity_time` / `vigorous_activity_time`。
  **是区间增量而不是累计值**（实测同一天出现 60s/180s/60s/240s，累计量不可能回落），
  所以必须 SUM；而且它们不与任何活动字段同现，挂不到 `daily_activity_observations` 上。
* **设备 UTC 偏移** —— `monitoring_info` / `local_time` 里 UTC 与本地墙钟之差。
  实测 15 份真实包全是 +480，存下来是为了可见可核对。

另修一处口径错误：`get_sleep` 的 FIT 兜底把**清醒时间也算进睡眠时长**。睡眠 CSV 的
「睡眠时长」是深+浅+REM（6时48分 = 101+222+85，清醒 43 分钟不计），而 FIT 兜底用
同一个键名给出全部阶段之和。真实数据里 2026-08-21 因此多报 156 分钟。
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


#: 按文件路径加载 health_store / health_importer 时，`health_importer` 里的
#: `from .health_store import HealthStore` 需要 `sys.modules["fithealth_agent"]`
#: 带 `__path__`。塞一个桩包即可绕开 `__init__.py` 的急切 LLM 栈导入（ARCH-02）。
#:
#: **但桩包必须还回去**：留在 sys.modules 里的话，之后任何 `import main` 都会在
#: `from fithealth_agent import create_fithealth_agent` 处炸掉——桩包没有那个属性。
#: 实测在某些随机执行顺序下会让另外两个文件的 setUpClass 直接 ERROR。
_LOADED_MODULE_NAMES = (
    "fithealth_agent.health_importer",
    "fithealth_agent.health_store",
    "fithealth_agent",
)


def load_health_modules():
    saved = {name: sys.modules.get(name) for name in _LOADED_MODULE_NAMES}
    package = sys.modules.get("fithealth_agent")
    if package is None or not hasattr(package, "__path__"):
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
    for name, filename in (
        ("fithealth_agent.health_store", "health_store.py"),
        ("fithealth_agent.health_importer", "health_importer.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, PACKAGE_DIR / filename)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return (
        sys.modules["fithealth_agent.health_store"],
        sys.modules["fithealth_agent.health_importer"],
        saved,
    )


def restore_modules(saved: dict[str, object]) -> None:
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class _Message:
    def __init__(self, name: str, fields: dict) -> None:
        self.type = types.SimpleNamespace(name=name)
        self.fields = fields


BASE = datetime(2026, 8, 21, 4, 0, tzinfo=timezone.utc)


class ParseNewFieldsTest(unittest.TestCase):
    """解析层：新字段被读出来，且解读正确。"""

    def setUp(self) -> None:
        self.store_module, self.importer, saved = load_health_modules()
        self.addCleanup(restore_modules, saved)

    def parse(self, messages: list[_Message], kind: str = "monitoring_b") -> dict:
        fake = types.SimpleNamespace(
            type=types.SimpleNamespace(name=kind), serial_number=1, messages=messages
        )
        with mock.patch.object(self.importer.fitfile.file, "File", lambda path: fake):
            return self.importer._parse_fit_source("sample_WELLNESS.fit", b"x" * 32)

    # ---- 静息代谢率 ----
    def test_resting_metabolic_rate_comes_from_monitoring_info(self) -> None:
        parsed = self.parse([
            _Message("monitoring_info", {
                "timestamp": BASE,
                "local_timestamp": datetime(2026, 8, 21, 12, 0),
                "resting_metabolic_rate": 2230.0,
            })
        ])
        entry = parsed["device_metrics"][0]
        self.assertEqual(entry["resting_metabolic_rate"], 2230.0)
        self.assertIn("resting_metabolic_rate", parsed["data_types"])

    def test_an_implausible_metabolic_rate_is_rejected(self) -> None:
        for value in (0, 120, 99999):
            with self.subTest(value=value):
                parsed = self.parse([
                    _Message("monitoring_info", {"timestamp": BASE, "resting_metabolic_rate": value})
                ])
                rates = [item.get("resting_metabolic_rate") for item in parsed["device_metrics"]]
                self.assertNotIn(value, rates)

    # ---- 静息心率 ----
    def test_resting_heart_rate_is_read_from_the_private_message(self) -> None:
        parsed = self.parse([
            _Message("unknown_211", {"timestamp": BASE, "unknown_0": 57.0, "unknown_1": 55.0})
        ])
        entry = parsed["device_metrics"][0]
        self.assertEqual(entry["resting_heart_rate"], 55)
        self.assertEqual(entry["resting_heart_rate_baseline"], 57)
        self.assertIn("resting_heart_rate", parsed["data_types"])

    def test_out_of_range_resting_heart_rate_is_dropped(self) -> None:
        """逆向出来的字段必须带范围闸门——猜错字段也不该把垃圾写进库。"""
        parsed = self.parse([
            _Message("unknown_211", {"timestamp": BASE, "unknown_0": 3.0, "unknown_1": 900.0})
        ])
        self.assertEqual(parsed["device_metrics"], [])

    def test_a_boolean_is_not_mistaken_for_a_heart_rate(self) -> None:
        parsed = self.parse([
            _Message("unknown_211", {"timestamp": BASE, "unknown_0": True, "unknown_1": True})
        ])
        self.assertEqual(parsed["device_metrics"], [])

    # ---- 强度分钟 ----
    def test_intensity_minutes_are_collected_even_without_activity_fields(self) -> None:
        """它们不与 steps/active_calories 同现，所以 activity_observations 那条路走不通。"""
        parsed = self.parse([
            _Message("monitoring", {
                "timestamp": BASE,
                "moderate_activity_time": datetime(2026, 1, 1, 0, 3).time(),
                "vigorous_activity_time": datetime(2026, 1, 1, 0, 1).time(),
            })
        ])
        self.assertEqual(parsed["activity_observations"], [])
        entry = parsed["intensity_observations"][0]
        self.assertEqual(entry["moderate_activity_s"], 180.0)
        self.assertEqual(entry["vigorous_activity_s"], 60.0)
        self.assertIn("intensity", parsed["data_types"])

    def test_the_intensity_level_is_kept_on_its_own(self) -> None:
        parsed = self.parse([_Message("monitoring", {"timestamp": BASE, "intensity": 3.0})])
        self.assertEqual(parsed["intensity_observations"][0]["intensity_level"], 3)
        self.assertIsNone(parsed["intensity_observations"][0]["moderate_activity_s"])

    # ---- 时区偏移 ----
    def test_the_device_utc_offset_is_derived_from_the_local_clock(self) -> None:
        parsed = self.parse([
            _Message("monitoring_info", {
                "timestamp": BASE, "local_timestamp": datetime(2026, 8, 21, 12, 0),
            })
        ])
        self.assertEqual(parsed["device_metrics"][0]["utc_offset_minutes"], 480)

    def test_a_metrics_file_yields_the_offset_from_local_time(self) -> None:
        """4 个 METRICS 文件原先一行数据都没产出；至少偏移是能确认的。"""
        parsed = self.parse(
            [_Message("local_time", {
                "timestamp": BASE, "local_timestamp": datetime(2026, 8, 21, 12, 0),
            })],
            kind="metrics",
        )
        self.assertEqual(parsed["device_metrics"][0]["utc_offset_minutes"], 480)

    def test_an_absurd_offset_is_refused(self) -> None:
        parsed = self.parse([
            _Message("monitoring_info", {
                "timestamp": BASE, "local_timestamp": datetime(2030, 1, 1, 0, 0),
            })
        ])
        self.assertEqual(parsed["device_metrics"], [])

    def test_the_local_clock_never_goes_through_utc_datetime(self) -> None:
        """`monitoring_info.local_timestamp` 是真正的本地墙钟（BUG-15 证过），
        把它当 UTC 会算出 +16h 的偏移。"""
        parsed = self.parse([
            _Message("monitoring_info", {
                "timestamp": BASE, "local_timestamp": datetime(2026, 8, 21, 12, 0),
                "resting_metabolic_rate": 2230.0,
            })
        ])
        # 时间戳本身仍按 UTC 解释（04:00 UTC → 12:00 北京）
        self.assertEqual(parsed["device_metrics"][0]["timestamp_local"], "2026-08-21T12:00:00+08:00")
        self.assertEqual(parsed["device_metrics"][0]["utc_offset_minutes"], 480)


class SummaryAggregationTest(unittest.TestCase):
    """日汇总：新列的聚合口径。"""

    DAY = "2026-08-21"

    def setUp(self) -> None:
        self.store_module, self.importer, saved = load_health_modules()
        self.addCleanup(restore_modules, saved)
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.store = self.store_module.HealthStore(root / "health.db", root / "raw")

    def stamp(self, minute: int) -> dict:
        moment = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
        return {
            "timestamp_utc": moment.isoformat(),
            "timestamp_local": moment.astimezone(self.store_module.BEIJING).isoformat(),
            "local_date": self.DAY,
        }

    def save(self, tag: str = "a", **payload) -> None:
        self.store.save_import({
            "id": str(uuid4()),
            "sha256": (tag * 64)[:64],
            "filename": f"{tag}-{self.DAY}.zip",
            "kind": "wellness_zip",
            "status": "imported",
            "date_hint": self.DAY,
            "warnings": [],
            "raw_path": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sleep": None,
            "sources": [{
                "id": str(uuid4()),
                "filename": f"{tag}_WELLNESS.fit",
                "kind": "wellness",
                "sha256": (tag * 64)[:64],
                "earliest_utc": None, "latest_utc": None,
                "device_serial": "device", "warnings": [],
                "record_count": 1, "message_counts": {}, "data_types": [],
                "heart_rates": payload.get("heart_rates", []),
                "metric_samples": [],
                "activity_observations": payload.get("activity_observations", []),
                "device_metrics": payload.get("device_metrics", []),
                "intensity_observations": payload.get("intensity_observations", []),
                "hrv_statuses": [], "sleep_stages": payload.get("sleep_stages", []),
            }],
        })

    def summary(self, column: str):
        with self.store._connection() as connection:
            row = connection.execute(
                f"SELECT {column} AS v FROM daily_health_summary WHERE date = ?", (self.DAY,)
            ).fetchone()
        return row["v"] if row else None

    def test_intensity_minutes_are_summed_not_maxed(self) -> None:
        """字段是区间增量：实测同一天出现 60s/180s/60s/240s，取 MAX 会只报 4 分钟。"""
        self.save(intensity_observations=[
            {**self.stamp(0), "moderate_activity_s": 60.0, "vigorous_activity_s": None,
             "intensity_level": None},
            {**self.stamp(1), "moderate_activity_s": 180.0, "vigorous_activity_s": 60.0,
             "intensity_level": None},
            {**self.stamp(2), "moderate_activity_s": 60.0, "vigorous_activity_s": None,
             "intensity_level": None},
        ])
        self.assertEqual(self.summary("moderate_activity_min"), 5.0)
        self.assertEqual(self.summary("vigorous_activity_min"), 1.0)
        # Garmin/WHO 口径：高强度双倍
        self.assertEqual(self.summary("intensity_minutes"), 7.0)

    def test_an_overlapping_reimport_does_not_double_intensity_minutes(self) -> None:
        observations = [
            {**self.stamp(index), "moderate_activity_s": 60.0, "vigorous_activity_s": None,
             "intensity_level": None}
            for index in range(10)
        ]
        self.save("a", intensity_observations=observations)
        self.save("b", intensity_observations=observations)
        self.assertEqual(self.summary("moderate_activity_min"), 10.0)

    def test_resting_calories_prefer_the_device_metabolic_rate(self) -> None:
        self.save(
            device_metrics=[{**self.stamp(0), "resting_metabolic_rate": 2230.0}],
            activity_observations=[
                {**self.stamp(1), "activity_type": "walking", "steps": 1000,
                 "active_calories": 187.0},
            ],
        )
        self.assertEqual(self.summary("resting_metabolic_rate"), 2230.0)
        self.assertEqual(self.summary("resting_calories"), 2230.0)
        # 设备没直接报总热量时按"静息 + 活动"推导，这也是 Garmin 自己的定义
        self.assertEqual(self.summary("total_calories"), 2417.0)

    def test_total_calories_stay_empty_when_only_one_half_is_known(self) -> None:
        """别把缺失当 0 用——那会给出一个看着像真值的错数。"""
        self.save(device_metrics=[{**self.stamp(0), "resting_metabolic_rate": 2230.0}])
        self.assertEqual(self.summary("resting_calories"), 2230.0)
        self.assertIsNone(self.summary("total_calories"))

    def test_the_latest_resting_heart_rate_of_the_day_wins(self) -> None:
        """每天开头往往还留着前一天的值（实测 08-21 00:07 是 61，当天真实值 55）。"""
        self.save(device_metrics=[
            {**self.stamp(0), "resting_heart_rate": 61, "resting_heart_rate_baseline": 58},
            {**self.stamp(300), "resting_heart_rate": 55, "resting_heart_rate_baseline": 57},
        ])
        self.assertEqual(self.summary("resting_heart_rate"), 55)
        self.assertEqual(self.summary("resting_heart_rate_baseline"), 57)

    def test_each_column_takes_its_own_latest_non_null_value(self) -> None:
        """RMR 来自 monitoring_info、静息心率来自 unknown_211，时间戳不同，一行里
        通常只有一列有值——所以不能整行取最新。"""
        self.save(device_metrics=[
            {**self.stamp(0), "resting_metabolic_rate": 2230.0},
            {**self.stamp(5), "resting_heart_rate": 55},
        ])
        self.assertEqual(self.summary("resting_metabolic_rate"), 2230.0)
        self.assertEqual(self.summary("resting_heart_rate"), 55)

    def test_a_day_with_only_device_metrics_still_gets_a_summary_row(self) -> None:
        self.save(device_metrics=[{**self.stamp(0), "resting_heart_rate": 55}])
        self.assertEqual(self.summary("resting_heart_rate"), 55)

    def test_deleting_the_import_removes_the_summary_row(self) -> None:
        """`delete_import` 必须把新表也列进"受影响日期"。

        某一天的数据可能**只**来自 device_daily_metrics（跨零点那天常常只剩一条
        静息心率）；漏掉的话日汇总行删不掉，界面上留下一个没有来源的幽灵日期。
        """
        self.save(device_metrics=[{**self.stamp(0), "resting_heart_rate": 55}])
        self.store.delete_import(self.store.list_imports()[0]["id"])
        self.assertIsNone(self.summary("resting_heart_rate"))

    def test_deleting_an_intensity_only_import_removes_the_summary_row(self) -> None:
        self.save(intensity_observations=[
            {**self.stamp(0), "moderate_activity_s": 60.0, "vigorous_activity_s": None,
             "intensity_level": None},
        ])
        self.store.delete_import(self.store.list_imports()[0]["id"])
        self.assertIsNone(self.summary("intensity_minutes"))


class SleepDurationTest(unittest.TestCase):
    """FIT 睡眠兜底的时长口径与夜间心率。"""

    DAY = "2026-08-21"

    def setUp(self) -> None:
        self.store_module, self.importer, saved = load_health_modules()
        self.addCleanup(restore_modules, saved)
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.store = self.store_module.HealthStore(root / "health.db", root / "raw")
        self.beijing = self.store_module.BEIJING

    def build(self) -> None:
        """深 30 + 浅 60 + REM 20 分钟睡眠，中间夹 40 分钟清醒。"""
        start = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)
        plan = [("deep_sleep", 30), ("light_sleep", 60), ("awake", 40), ("rem_sleep", 20)]
        stages = []
        moment = start
        for stage, minutes in plan:
            stages.append({
                "sleep_date": self.DAY,
                "timestamp_utc": moment.isoformat(),
                "timestamp_local": moment.astimezone(self.beijing).isoformat(),
                "stage": stage,
                "duration_s": minutes * 60,
            })
            moment += timedelta(minutes=minutes)
        # 睡眠窗口内每分钟一条心率：前 90 分钟 55，之后 65
        heart_rates = []
        for index in range(150):
            instant = start + timedelta(minutes=index)
            heart_rates.append({
                "timestamp_utc": instant.isoformat(),
                "timestamp_local": instant.astimezone(self.beijing).isoformat(),
                "local_date": instant.astimezone(self.beijing).date().isoformat(),
                "bpm": 55 if index < 90 else 65,
            })
        self.store.save_import({
            "id": str(uuid4()), "sha256": "a" * 64, "filename": f"{self.DAY}.zip",
            "kind": "wellness_zip", "status": "imported", "date_hint": self.DAY,
            "warnings": [], "raw_path": None,
            "created_at": datetime.now(timezone.utc).isoformat(), "sleep": None,
            "sources": [{
                "id": str(uuid4()), "filename": "SLEEP.fit", "kind": "sleep",
                "sha256": "b" * 64, "earliest_utc": None, "latest_utc": None,
                "device_serial": "d", "warnings": [], "record_count": 1,
                "message_counts": {}, "data_types": [],
                "heart_rates": heart_rates, "metric_samples": [],
                "activity_observations": [], "device_metrics": [],
                "intensity_observations": [], "hrv_statuses": [], "sleep_stages": stages,
                "sleep_sessions": [{
                    "sleep_date": self.DAY,
                    "bed_start_utc": start.isoformat(),
                    "bed_end_utc": moment.isoformat(),
                    "bed_start_local": start.astimezone(self.beijing).isoformat(),
                    "bed_end_local": moment.astimezone(self.beijing).isoformat(),
                    "time_in_bed_min": 150,
                    "awake_min": 40,
                    "score": None,
                    "restlessness": None,
                }],
            }],
        })

    def test_sleep_duration_excludes_awake_time(self) -> None:
        """睡眠 CSV 的「睡眠时长」是深+浅+REM，清醒不计——两条路径必须同口径。"""
        self.build()
        sleep = self.store.get_sleep(self.DAY)
        self.assertEqual(sleep["duration_min"], 110.0)   # 旧实现会给 150
        self.assertEqual(sleep["awake_min"], 40.0)
        self.assertEqual(sleep["time_in_bed_min"], 150.0)
        self.assertEqual(
            sleep["duration_min"],
            sleep["device_stage_deep_min"]
            + sleep["device_stage_light_min"]
            + sleep["device_stage_rem_min"],
        )

    def test_night_heart_rate_is_computed_from_the_sleep_window(self) -> None:
        """睡眠分数与质量只有 CSV 才有，但夜间心率手上已经有全部原料。"""
        self.build()
        sleep = self.store.get_sleep(self.DAY)
        # 90 条 55 + 60 条 65（窗口含首尾各一分钟，允许 ±1 条）
        self.assertAlmostEqual(sleep["night_avg_hr"], 59.0, delta=0.5)
        self.assertEqual(sleep["night_min_hr"], 55)
        self.assertGreater(sleep["night_hr_samples"], 100)

    def test_night_heart_rate_is_none_without_heart_rate_data(self) -> None:
        start = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)
        self.store.save_import({
            "id": str(uuid4()), "sha256": "c" * 64, "filename": "x.zip",
            "kind": "wellness_zip", "status": "imported", "date_hint": self.DAY,
            "warnings": [], "raw_path": None,
            "created_at": datetime.now(timezone.utc).isoformat(), "sleep": None,
            "sources": [{
                "id": str(uuid4()), "filename": "s.fit", "kind": "sleep", "sha256": "d" * 64,
                "earliest_utc": None, "latest_utc": None, "device_serial": "d",
                "warnings": [], "record_count": 1, "message_counts": {}, "data_types": [],
                "heart_rates": [], "metric_samples": [], "activity_observations": [],
                "device_metrics": [], "intensity_observations": [], "hrv_statuses": [],
                "sleep_stages": [{
                    "sleep_date": self.DAY, "timestamp_utc": start.isoformat(),
                    "timestamp_local": start.astimezone(self.beijing).isoformat(),
                    "stage": "light_sleep", "duration_s": 3600,
                }],
                "sleep_sessions": [{
                    "sleep_date": self.DAY,
                    "bed_start_utc": start.isoformat(),
                    "bed_end_utc": (start + timedelta(hours=1)).isoformat(),
                    "bed_start_local": start.astimezone(self.beijing).isoformat(),
                    "bed_end_local": (start + timedelta(hours=1)).astimezone(self.beijing).isoformat(),
                    "time_in_bed_min": 60,
                    "awake_min": 0,
                    "score": None,
                    "restlessness": None,
                }],
            }],
        })
        sleep = self.store.get_sleep(self.DAY)
        self.assertIsNone(sleep["night_avg_hr"])
        self.assertEqual(sleep["duration_min"], 60.0)


class StructuralInvariantTest(unittest.TestCase):
    """只保留无法从公开行为表达的模块级结构约束。"""

    def test_the_reverse_engineered_field_has_a_range_gate(self) -> None:
        tree = ast.parse((PACKAGE_DIR / "health_importer.py").read_text(encoding="utf-8"))
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_RESTING_HR_RANGE" for t in node.targets)
        )
        low, high = [element.value for element in assignment.value.elts]
        self.assertGreaterEqual(low, 20)
        self.assertLessEqual(high, 200)


class BackfillSafetyBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            f"backfill_health_fields_test_{uuid4().hex}",
            REPO_ROOT / "scripts" / "backfill_health_fields.py",
        )
        assert spec is not None and spec.loader is not None
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "2026-08-21.zip"
        self.source.write_bytes(b"fixture")

    def _run(self, *, apply: bool, import_error: Exception | None = None):
        events: list[str] = []

        class _Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, *_args):
                return self

            def fetchone(self):
                return {"n": 1}

        class _Store:
            writable = True
            db_path = self.root / "health.db"

            def storage_status(self):
                return {"available": True}

            def _connection(self):
                return _Connection()

            def delete_import(self, *_args, **_kwargs):
                events.append("delete")

        class _Service:
            def import_file(self, *_args):
                events.append("import")
                if import_error is not None:
                    raise import_error
                return {"status": "imported", "data_types": []}

        class _Backup:
            def write_recovery_point(self):
                events.append("backup")
                return {"name": "pre-reset-fixture.zip", "bytes": 1}

        candidate = {
            "id": "import-1", "filename": self.source.name, "kind": "wellness_zip",
            "date_hint": "2026-08-21", "sha256": self.script._sha256(self.source.read_bytes()),
            "path": self.source, "source": "health-imports", "available": True,
        }
        argv = ["backfill_health_fields.py", *(["--apply"] if apply else [])]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(self.script, "HealthStore", return_value=_Store()),
            mock.patch.object(self.script, "HealthImportService", return_value=_Service()),
            mock.patch.object(self.script, "LocalBackupService", return_value=_Backup()),
            mock.patch.object(self.script, "summary_coverage", side_effect=[{}, {}]),
            mock.patch.object(self.script, "reimportable", return_value=[candidate]),
        ):
            result = self.script.main()
        return result, events

    def test_recovery_point_exists_before_delete_even_when_reimport_fails(self) -> None:
        result, events = self._run(apply=True, import_error=RuntimeError("解析失败"))
        self.assertEqual(result, 1)
        self.assertEqual(events, ["backup", "delete", "import"])

    def test_dry_run_does_not_create_backup_or_touch_the_import(self) -> None:
        result, events = self._run(apply=False)
        self.assertEqual(result, 0)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
