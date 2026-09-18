#!/usr/bin/env python3
"""DATA-05 回灌：把被误隔离的待确认训练救成一条正常训练记录。

背景
----
`data/` 下的两个 `pending_workout.corrupt-*.json` **根本没有损坏**。它们被搬走
是因为一条早期的校验 `if not segments_data: raise ValueError("训练分段为空")`
—— 而空 sets 对有氧/跳绳/无 lap 的 FIT 是完全正常的（这段活动的
`sport_raw=training / sub_sport=cardio_training` 还同时踩中了 DATA-04）。其中
`pending_workout.corrupt-20260816T085939230364Z.json` 含 **1802 条 1Hz 心率**
和一段完整 session：2026-08-15 18:52 北京时间、64.2 分钟、232 kcal、
均心率 92 / 峰值 147。

心率流灌到哪里
--------------
**不进 `health.db`**。那张 `heart_rate_samples` 表存的是全天 1 分钟级监测数据，
日汇总与趋势按"每个采样点等权"算均值。实测 2026-08-15 已有 1212 个分钟级
时间点、且完整覆盖这段训练窗口；再灌 1802 条 1Hz 进去，训练那一小时会被
加权约 30 倍，当天日均心率从 78.7 直接失真。

改为走 `HRStreamStore` 旁挂：训练记录里只留一份体积恒定的摘要
（条数/最小/最大/均值/覆盖窗口），原始流按记录 id 存成独立文件。这样
`query_daily_records` 交给 ReAct 的观察不会被 1802 个点撑爆，而 Python
需要重算区间心率时仍能按 id 取回完整流。

幂等性
------
用 `idempotency_key = fit:<source_sha256>` 走 `DailyRecordStore.add_record`，
与正常确认流程同一把钥匙；另外先用 `find_training_by_start` 查同日期+同运动+
起始时间 60 秒内的既有记录。重复执行安全。

用法
----
    python scripts/backfill_quarantined_hr.py            # 预演，只报告不写入
    python scripts/backfill_quarantined_hr.py --apply    # 实际写入（会先备份 daily_records.json）
    python scripts/backfill_quarantined_hr.py --apply --file pending_workout.corrupt-XXX.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
# 跟随 FITHEALTH_DATA_DIR（DATA-10 / ARCH-03），否则容器或自定义目录下会
# 在错误的位置找隔离文件。相对路径按仓库根解析，保持本机直跑时的老行为。
_CONFIGURED_DATA_DIR = os.environ.get("FITHEALTH_DATA_DIR", "").strip()
DATA_DIR = (
    Path(_CONFIGURED_DATA_DIR)
    if Path(_CONFIGURED_DATA_DIR).is_absolute()
    else REPO_ROOT / (_CONFIGURED_DATA_DIR or "data")
)
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")


def load_module(name: str):
    """按文件路径加载模块，绕开 fithealth_agent/__init__.py 的 LLM 依赖链（ARCH-02）。"""
    if str(REPO_ROOT) not in sys.path:
        # storage.py 内部会 `from fithealth_agent.json_file_lock import ...`，
        # 所以仓库根目录必须在 sys.path 上；但包的 __init__ 仍然不会被执行，
        # 因为我们是按文件路径加载目标模块本身。
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        f"{name}_for_backfill", REPO_ROOT / "fithealth_agent" / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def collect_samples(payload: dict) -> tuple[list[dict], list[str]]:
    """清洗 hr_records：去重、排序、剔除越界值。"""
    warnings: list[str] = []
    rows: list[dict] = []
    seen: set[str] = set()
    for item in payload.get("hr_records") or []:
        if not isinstance(item, dict):
            warnings.append("跳过一个非对象的心率采样点")
            continue
        moment = _parse_dt(item.get("timestamp"))
        bpm = item.get("heart_rate")
        if moment is None or bpm is None:
            warnings.append("跳过一个缺少时间戳或心率值的采样点")
            continue
        try:
            bpm_int = int(bpm)
        except (TypeError, ValueError):
            warnings.append("跳过一个心率值非法的采样点")
            continue
        if not 20 <= bpm_int <= 250:
            # 与 health_importer 的心率区间过滤保持同一口径
            warnings.append(f"跳过越界心率值 {bpm_int}")
            continue
        key = moment.astimezone(timezone.utc).isoformat()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"timestamp": key, "heart_rate": bpm_int})
    rows.sort(key=lambda row: row["timestamp"])
    return rows, warnings


def describe(path: Path, payload: dict, rows: list[dict]) -> str:
    session = payload.get("session") or {}
    start = _parse_dt(session.get("start_time"))
    return (
        f"{path.name}\n"
        f"  运动类型   : {session.get('sport') or '未知'}"
        f"（原始 {session.get('sport_raw') or '?'} / {session.get('sub_sport') or '?'}）\n"
        f"  开始时间   : {start.astimezone(BEIJING).isoformat() if start else '缺失'}（北京时间）\n"
        f"  时长/热量  : {round(float(session.get('total_timer_s') or 0) / 60, 1)} 分钟"
        f" / {session.get('total_calories') or 0} kcal\n"
        f"  均心率/峰值: {session.get('avg_hr')} / {session.get('max_hr')}\n"
        f"  分段数     : {len(payload.get('sets') or [])}\n"
        f"  可救心率   : {len(rows)} 条"
    )


def build_record(payload: dict, start_beijing: datetime, rows: list[dict]) -> tuple[str, str, dict]:
    """构造与 workout_store._save_confirmed_workout 同形状的训练记录。"""
    session = payload.get("session") or {}
    sport = str(session.get("sport") or "未知运动")
    segments = [item for item in (payload.get("sets") or []) if isinstance(item, dict)]
    active = [item for item in segments if not item.get("is_rest")]
    record = {
        "name": f"{start_beijing:%y-%m-%d-%H-%M}-{sport}",
        "source_file": str(payload.get("source_file") or ""),
        "source_sha256": str(payload.get("source_sha256") or ""),
        "parsed_at": str(payload.get("parsed_at") or ""),
        "sport": sport,
        "session": session,
        "segments": segments,
        "total_sets": len(active),
        "total_reps": sum(int(item.get("repetitions") or 0) for item in active),
        "note": str(payload.get("note") or ""),
        "workout_start_time_beijing": start_beijing.isoformat(),
        "recovered_from_quarantine": True,
        "recovery_note": (
            "由 scripts/backfill_quarantined_hr.py 从被误隔离的待确认训练恢复（DATA-05）。"
            "sets 为空是因为原始 FIT 的 sport=training 被路由给力量训练解析器（DATA-04），"
            "lap 数据在解析阶段已丢失；session 摘要与 1Hz 心率流完好。"
        ),
    }
    cat_key = "training" if "strength" in sport or "训练" in sport else sport
    return sport, cat_key, record


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="实际写入（默认仅预演）")
    parser.add_argument("--force", action="store_true", help="确认服务已停止并允许写入")
    parser.add_argument(
        "--file",
        action="append",
        default=None,
        help="只处理指定的隔离文件名（可重复）；默认处理全部含心率的隔离文件",
    )
    args = parser.parse_args()

    if args.file:
        candidates = []
        for name in args.file:
            safe_name = Path(name).name
            legacy = DATA_DIR / safe_name
            current = DATA_DIR / "workout-quarantine" / safe_name
            candidates.append(current if current.exists() else legacy)
    else:
        candidates = sorted(DATA_DIR.glob("pending_workout.corrupt-*.json"))
        candidates += sorted((DATA_DIR / "workout-quarantine").glob("pending_workout.corrupt-*.json"))
    if not candidates:
        print("未找到任何隔离的待确认训练文件，无需回灌。")
        return 0

    plans: list[tuple[Path, dict, datetime, list[dict]]] = []
    for path in candidates:
        if not path.exists():
            print(f"跳过 {path.name}：文件不存在")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            print(f"跳过 {path.name}：无法解析（{exc}）")
            continue
        if not isinstance(payload, dict):
            print(f"跳过 {path.name}：根节点不是对象")
            continue
        rows, warnings = collect_samples(payload)
        print(describe(path, payload, rows))
        for warning in warnings[:5]:
            print(f"  ! {warning}")
        if len(warnings) > 5:
            print(f"  ! 另有 {len(warnings) - 5} 条同类告警")
        start = _parse_dt((payload.get("session") or {}).get("start_time"))
        if start is None:
            print("  → 缺少有效开始时间，无法定位到某一天，跳过\n")
            continue
        if not rows and not (payload.get("sets") or []):
            print("  → 既无分段也无心率，没有可救的内容，跳过\n")
            continue
        plans.append((path, payload, start.astimezone(BEIJING), rows))
        print()

    if not plans:
        print("没有需要回灌的内容。")
        return 0

    total = sum(len(rows) for _, _, _, rows in plans)
    if not args.apply:
        print(f"[预演] 共 {len(plans)} 个文件、{total} 条心率待回灌。加 --apply 实际执行。")
        return 0
    if not args.force:
        print("写入前请先停止 FitHealthAgent 服务，再加 --force 执行。")
        return 2

    storage = load_module("storage")
    hr_streams = load_module("hr_stream_store")

    from fithealth_agent.backup_service import LocalBackupService
    try:
        recovery_point = LocalBackupService(DATA_DIR).write_recovery_point(prefix="pre-reset")
    except Exception as exc:
        print(f"恢复点创建失败，回灌已中止：{exc}")
        return 3
    print(f"已创建恢复点：{recovery_point['name']}")

    store = storage.DailyRecordStore()
    stream_store = hr_streams.HRStreamStore()

    written = 0
    for path, payload, start_beijing, rows in plans:
        sport, cat_key, record = build_record(payload, start_beijing, rows)
        derived_date = start_beijing.date().isoformat()
        duplicate = store.find_training_by_start(
            date=derived_date, sport=sport, start_time_beijing=start_beijing.isoformat()
        )
        if duplicate is not None:
            # 常见情形：用户后来重新导入过同一份 FIT，训练记录本身并没丢，
            # 丢的只是确认时被丢弃的 1Hz 心率流。那就只补心率流，绝不覆盖
            # 用户可能已经编辑过的记录正文。
            existing = duplicate.get("record") if isinstance(duplicate.get("record"), dict) else {}
            if not rows:
                print(f"{path.name}：记录 {duplicate['id']} 已存在且无心率可补，跳过")
                continue
            existing_stream = existing.get("hr_stream")
            if isinstance(existing_stream, dict) and existing_stream.get("samples"):
                print(f"{path.name}：记录 {duplicate['id']} 已带心率流，跳过")
                continue
            summary = stream_store.save(duplicate["id"], rows)
            store.update_record(
                duplicate["id"],
                date=str(duplicate.get("date") or derived_date),
                category=str(duplicate.get("category") or cat_key),
                record={**existing, "hr_stream": summary},
            )
            written += len(rows)
            print(
                f"{path.name}：已把 {summary.get('samples')} 条心率补挂到既有记录 "
                f"{duplicate['id']}（均 {summary.get('avg')} / 峰 {summary.get('max')}）"
                "，记录正文未改动"
            )
            continue
        digest = str(payload.get("source_sha256") or "")
        saved = store.add_record(
            date=derived_date,
            category=cat_key,
            record=record,
            idempotency_key=f"fit:{digest}" if digest else None,
        )
        if saved.get("idempotent_replay"):
            print(f"{path.name}：已回灌过（record {saved['id']}），跳过")
            continue
        summary = stream_store.save(saved["id"], rows) if rows else {"samples": 0}
        if rows:
            store.update_record(
                saved["id"],
                date=derived_date,
                category=cat_key,
                record={**record, "hr_stream": summary},
            )
        written += len(rows)
        print(
            f"{path.name}：已恢复为训练记录 {saved['id']}"
            f"（{derived_date} {sport}，心率 {summary.get('samples', 0)} 条"
            f"，均 {summary.get('avg')} / 峰 {summary.get('max')}）"
        )

    print(f"\n完成，本次新写入 {written} 条心率采样点。")
    print("隔离文件已保留在原处，未删除——确认数据无误后再手动清理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
