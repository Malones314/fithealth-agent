"""删除 Mifflin-St Jeor 估算、并把总热量接进健康趋势后的回归测试。

**为什么删掉公式估算**：手表自己算的静息代谢率（`monitoring_info.resting_metabolic_rate`）
现在会入库，是实测值。同时保留一个 Mifflin-St Jeor 估算只会让两个数打架，而且
档案缺一项就退化成"待补充信息"，对模型没有任何帮助。

**总热量为什么不能塞进原来那五个指标**：那五个是**采样均值**（`*_avg` + `*_samples`），
总热量是**当天累计量**。周/月直接读日汇总那一列；日视图给的是日内累计曲线，而不是
逐小时瞬时值——静息代谢只有 kcal/天 的日速率，硬拆成瞬时值只是在编数字。
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from tests.module_map import IMPLEMENTATION_MODULES, consumer_home, function_home
from tests.source_tools import function_node, module_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "fithealth_agent"


def _defined_function_names(path) -> set[str]:
    """模块里定义的全部函数名（含嵌套），给缺席断言用。"""
    return {
        node.name
        for node in ast.walk(module_tree(path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

_LOADED_MODULE_NAMES = ("fithealth_agent.health_store", "fithealth_agent")


def load_health_store():
    """按文件路径加载 health_store，绕开 `__init__.py` 的急切 LLM 栈导入（ARCH-02）。

    桩包必须还回去，否则之后任何 `import main` 会在
    `from fithealth_agent import create_fithealth_agent` 处炸掉。
    """
    saved = {name: sys.modules.get(name) for name in _LOADED_MODULE_NAMES}
    package = sys.modules.get("fithealth_agent")
    if package is None or not hasattr(package, "__path__"):
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
    spec = importlib.util.spec_from_file_location(
        "fithealth_agent.health_store", PACKAGE_DIR / "health_store.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fithealth_agent.health_store"] = module
    spec.loader.exec_module(module)
    return module, saved


def restore_modules(saved: dict[str, object]) -> None:
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class CalorieTrendTest(unittest.TestCase):
    DAY = "2026-08-21"
    RMR = 2400.0          # 便于核对：/24 恰好 100 kcal/小时

    def setUp(self) -> None:
        self.module, saved = load_health_store()
        self.addCleanup(restore_modules, saved)
        self._directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.store = self.module.HealthStore(root / "health.db", root / "raw")

    def stamp(self, hour: int, minute: int = 0, day: str | None = None) -> dict:
        local = datetime.fromisoformat(f"{day or self.DAY}T{hour:02d}:{minute:02d}:00+08:00")
        return {
            "timestamp_utc": local.astimezone(timezone.utc).isoformat(),
            "timestamp_local": local.isoformat(),
            "local_date": day or self.DAY,
        }

    def save(self, tag: str = "a", **payload) -> None:
        self.store.save_import({
            "id": str(uuid4()), "sha256": (tag * 64)[:64], "filename": f"{tag}.zip",
            "kind": "wellness_zip", "status": "imported", "date_hint": self.DAY,
            "warnings": [], "raw_path": None,
            "created_at": datetime.now(timezone.utc).isoformat(), "sleep": None,
            "sources": [{
                "id": str(uuid4()), "filename": f"{tag}.fit", "kind": "wellness",
                "sha256": (tag * 64)[:64], "earliest_utc": None, "latest_utc": None,
                "device_serial": "d", "warnings": [], "record_count": 1,
                "message_counts": {}, "data_types": [],
                "heart_rates": payload.get("heart_rates", []),
                "metric_samples": [], "hrv_statuses": [], "sleep_stages": [],
                "activity_observations": payload.get("activity_observations", []),
                "device_metrics": payload.get("device_metrics", []),
                "intensity_observations": [],
            }],
        })

    def build_full_day(self) -> None:
        """整日：RMR 2400，走路累计到 300 kcal，心率覆盖到 23 点。"""
        self.save(
            device_metrics=[{**self.stamp(0), "resting_metabolic_rate": self.RMR}],
            activity_observations=[
                {**self.stamp(8), "activity_type": "walking", "active_calories": 100.0, "steps": 500},
                {**self.stamp(18), "activity_type": "walking", "active_calories": 300.0, "steps": 1500},
                # generic 是手表的聚合行，日汇总不计它，曲线也不该计
                {**self.stamp(18, 30), "activity_type": "generic", "active_calories": 900.0},
            ],
            heart_rates=[{**self.stamp(hour), "bpm": 70} for hour in range(24)],
        )

    # ---- 日视图：累计曲线 ----
    def test_the_day_view_returns_a_cumulative_curve(self) -> None:
        self.build_full_day()
        trend = self.store.get_metric_trend("total_calories", "day", self.DAY)
        self.assertTrue(trend["cumulative"])
        self.assertEqual(trend["unit"], "kcal")
        values = [item["value"] for item in trend["items"]]
        self.assertEqual(len(values), 24)
        self.assertEqual(values, sorted(values), "累计曲线必须单调不减")

    def test_the_resting_part_is_prorated_across_the_day(self) -> None:
        """手表只给 kcal/天 的日速率，没有小时粒度，只能匀速摊。"""
        self.build_full_day()
        items = self.store.get_metric_trend("total_calories", "day", self.DAY)["items"]
        self.assertAlmostEqual(items[0]["resting_calories"], 100.0, places=1)
        self.assertAlmostEqual(items[11]["resting_calories"], 1200.0, places=1)
        self.assertAlmostEqual(items[23]["resting_calories"], self.RMR, places=1)

    def test_the_last_point_equals_the_daily_summary_total(self) -> None:
        """整日完整时这条必须成立，否则趋势和总览卡片会给出两个数。"""
        self.build_full_day()
        items = self.store.get_metric_trend("total_calories", "day", self.DAY)["items"]
        with self.store._connection() as connection:
            total = connection.execute(
                "SELECT total_calories FROM daily_health_summary WHERE date = ?", (self.DAY,)
            ).fetchone()["total_calories"]
        self.assertAlmostEqual(items[-1]["value"], total, delta=0.6)

    def test_only_counted_activity_types_enter_the_curve(self) -> None:
        """`generic` 是手表的聚合行；算进来会重复计数（实测它能到 walking 的 1.5 倍）。"""
        self.build_full_day()
        items = self.store.get_metric_trend("total_calories", "day", self.DAY)["items"]
        self.assertAlmostEqual(items[-1]["active_calories"], 300.0, places=1)

    def test_active_calories_carry_forward_through_hours_without_observations(self) -> None:
        """观测是稀疏的（真实数据一天约 100 条），中间空的小时必须沿用上一次累计值，
        否则曲线会掉回去。"""
        self.build_full_day()
        items = self.store.get_metric_trend("total_calories", "day", self.DAY)["items"]
        self.assertAlmostEqual(items[8]["active_calories"], 100.0, places=1)
        for index in range(9, 18):
            self.assertAlmostEqual(items[index]["active_calories"], 100.0, places=1)
        self.assertAlmostEqual(items[18]["active_calories"], 300.0, places=1)

    def test_a_partial_day_is_not_padded_to_24_hours(self) -> None:
        """今天才过到 10 点就画满一天，等于凭空替用户消耗了 14 小时静息热量。"""
        self.save(
            device_metrics=[{**self.stamp(0), "resting_metabolic_rate": self.RMR}],
            heart_rates=[{**self.stamp(hour), "bpm": 70} for hour in range(11)],
        )
        items = self.store.get_metric_trend("total_calories", "day", self.DAY)["items"]
        self.assertEqual(len(items), 11)
        self.assertAlmostEqual(items[-1]["value"], 1100.0, places=1)

    def test_without_a_metabolic_rate_there_is_no_total(self) -> None:
        """静息是主项（真实数据 2417 里的 2230）；只画活动却标成总热量会低报 90%。"""
        self.save(
            activity_observations=[
                {**self.stamp(8), "activity_type": "walking", "active_calories": 100.0},
            ],
            heart_rates=[{**self.stamp(hour), "bpm": 70} for hour in range(24)],
        )
        self.assertEqual(self.store.get_metric_trend("total_calories", "day", self.DAY)["items"], [])
        # 与周/月一致：日汇总里 total_calories 也是空的
        with self.store._connection() as connection:
            self.assertIsNone(connection.execute(
                "SELECT total_calories FROM daily_health_summary WHERE date = ?", (self.DAY,)
            ).fetchone()["total_calories"])

    def test_an_overlapping_reimport_does_not_double_the_curve(self) -> None:
        self.build_full_day()
        before = self.store.get_metric_trend("total_calories", "day", self.DAY)["items"][-1]
        self.save("b",
            device_metrics=[{**self.stamp(0), "resting_metabolic_rate": self.RMR}],
            activity_observations=[
                {**self.stamp(8), "activity_type": "walking", "active_calories": 100.0, "steps": 500},
                {**self.stamp(18), "activity_type": "walking", "active_calories": 300.0, "steps": 1500},
            ],
            heart_rates=[{**self.stamp(hour), "bpm": 70} for hour in range(24)],
        )
        after = self.store.get_metric_trend("total_calories", "day", self.DAY)["items"][-1]
        self.assertAlmostEqual(after["value"], before["value"], places=1)

    # ---- 周/月视图 ----
    def test_the_week_view_reads_daily_totals(self) -> None:
        self.build_full_day()
        trend = self.store.get_metric_trend("total_calories", "week", self.DAY)
        self.assertFalse(trend["cumulative"])
        self.assertEqual([item["label"] for item in trend["items"]], [self.DAY])
        self.assertAlmostEqual(trend["items"][0]["value"], 2700.0, delta=0.6)

    def test_daily_total_points_carry_no_sample_count(self) -> None:
        """日累计量没有"采样数"这个概念，编一个出来只会误导。"""
        self.build_full_day()
        for period in ("day", "week", "month"):
            with self.subTest(period=period):
                items = self.store.get_metric_trend("total_calories", period, self.DAY)["items"]
                for item in items:
                    self.assertNotIn("samples", item)

    def test_days_without_a_total_are_skipped_in_the_week_view(self) -> None:
        self.build_full_day()
        # 另一天只有活动、没有 RMR → total_calories 为空，不该出现在曲线里
        other = "2026-08-20"
        self.store.save_import({
            "id": str(uuid4()), "sha256": "c" * 64, "filename": "c.zip",
            "kind": "wellness_zip", "status": "imported", "date_hint": other,
            "warnings": [], "raw_path": None,
            "created_at": datetime.now(timezone.utc).isoformat(), "sleep": None,
            "sources": [{
                "id": str(uuid4()), "filename": "c.fit", "kind": "wellness", "sha256": "d" * 64,
                "earliest_utc": None, "latest_utc": None, "device_serial": "d", "warnings": [],
                "record_count": 1, "message_counts": {}, "data_types": [],
                "heart_rates": [], "metric_samples": [], "hrv_statuses": [], "sleep_stages": [],
                "activity_observations": [
                    {**self.stamp(9, day=other), "activity_type": "walking",
                     "active_calories": 50.0, "steps": 300},
                ],
                "device_metrics": [], "intensity_observations": [],
            }],
        })
        labels = [item["label"] for item in
                  self.store.get_metric_trend("total_calories", "week", self.DAY)["items"]]
        self.assertEqual(labels, [self.DAY])

    # ---- 不影响原有指标 ----
    def test_the_five_sample_metrics_are_unchanged(self) -> None:
        self.build_full_day()
        for metric in ("heart_rate", "stress", "respiration", "spo2", "hrv"):
            with self.subTest(metric=metric):
                trend = self.store.get_metric_trend(metric, "day", self.DAY)
                self.assertFalse(trend["cumulative"])
                self.assertIsNone(trend["unit"])
        heart = self.store.get_metric_trend("heart_rate", "day", self.DAY)
        self.assertTrue(heart["items"])
        self.assertIn("samples", heart["items"][0])

    def test_unknown_metrics_are_still_rejected(self) -> None:
        for metric in ("body_battery", "resting_heart_rate", "intensity_minutes", ""):
            with self.subTest(metric=metric):
                with self.assertRaises(ValueError):
                    self.store.get_metric_trend(metric, "day", self.DAY)

    def test_unknown_periods_are_still_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.get_metric_trend("total_calories", "year", self.DAY)


class MifflinRemovalTest(unittest.TestCase):
    """Mifflin-St Jeor 估算已从后端删除。"""

    def test_the_estimator_function_is_gone(self) -> None:
        # 缺席断言扫过全部实现模块：只看 main.py 的话，函数搬进 domain/ 之后
        # 这条会静默变成永真。
        for path in IMPLEMENTATION_MODULES:
            with self.subTest(module=path.name):
                self.assertNotIn("calculate_bmr_kcal", _defined_function_names(path))

    def test_the_profile_summary_no_longer_reports_an_estimate(self) -> None:
        node = function_node(function_home("profile_summary"), "profile_summary")
        body = "\n".join(ast.unparse(statement) for statement in node.body[1:])
        self.assertNotIn("Mifflin", body)
        self.assertNotIn("基础代谢", body)
        # 手表实测的静息代谢率走 daily_health_summary，不在档案里
        self.assertNotIn("6.25", body)

    def test_the_overview_endpoint_no_longer_injects_an_estimate(self) -> None:
        node = function_node(consumer_home("get_daily_overview"), "get_daily_overview")
        body = "\n".join(ast.unparse(statement) for statement in node.body)
        self.assertNotIn("estimated_bmr_kcal", body)

class TrendWiringTest(unittest.TestCase):
    """趋势的前后端接线。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.store_source = (PACKAGE_DIR / "health_store.py").read_text(encoding="utf-8")

    def test_the_counted_activity_types_match_the_daily_summary(self) -> None:
        """曲线与日汇总必须用同一份活动类型白名单，否则两处活动消耗对不上。

        DATA-25 之后这个不变量是**结构性**的：`_rebuild_daily_summary` 的 SQL 由
        `_COUNTED_ACTIVITY_TYPES_SQL` 插值生成，不再有手写字面量可漂移。所以这里
        直接断言"插值片段由常量派生"且"源码里不再残留手写的类型字面量"，而不是去
        grep 那串 SQL 文本——旧写法只要有人改一下空格就会红，改一下类型却照样绿。
        """
        from fithealth_agent.health_store import (
            COUNTED_ACTIVITY_TYPES,
            _COUNTED_ACTIVITY_TYPES_SQL,
        )

        self.assertEqual(set(COUNTED_ACTIVITY_TYPES), {"walking", "running", "cycling"})
        self.assertEqual(
            _COUNTED_ACTIVITY_TYPES_SQL,
            ", ".join(f"'{name}'" for name in COUNTED_ACTIVITY_TYPES),
        )
        self.assertNotIn(
            "activity_type IN ('walking', 'running', 'cycling')", self.store_source,
            "白名单又被手写回 SQL 里了，两处会重新漂移",
        )
        self.assertIn("{_COUNTED_ACTIVITY_TYPES_SQL}", self.store_source)

    def test_the_metric_is_registered_as_a_daily_total(self) -> None:
        self.assertIn('DAILY_TOTAL_METRICS = ("total_calories",)', self.store_source)

if __name__ == "__main__":
    unittest.main()
