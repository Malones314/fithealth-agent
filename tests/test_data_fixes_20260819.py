"""DATA-01 / DATA-02 / DATA-03 的回归测试。

这三处修复各自对应一个曾经把错误数据写进库的缺陷，且都落在原先零覆盖的
解析层，因此这里用纯函数级构造来锁死行为，不需要真实 .fit 夹具。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


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


fit_parser = load_module("fit_parser_data_fixes", PACKAGE_DIR / "fit_parser.py")


# ══════════════════════════════════════════════════════════════════════════
# DATA-01：速度单位
# ══════════════════════════════════════════════════════════════════════════


class SpeedUnitContractTest(unittest.TestCase):
    """fitparse 把 *_speed 转成 km/h，我们内部一律存 m/s。"""

    def test_fitparse_still_converts_speed_to_kmh(self) -> None:
        """锁死上游契约：一旦 fitparse 升级后不再换算，这条会先失败。

        这是整个修复的前提假设，必须显式钉住，否则 fitparse 行为一变，
        速度就会反向错 3.6 倍且没有任何信号。
        """
        from fitparse.processors import StandardUnitsDataProcessor

        class _Field:
            def __init__(self, name: str, value: float, units: str) -> None:
                self.name, self.value, self.units = name, value, units

        processor = StandardUnitsDataProcessor()

        for name in ("avg_speed", "max_speed", "enhanced_avg_speed", "enhanced_max_speed"):
            field = _Field(name, 3.0, "m/s")
            processor.run_field_processor(field)
            self.assertAlmostEqual(field.value, 10.8, places=6, msg=f"{name} 应被转成 km/h")
            self.assertEqual(field.units, "km/h")

        # total_distance 不受影响，仍是米——不要跟着一起改
        distance = _Field("total_distance", 5000.0, "m")
        processor.run_field_processor(distance)
        self.assertAlmostEqual(distance.value, 5000.0, places=6)

    def test_speed_helper_converts_kmh_back_to_mps(self) -> None:
        self.assertAlmostEqual(fit_parser._speed_mps(10.8), 3.0, places=6)
        self.assertAlmostEqual(fit_parser._speed_mps(18.0), 5.0, places=6)

    def test_speed_helper_falls_back_to_later_values(self) -> None:
        """等价于原来的 `enhanced or plain` 写法。"""
        self.assertAlmostEqual(fit_parser._speed_mps(None, 10.8), 3.0, places=6)
        self.assertAlmostEqual(fit_parser._speed_mps(0, 10.8), 3.0, places=6)
        self.assertEqual(fit_parser._speed_mps(None, None), 0.0)
        self.assertEqual(fit_parser._speed_mps("bad"), 0.0)

    def test_lap_speed_is_stored_as_mps(self) -> None:
        """端到端：喂 km/h 的 lap，段里应拿到 m/s。"""
        start = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
        laps = [{
            "start_time": start,
            "timestamp": start + timedelta(seconds=600),
            "total_elapsed_time": 600.0,
            "total_distance": 1800.0,
            "avg_speed": 10.8,   # fitparse 交给我们时已是 km/h
            "max_speed": 18.0,
            "avg_heart_rate": 150,
            "max_heart_rate": 165,
        }]
        session = fit_parser.SessionSummary(sport="骑行", sport_raw="cycling")
        segments = fit_parser.LapBasedParser().parse({"lap": laps}, [], session)

        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0].avg_speed_mps, 3.0, places=3)
        self.assertAlmostEqual(segments[0].max_speed_mps, 5.0, places=3)
        # 展示层会再乘 3.6，还原成最初的 km/h
        self.assertAlmostEqual(segments[0].avg_speed_mps * 3.6, 10.8, places=3)


# ══════════════════════════════════════════════════════════════════════════
# DATA-03：自重 / 计时动作不能被判成组间休息
# ══════════════════════════════════════════════════════════════════════════


class RestFallbackTest(unittest.TestCase):
    def _decode(self, weight: float, reps: int, set_type: str):
        return fit_parser._decode_exercise_name((), (), (), weight, reps, set_type)

    def test_explicit_rest_is_rest(self) -> None:
        self.assertEqual(self._decode(0.0, 0, "rest")[1], "rest")

    def test_active_bodyweight_set_is_not_rest(self) -> None:
        """平板支撑 / 静力保持：weight=0 且 reps=0，但 set_type 是 active。"""
        zh, _en = self._decode(0.0, 0, "active")
        self.assertNotEqual(zh, "组间休息")

    def test_missing_set_type_keeps_legacy_fallback(self) -> None:
        """老文件没有 set_type 时保持原有兜底，避免把休息段当成动作。"""
        self.assertEqual(self._decode(0.0, 0, "")[1], "rest_fallback")

    def test_active_set_with_reps_still_decodes(self) -> None:
        zh, _en = self._decode(0.0, 12, "active")
        self.assertNotEqual(zh, "组间休息")

    def test_unknown_device_category_uses_short_editable_label(self) -> None:
        zh, raw = fit_parser._decode_exercise_name(
            (65534, 7), (2, None), (255, 0), 10.0, 8, "active"
        )
        self.assertEqual((zh, raw), ("未识别", "unknown"))

    def test_active_bodyweight_segment_is_editable(self) -> None:
        """回归 DATA-03 的真正危害：被判成休息就不可编辑、组数也会少算。"""
        start = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
        sets = [{
            "start_time": start,
            "duration": 60.0,
            "repetitions": 0,
            "weight": 0.0,
            "set_type": "active",
            "category": (),
            "category_subtype": (),
        }]
        session = fit_parser.SessionSummary(sport="力量训练", sport_raw="strength_training")
        segments = fit_parser.StrengthTrainingParser().parse({"set": sets}, [], session)

        self.assertEqual(len(segments), 1)
        self.assertFalse(segments[0].is_rest)
        self.assertEqual(segments[0].segment_type, "set_active")


# ══════════════════════════════════════════════════════════════════════════
# DATA-02：raw_path 相对化与删除归属校验
# ══════════════════════════════════════════════════════════════════════════


class RawPathResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        health_store = load_module("health_store_data_fixes", PACKAGE_DIR / "health_store.py")
        self.store = health_store.HealthStore(
            db_path=root / "health.db", raw_dir=root / "health-imports"
        )
        self.root = root

    def test_bare_filename_resolves_inside_raw_dir(self) -> None:
        resolved = self.store.resolve_raw_file("abc-2026-08-19.zip")
        self.assertEqual(resolved, (self.store.raw_dir / "abc-2026-08-19.zip").resolve())

    def test_legacy_absolute_path_is_remapped_by_basename(self) -> None:
        """老数据里的容器绝对路径必须还能对上本机文件。"""
        resolved = self.store.resolve_raw_file(
            "/opt/project/data/health-imports/abc-2026-08-19.zip"
        )
        self.assertEqual(resolved, (self.store.raw_dir / "abc-2026-08-19.zip").resolve())

    def test_windows_style_legacy_path_is_remapped(self) -> None:
        resolved = self.store.resolve_raw_file(r"C:\project\data\health-imports\abc.zip")
        self.assertEqual(resolved, (self.store.raw_dir / "abc.zip").resolve())

    def test_traversal_and_empty_values_never_escape(self) -> None:
        for value in ("", None, "..", ".", "/", "   /../.."):
            with self.subTest(value=value):
                resolved = self.store.resolve_raw_file(value)
                if resolved is not None:
                    self.assertEqual(resolved.parent, self.store.raw_dir.resolve())

        # 带穿越片段的路径只会取到 basename，永远落在 raw_dir 内
        for value in ("../../../etc/passwd", "/etc/passwd", "....//....//etc/shadow"):
            with self.subTest(value=value):
                resolved = self.store.resolve_raw_file(value)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.parent, self.store.raw_dir.resolve())

    def test_delete_never_touches_files_outside_raw_dir(self) -> None:
        outsider = self.root / "important.txt"
        outsider.write_text("请不要删我", encoding="utf-8")

        self.store._delete_raw_file(str(outsider))

        self.assertTrue(outsider.exists(), "删除操作不得触及 raw_dir 之外的文件")

    def test_delete_removes_file_inside_raw_dir(self) -> None:
        victim = self.store.raw_dir / "stale.zip"
        victim.write_bytes(b"x")

        self.store._delete_raw_file("stale.zip")

        self.assertFalse(victim.exists())

    def test_delete_import_cleans_up_legacy_absolute_path(self) -> None:
        """DATA-02 的核心症状：老记录删不掉原始文件。"""
        raw_name = "deadbeefdeadbeef-2026-08-19.zip"
        raw_file = self.store.raw_dir / raw_name
        raw_file.write_bytes(b"garmin")

        self.store.save_import({
            "id": "import-legacy",
            "sha256": "deadbeef",
            "filename": "2026-08-19.zip",
            "kind": "wellness",
            "status": "ok",
            "date_hint": "2026-08-19",
            "warnings": [],
            # 迁移前的形态：另一台机器上的绝对路径
            "raw_path": f"/opt/project/data/health-imports/{raw_name}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_files": [],
        })

        self.assertTrue(self.store.delete_import("import-legacy"))
        self.assertFalse(raw_file.exists(), "老的绝对路径记录也应能删掉原始文件")


class RawPathPersistenceTest(unittest.TestCase):
    """新导入必须只写文件名，不写绝对路径。"""

    def test_importer_stores_bare_filename(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)

        # health_importer 里有 `from .health_store import ...`，需要先占位一个
        # 轻量的包对象，避免触发 fithealth_agent/__init__.py 的 LLM 依赖链。
        package = types.ModuleType("fithealth_agent")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["fithealth_agent"] = package
        self.addCleanup(
            lambda: [
                sys.modules.pop(name, None)
                for name in (
                    "fithealth_agent.health_importer",
                    "fithealth_agent.health_store",
                    "fithealth_agent",
                )
            ]
        )

        health_store = load_module(
            "fithealth_agent.health_store", PACKAGE_DIR / "health_store.py"
        )
        store = health_store.HealthStore(
            db_path=root / "health.db", raw_dir=root / "health-imports"
        )
        importer = load_module(
            "fithealth_agent.health_importer", PACKAGE_DIR / "health_importer.py"
        )

        csv = (
            "睡眠分数 1 天,\n"
            "日期,2026-08-14\n"
            "睡眠时长,6时 48分\n"
            "\n"
            "睡眠分数因素,\n"
            "深度睡眠持续时间,1时 41分\n"
            "轻度睡眠持续时间,3时 42分\n"
            "快速眼动持续时间,1时 25分\n"
            "清醒时间,43分\n"
        )
        service = importer.HealthImportService(store)
        result = service.import_file("睡眠.csv", csv.encode("utf-8"))

        # 必须显式 close：留着的 sqlite 句柄会让 Windows 上的
        # TemporaryDirectory 清理直接 PermissionError（与被测行为无关的假失败）。
        connection = sqlite3.connect(root / "health.db")
        try:
            stored = connection.execute(
                "SELECT raw_path FROM health_imports WHERE id = ?", (result["id"],)
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertNotIn("/", stored)
        self.assertNotIn("\\", stored)
        self.assertTrue((store.raw_dir / stored).exists())


if __name__ == "__main__":
    unittest.main()
