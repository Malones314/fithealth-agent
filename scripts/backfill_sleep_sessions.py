"""把 sleep_sessions 回灌进已有的 health.db（2026-08-24）。

为什么需要：睡眠时长原来按「深+浅+REM 分期累加」算，实测每晚少报 13~144 分钟
（详见 `health_store.get_sleep` 的注释）。修法是改用「躺床时长 − 清醒分钟」，
数据来自 METRICS 的 `unknown_384` 与 SLEEP_DATA 的 sleep start/stop 事件——这些
字段以前根本没解析，所以库里也没有。已导入的历史包不会自动重解析
（`find_import_by_hash` 会判重直接返回），需要跑一次本脚本。

安全性：只对 `sleep_sessions` 做 INSERT OR REPLACE，**不删任何行、不改其他表**。
可以重复跑。原始包只读打开。

用法：
    python scripts/backfill_sleep_sessions.py            # 预演，只打印
    python scripts/backfill_sleep_sessions.py --commit   # 真正写入
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fithealth_agent.health_importer import parse_wellness_zip  # noqa: E402


def data_dir() -> Path:
    return Path(os.environ.get("FITHEALTH_DATA_DIR") or (ROOT / "data"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="真正写入（默认只预演）")
    args = parser.parse_args()

    base = data_dir()
    db_path = base / "health.db"
    imports_dir = base / "health-imports"
    if not db_path.exists():
        print(f"找不到数据库：{db_path}")
        return 1

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    known = {
        row["sha256"]: row["id"]
        for row in connection.execute("SELECT id, sha256 FROM health_source_files")
        if row["sha256"]
    }

    rows: list[tuple] = []
    unmatched = 0
    for archive in sorted(imports_dir.glob("*.zip")):
        try:
            parsed = parse_wellness_zip(archive.name, archive.read_bytes())
        except Exception as exc:  # noqa: BLE001
            print(f"  跳过 {archive.name}：{exc}")
            continue
        for source in parsed.get("sources", []):
            sessions = source.get("sleep_sessions") or []
            if not sessions:
                continue
            source_id = known.get(source.get("sha256") or "")
            if source_id is None:
                unmatched += len(sessions)
                continue
            for item in sessions:
                rows.append((
                    source_id, item["sleep_date"],
                    item["bed_start_utc"], item["bed_end_utc"],
                    item["bed_start_local"], item["bed_end_local"],
                    item["time_in_bed_min"], item.get("awake_min"),
                    item.get("score"), item.get("restlessness"),
                ))

    dates = sorted({row[1] for row in rows})
    print(f"可回灌 {len(rows)} 行，覆盖 {len(dates)} 天：{dates[0]} ~ {dates[-1]}" if dates else "没有可回灌的数据")
    if unmatched:
        print(f"另有 {unmatched} 行找不到对应的 health_source_files（该包可能已被删除），已跳过")

    if not args.commit:
        print("\n这是预演。确认无误后加 --commit 再跑一次。")
        return 0

    connection.executemany(
        """
        INSERT OR REPLACE INTO sleep_sessions
            (source_file_id, sleep_date, bed_start_utc, bed_end_utc,
             bed_start_local, bed_end_local, time_in_bed_min,
             awake_min, score, restlessness)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    total = connection.execute("SELECT COUNT(*) FROM sleep_sessions").fetchone()[0]
    print(f"\n已写入。sleep_sessions 现有 {total} 行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
