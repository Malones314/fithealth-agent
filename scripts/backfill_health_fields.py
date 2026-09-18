#!/usr/bin/env python3
"""把新增的健康字段回灌到已导入的历史数据上。

背景
----
2026-08-22 扩展了 Garmin wellness 包的解析范围，新增四类以前一条都没入库的数据：

* **静息代谢率**（`monitoring_info.resting_metabolic_rate`）—— `daily_health_summary`
  早就有 `resting_calories` / `total_calories` 两列却 16 天全空，因为读取方去
  `monitoring` 里找 `resting_calories`/`bmr_calories`，而这个数其实在
  `monitoring_info` 里，而那条消息整个没被处理。
* **静息心率**（Garmin 私有消息 `unknown_211`，逆向推断）
* **强度分钟**（`monitoring.moderate_activity_time` / `vigorous_activity_time`）
* **设备 UTC 偏移**（`monitoring_info` / `local_time` 的本地墙钟与 UTC 之差）

这些数据一直躺在 `data/health-imports/` 下的原始包里，只是没被读出来。本脚本
重新解析那些包，把新字段补进库，而**不需要重新下载或重新上传**。

为什么可以安全重放
------------------
样本表都带 `UNIQUE(source_file_id, timestamp_utc, …)`，重放走 `INSERT OR IGNORE`，
已有行原样不动。所以脚本的做法是：删掉旧 import 行（级联清掉它的样本），再用
同一份原始字节重新导入。sha256 一致，因此不会产生重复的 import 记录。

**动库之前一定先落一份恢复点**（复用 DATA-14 那套：普通备份 zip，出问题时下载后
走「导入备份」即可整体还原）。写不出恢复点就一个字节都不改。

本脚本目前按导入逐条“删除旧行，再重新解析”，不是跨导入的原子事务。若解析器在
删除后抛错，脚本会立即停止；此时应使用本次运行开头生成的恢复点整体还原后再排查。

用法
----
    python scripts/backfill_health_fields.py             # 预演，只报告不写入
    python scripts/backfill_health_fields.py --apply     # 实际执行
    python scripts/backfill_health_fields.py --apply --filename 2026-08-21.zip
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

from fithealth_agent.backup_service import LocalBackupService
from fithealth_agent.health_importer import HealthImportService, _sha256
from fithealth_agent.health_store import HealthStore
from fithealth_agent.maintenance import MaintenanceGate
from fithealth_agent.settings import data_path

#: 本次新增的字段，用来在报告里显示"补上了什么"。
NEW_SUMMARY_COLUMNS = (
    "resting_calories",
    "total_calories",
    "resting_metabolic_rate",
    "resting_heart_rate",
    "resting_heart_rate_baseline",
    "utc_offset_minutes",
    "moderate_activity_min",
    "vigorous_activity_min",
    "intensity_minutes",
)


def summary_coverage(store: HealthStore) -> dict[str, int]:
    """每个新列有多少天已经有值。"""
    with store._connection() as connection:
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(daily_health_summary)")}
        columns = [name for name in NEW_SUMMARY_COLUMNS if name in existing]
        if not columns:
            return {}
        counts = ", ".join(f"COUNT({name}) AS {name}" for name in columns)
        row = connection.execute(f"SELECT {counts} FROM daily_health_summary").fetchone()
    return {name: int(row[name]) for name in columns}


def reimportable(store: HealthStore, extra_dirs: list[Path]) -> list[dict[str, object]]:
    """列出可以重放的导入记录。

    优先用 `data/health-imports/` 下自己留的副本。有些早期导入没留下 raw 文件
    （DATA-02 之前的记录 `raw_path` 是空的），这时按**同名文件**去用户指定的目录
    里找——文件名带日期，且随后会核对 sha256，认错不会静默写进去。
    """
    items: list[dict[str, object]] = []
    with store._connection() as connection:
        rows = connection.execute(
            "SELECT id, filename, kind, raw_path, sha256, date_hint FROM health_imports ORDER BY created_at"
        ).fetchall()
    for row in rows:
        path = store.resolve_raw_file(row["raw_path"])
        source = "health-imports"
        if path is None or not path.is_file():
            path = None
            for directory in extra_dirs:
                candidate = directory / str(row["filename"])
                if candidate.is_file():
                    path, source = candidate, str(directory)
                    break
        items.append({
            "id": row["id"],
            "filename": row["filename"],
            "kind": row["kind"],
            "date_hint": row["date_hint"],
            "sha256": row["sha256"],
            "path": path,
            "source": source,
            "available": path is not None,
        })
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际写入；缺省只预演")
    parser.add_argument("--filename", help="只处理这一个导入文件名")
    parser.add_argument(
        "--extra-source", action="append", default=[], metavar="DIR",
        help="额外的原始包目录，用来补 data/health-imports/ 里已丢失的文件（可多次指定）",
    )
    parser.add_argument(
        "--skip-recovery-point", action="store_true",
        help="不落恢复点就直接改库。**不建议**，只在已经手动备份过时使用",
    )
    args = parser.parse_args()
    extra_dirs = [Path(item).expanduser() for item in args.extra_source]
    for directory in extra_dirs:
        if not directory.is_dir():
            print(f"额外目录不存在：{directory}")
            return 2

    store = HealthStore()
    service = HealthImportService(store)
    print(f"数据库：{store.db_path}")
    status = store.storage_status()
    if not status["available"] or not store.writable:
        print(f"数据库当前不可写（{status}），无法回灌。")
        return 2

    before = summary_coverage(store)
    total_days = 0
    with store._connection() as connection:
        total_days = connection.execute(
            "SELECT COUNT(*) AS n FROM daily_health_summary"
        ).fetchone()["n"]
    print(f"日汇总共 {total_days} 天。各新列当前覆盖：")
    for name, count in before.items():
        print(f"    {name:<30} {count}/{total_days}")

    candidates = reimportable(store, extra_dirs)
    by_digest: dict[str, list[dict[str, object]]] = {}
    for item in candidates:
        digest = str(item.get("sha256") or "")
        if digest:
            by_digest.setdefault(digest, []).append(item)
    duplicate_groups = [items for items in by_digest.values() if len(items) > 1]
    if duplicate_groups:
        print("\n检测到内容相同的重复导入记录；为避免删掉一条后命中另一条而跳过解析，本次未执行：")
        for items in duplicate_groups:
            names = "、".join(f"{item['filename']} ({item['id']})" for item in items)
            print(f"    ✘ {names}")
        print("请先核对并清理重复 import 行，再重新运行回灌。")
        return 2
    if args.filename:
        candidates = [item for item in candidates if item["filename"] == args.filename]
    missing = [item for item in candidates if not item["available"]]
    usable = [item for item in candidates if item["available"]]

    print()
    print(f"可重放 {len(usable)} 份，找不到原始文件 {len(missing)} 份")
    for item in missing:
        print(f"    ✘ {item['filename']}（原始文件不在；可用 --extra-source 指向存放它的目录）")
    for item in usable:
        note = "" if item["source"] == "health-imports" else f"  ← {item['source']}"
        print(f"    ✔ {item['filename']:<28} {item['kind']:<14} {item['date_hint'] or '':<12}{note}")

    if not usable:
        print("\n没有可重放的原始包。")
        return 0
    if not args.apply:
        print("\n预演结束。加 --apply 才会实际写入。")
        return 0

    if not args.skip_recovery_point:
        backup = LocalBackupService(store.db_path.parent, gate=MaintenanceGate(), database=store)
        try:
            point = backup.write_recovery_point()
        except Exception as exc:  # noqa: BLE001
            print(f"\n恢复点写入失败（{exc}），已放弃回灌，数据未做任何改动。")
            return 3
        print(f"\n已落恢复点 {point['name']}（{point['bytes']:,} 字节），出问题时可从「导入备份」还原。")

    print()
    replayed = failed = skipped = 0
    for item in usable:
        path = item["path"]
        assert isinstance(path, Path)
        content = path.read_bytes()
        if item["source"] != "health-imports":
            # 从外部目录取的文件必须逐字节对得上，否则重放的是**另一份**数据。
            digest = _sha256(content)
            if digest != item["sha256"]:
                skipped += 1
                print(f"    ✘ {item['filename']}：{item['source']} 里的同名文件 sha256 不一致，跳过")
                continue
        try:
            # 先删旧 import 行：样本表按 source_file_id 级联清理，随后用同一份
            # 字节重新导入。sha256 不变，所以不会多出一条 import 记录。
            store.delete_import(str(item["id"]), keep_raw_file=True)
            result = service.import_file(str(item["filename"]), content)
            if result.get("duplicate"):
                failed += 1
                print(f"    ✘ {item['filename']}：命中已有导入，未重新解析")
                return 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"    ✘ {item['filename']}：{exc}")
            return 1
        replayed += 1
        types = ", ".join(result.get("data_types", []))
        print(f"    ✔ {item['filename']:<28} {result['status']:<10} {types}")

    after = summary_coverage(store)
    with store._connection() as connection:
        total_days = connection.execute(
            "SELECT COUNT(*) AS n FROM daily_health_summary"
        ).fetchone()["n"]
    print()
    print(f"重放 {replayed} 份，失败 {failed} 份，校验不符跳过 {skipped} 份。"
          f"日汇总共 {total_days} 天。各新列覆盖变化：")
    for name in before:
        delta = after.get(name, 0) - before.get(name, 0)
        arrow = f"  (+{delta})" if delta > 0 else (f"  ({delta})" if delta else "")
        print(f"    {name:<30} {before.get(name, 0)} → {after.get(name, 0)}{arrow}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
