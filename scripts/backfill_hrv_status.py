"""把 HRV 的 SDK 语义列回灌进已有的 health.db（DATA-18，2026-08-27）。

为什么需要：`hrv_status_summaries` 原先只按 fitfile 给的字段名存值，而那些名字
从字段 1 起整体偏了一位（论证见 `health_importer._HRV_STATUS_FIELDS` 上方的注释）。
修法是在解析侧按 FIT SDK 语义重映射并写进新的 `*_ms` 列，但：

  * 已导入的历史包不会自动重解析（`find_import_by_hash` 判重直接返回）；
  * 其中 `baseline_balanced_upper`（fitfile 名 `baseline_balanced_low`）**以前
    根本没存**，所以无法只靠库里的旧列换算出来，必须重读原始包。

安全性：只对 `hrv_status_summaries` 的新列做 UPDATE（按 source_file_id +
timestamp_utc 定位），**不删任何行、不改旧列、不动其他观测表**；随后重算
`daily_health_summary` 这一张派生表。可以重复跑。原始包只读打开。
按 DATA-37 的规矩，`--commit` 前先落一个恢复点，落不出来就中止。

用法：
    python scripts/backfill_hrv_status.py            # 预演，只打印
    python scripts/backfill_hrv_status.py --commit   # 真正写入
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
from fithealth_agent.health_store import HealthStore  # noqa: E402

_MS_COLUMNS = (
    "weekly_average_ms",
    "last_night_average_ms",
    "last_night_5min_high_ms",
    "baseline_low_upper_ms",
    "baseline_balanced_lower_ms",
    "baseline_balanced_upper_ms",
    "unmapped_balanced_high_raw",
)


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

    # 先让 HealthStore 跑一遍建表/加列，否则下面 UPDATE 会撞上缺列。
    store = HealthStore(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    known = {
        row["sha256"]: row["id"]
        for row in connection.execute("SELECT id, sha256 FROM health_source_files")
        if row["sha256"]
    }
    existing = {
        (row["source_file_id"], row["timestamp_utc"]): row["local_date"]
        for row in connection.execute(
            "SELECT source_file_id, timestamp_utc, local_date FROM hrv_status_summaries"
        )
    }

    updates: list[tuple] = []
    days: set[str] = set()
    unmatched = 0
    for archive in sorted(imports_dir.glob("*.zip")):
        try:
            parsed = parse_wellness_zip(archive.name, archive.read_bytes())
        except Exception as exc:  # noqa: BLE001
            print(f"  跳过 {archive.name}：{exc}")
            continue
        for source in parsed.get("sources", []):
            statuses = source.get("hrv_statuses") or []
            if not statuses:
                continue
            source_id = known.get(source.get("sha256") or "")
            if source_id is None:
                unmatched += len(statuses)
                continue
            for item in statuses:
                key = (source_id, item["timestamp_utc"])
                if key not in existing:
                    # 该行本来就不在库里（包已删或从未导入），本脚本不新增行。
                    unmatched += 1
                    continue
                updates.append(
                    tuple(item.get(column) for column in _MS_COLUMNS) + key
                )
                days.add(existing[key])

    ordered_days = sorted(days)
    if not updates:
        print("没有可回灌的数据")
        return 0
    print(
        f"可回灌 {len(updates)} 行 HRV 状态，覆盖 {len(ordered_days)} 天："
        f"{ordered_days[0]} ~ {ordered_days[-1]}"
    )
    sample = updates[0]
    print("样例（第一行的新列值）：")
    for column, value in zip(_MS_COLUMNS, sample):
        print(f"    {column:32} = {value}")
    if unmatched:
        print(f"另有 {unmatched} 行在库里找不到对应记录，已跳过（不新增行）")

    if not args.commit:
        print("\n这是预演。确认无误后加 --commit 再跑一次。")
        return 0

    # DATA-37/DATA-21 的规矩：不可逆写入之前先落恢复点，落不出来就别动数据。
    try:
        from fithealth_agent.backup_service import LocalBackupService

        point = LocalBackupService(base).write_recovery_point(prefix="pre-hrv-remap")
        print(f"已生成恢复点：{point}")
    except Exception as exc:  # noqa: BLE001
        print(f"无法生成恢复点，已中止（未写入任何数据）：{exc}")
        return 1

    assignments = ", ".join(f"{column} = ?" for column in _MS_COLUMNS)
    connection.executemany(
        f"""
        UPDATE hrv_status_summaries SET {assignments}
        WHERE source_file_id = ? AND timestamp_utc = ?
        """,
        updates,
    )
    connection.commit()
    connection.close()

    rebuilt = store.rebuild_daily_summaries(ordered_days)
    print(f"\n已写入 {len(updates)} 行，并重算了 {rebuilt} 天的 daily_health_summary。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
