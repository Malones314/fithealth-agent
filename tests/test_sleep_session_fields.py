"""睡眠时长口径的回归测试（2026-08-24）。

盯的是一个会静默出错的地方：睡眠时长曾按「深+浅+REM 分期累加」算，每晚少报
13~144 分钟，而界面和 Agent 都看不出差别。这里用**真实数据包 + 睡眠 CSV 的真值**
做端到端断言，不做源码文本断言——后者是这个仓库里假绿的主要来源。

真值取自 Garmin Connect 导出的睡眠 CSV（人工核对过）：
    日期          分数  睡眠时长  清醒  躺床
    2026-08-10    54    457      102   559
    2026-08-19    82    427      7     434
    2026-08-20    71    401      49    450
    2026-08-21    68    477      67    544
    2026-08-22    64    301      3     304
其中「躺床 = 睡眠 + 清醒」，「睡眠 = 躺床 − 清醒」，五天都精确成立。

真实包不在仓库里时整个模块 skip，**不允许退化成永远为真的空断言**（ARCH-09）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fithealth_agent.health_importer import HealthImportService
from fithealth_agent.health_store import HealthStore

DATA_DIR = Path(os.environ.get("FITHEALTH_REAL_DATA_DIR") or "data")
IMPORTS = DATA_DIR / "health-imports"

# (日期, 分数, 睡眠时长, 清醒, 躺床)
TRUTH = [
    ("2026-08-10", 54, 457, 102, 559),
    ("2026-08-19", 82, 427, 7, 434),
    ("2026-08-20", 71, 401, 49, 450),
    ("2026-08-21", 68, 477, 67, 544),
    ("2026-08-22", 64, 301, 3, 304),
]


def _archive(day: str) -> Path | None:
    for path in sorted(IMPORTS.glob("*.zip")):
        if day in path.name and "_2_" not in path.name:
            return path
    return None


pytestmark = pytest.mark.skipif(
    not IMPORTS.is_dir() or any(_archive(day) is None for day, *_ in TRUTH),
    reason="缺少真实健康数据包，无法做真值回归",
)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> HealthStore:
    """把 5 份真实包导进临时库。原始包只读打开，不碰 data/ 下的任何东西。"""
    base = tmp_path_factory.mktemp("sleep")
    instance = HealthStore(base / "health.db", base / "raw")
    service = HealthImportService(instance)
    for day, *_ in TRUTH:
        path = _archive(day)
        assert path is not None
        service.import_file(path.name, path.read_bytes())
    return instance


def _summary(store: HealthStore, day: str) -> dict:
    record = store.get_sleep(day)
    assert record is not None, f"{day} 没有任何睡眠记录"
    return record.get("fit_stage_summary") or record


@pytest.mark.parametrize(("day", "score", "asleep", "awake", "in_bed"), TRUTH)
def test_sleep_duration_matches_garmin_csv(
    store: HealthStore, day: str, score: int, asleep: int, awake: int, in_bed: int
) -> None:
    summary = _summary(store, day)
    assert summary["source_type"] == "fit_sleep_session"
    assert summary["time_in_bed_min"] == in_bed, "躺床时长应取 sleep start/stop 事件的跨度"
    assert summary["awake_min"] == awake
    assert summary["duration_min"] == asleep, "睡眠时长 = 躺床 − 清醒"
    assert summary["score"] == score
    assert summary["is_partial"] is False


@pytest.mark.parametrize(("day", "_s", "asleep", "_a", "_b"), TRUTH)
def test_device_stage_totals_are_labelled_not_passed_off_as_duration(
    store: HealthStore, day: str, _s: int, asleep: int, _a: int, _b: int
) -> None:
    """分期时长必须带来源标签，且不得再被当成睡眠时长。

    FIT 里那条 sleep_level 时间线一晚只有 15~33 条，是设备端粗分期；Garmin
    Connect 的分期是云端重算的。两者能差 -181~+50 分钟，所以字段名必须自带
    `device_` 前缀 + `stage_source`，让读的人一眼看出这不是 Connect 的口径。
    """
    summary = _summary(store, day)
    assert summary["stage_source"] == "device_coarse_timeline"
    for key in ("deep", "light", "rem", "awake"):
        assert f"device_stage_{key}_min" in summary
    # 旧字段名不能回来：它们曾经就是 Connect 口径的冒充者
    for stale in ("deep_sleep_min", "light_sleep_min", "rem_sleep_min"):
        assert stale not in summary, f"{stale} 会被误读成 Connect 的分期"
    coarse = sum(
        summary[f"device_stage_{key}_min"] for key in ("deep", "light", "rem")
    )
    assert summary["duration_min"] != coarse, (
        "睡眠时长若又等于粗分期累加，说明口径被改回了少报 13~144 分钟的那一版"
    )


def test_bed_window_starts_at_the_sleep_start_event(store: HealthStore) -> None:
    """DATA-28：躺床起点必须是 sleep start 事件，而不是第一条分期记录。

    2026-08-21 那晚第一条 sleep_level 比 start 事件晚 7 分钟；用后者当起点会把
    躺床时长少算 7 分钟，也会让夜间心率漏掉开头那几分钟的样本。
    """
    summary = _summary(store, "2026-08-21")
    assert summary["bed_start_local"].startswith("2026-08-20T23:03")
    assert summary["bed_end_local"].startswith("2026-08-21T08:07")
    assert summary["coverage_start"] > summary["bed_start_local"], (
        "分期覆盖起点本来就晚于 start 事件，这正是不能用它当躺床起点的原因"
    )


def test_sleep_date_binds_to_wake_up_day(store: HealthStore) -> None:
    """跨零点的夜晚按醒来那天记（与 Garmin CSV 的「日期」一致）。"""
    summary = _summary(store, "2026-08-21")
    assert summary["sleep_date"] == "2026-08-21"
    assert summary["bed_start_local"].startswith("2026-08-20"), "入睡在前一天"


def test_metrics_files_no_longer_claim_data_they_do_not_have(store: HealthStore) -> None:
    """DATA-36：METRICS 不再无条件宣称提供 metrics_snapshot。

    有 unknown_384 的那份现在真的产出 sleep_session；空的那几份应当什么都不声称。
    """
    detail = None
    for row in store.list_imports(20):
        candidate = store.get_import_detail(row["id"])
        if candidate and "2026-08-21" in (candidate.get("filename") or ""):
            detail = candidate
            break
    assert detail is not None
    metrics = [s for s in detail["sources"] if "METRICS" in s["filename"]]
    assert metrics, "这份包里应该有 METRICS 文件"
    assert any("sleep_session" in s["data_types"] for s in metrics)
    for source in metrics:
        assert "metrics_snapshot" not in source["data_types"], (
            "metrics_snapshot 是那个只看文件类型、不看有没有数据的空声明"
        )
