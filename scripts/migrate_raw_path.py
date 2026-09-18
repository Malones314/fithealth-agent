#!/usr/bin/env python3
"""DATA-02 迁移：把 health_imports.raw_path 从绝对路径规范化为纯文件名。

背景
----
`health_importer` 以前把原始 Garmin 文件的**绝对路径**写进库
（`str(raw_path.resolve())`）。这些路径常常来自另一个运行环境——例如容器内的
`/opt/project/data/health-imports/...`——在本机 100% 解析不到。后果：

* 删除导入时 `unlink()` 全部落空，原始 zip/fit 永远删不掉、不断堆积；
* 备份跨机恢复后，raw_path 可能指向数据目录之外的任意位置。

代码侧已改为只存文件名并在 `HealthStore.resolve_raw_file` 里强制目录归属校验。
本脚本负责把**存量数据**一并规范化。

用法
----
    python scripts/migrate_raw_path.py            # 预演，只报告不写入
    python scripts/migrate_raw_path.py --apply    # 实际写入（会先备份数据库）

脚本是幂等的，重复执行安全。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]


def normalize(raw_path: object) -> str | None:
    """与 HealthStore.resolve_raw_file 保持一致的文件名提取逻辑。"""
    if not raw_path:
        return None
    name = PurePosixPath(str(raw_path).replace("\\", "/")).name
    if not name or name in {".", ".."}:
        return None
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="实际写入；缺省为预演模式")
    parser.add_argument("--data-dir", default="data", help="数据目录（默认 data）")
    parser.add_argument("--force", action="store_true", help="确认服务已停止并允许写入")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = data_dir / "health.db"
    raw_dir = data_dir / "health-imports"

    if not db_path.exists():
        print(f"找不到数据库：{db_path}", file=sys.stderr)
        return 1

    on_disk = {p.name for p in raw_dir.iterdir() if p.is_file()} if raw_dir.is_dir() else set()

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, filename, raw_path FROM health_imports WHERE raw_path IS NOT NULL"
    ).fetchall()

    unchanged: list[str] = []
    to_update: list[tuple[str, str, str]] = []   # (id, 旧值, 新值)
    missing: list[tuple[str, str]] = []          # 规范化后磁盘上仍找不到
    unusable: list[str] = []                     # 无法提取出文件名

    for row in rows:
        old = row["raw_path"]
        name = normalize(old)
        if name is None:
            unusable.append(row["id"])
            continue
        if name not in on_disk:
            missing.append((row["id"], name))
        if name == old:
            unchanged.append(row["id"])
        else:
            to_update.append((row["id"], old, name))

    referenced = {normalize(r["raw_path"]) for r in rows} - {None}
    orphan_files = sorted(on_disk - referenced)

    print("=" * 68)
    print(f"数据库      : {db_path}")
    print(f"原始文件目录: {raw_dir}  （磁盘上 {len(on_disk)} 个文件）")
    print(f"待处理记录  : {len(rows)} 条")
    print("=" * 68)
    print(f"  已是文件名，无需修改 : {len(unchanged)}")
    print(f"  需要规范化           : {len(to_update)}")
    print(f"  无法提取文件名       : {len(unusable)}")
    print()

    if to_update:
        print("将要修改（最多展示 20 条）：")
        for import_id, old, new in to_update[:20]:
            print(f"  {import_id[:8]}  {old}")
            print(f"          -> {new}")
        if len(to_update) > 20:
            print(f"  …… 其余 {len(to_update) - 20} 条")
        print()

    if missing:
        print(f"⚠ 以下 {len(missing)} 条记录规范化后在磁盘上仍找不到对应文件；")
        print("  它们的原始文件已经丢失（导入数据本身不受影响，只是无法再重新解析）：")
        for import_id, name in missing:
            print(f"  {import_id[:8]}  {name}")
        print()

    if orphan_files:
        print(f"⚠ 磁盘上有 {len(orphan_files)} 个文件没有任何数据库记录引用（孤儿文件，可手动删除）：")
        for name in orphan_files:
            print(f"  {name}")
        print()

    if not to_update:
        print("无需迁移，数据已是规范状态。")
        connection.close()
        return 0

    if not args.apply:
        print("预演模式，未写入任何改动。确认无误后加 --apply 重新执行。")
        connection.close()
        return 0

    if not args.force:
        print("写入前请先停止 FitHealthAgent 服务，再加 --force 执行。", file=sys.stderr)
        connection.close()
        return 2

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from fithealth_agent.backup_service import LocalBackupService
    try:
        recovery_point = LocalBackupService(data_dir).write_recovery_point(prefix="pre-reset")
    except Exception as exc:
        print(f"恢复点创建失败，迁移已中止：{exc}", file=sys.stderr)
        connection.close()
        return 3
    print(f"已创建恢复点：{recovery_point['name']}")

    with connection:
        connection.executemany(
            "UPDATE health_imports SET raw_path = ? WHERE id = ?",
            [(new, import_id) for import_id, _old, new in to_update],
        )
    print(f"✓ 已更新 {len(to_update)} 条记录。")

    remaining = connection.execute(
        "SELECT COUNT(*) FROM health_imports WHERE raw_path LIKE '%/%' OR raw_path LIKE '%\\%'"
    ).fetchone()[0]
    print(f"复查：仍含路径分隔符的记录数 = {remaining}（应为 0）")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
