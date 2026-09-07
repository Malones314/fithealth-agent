"""observability/sink.py — trace 落盘与保留策略（agent-trace 阶段 1 + 6）。

## 职责边界

写：目录校验、单回合原子写、index 追加、stream 追加模式。
清：保留策略（`prune`）与治理入口（`TraceStore`，供 `/data/reset` 与"恢复备份后"用）。

两件事放在一个模块里是因为它们共用同一份目录布局知识——"哪个文件属于我们"这个判断
只能有一处，否则清理迟早会漏掉一类产物（`index.jsonl.lock` 就是最容易漏的那个）。

## 两个文件，一套提交策略

```
<trace_dir>/<YYYY-MM-DD>/turn-<turn_id>.jsonl   一个回合的全部事件
<trace_dir>/index.jsonl                          每回合一行摘要
```

回合文件走 `atomic_write_text`（fsync 临时文件再原子替换）；index 是追加，用
`JsonFileLock` 串行化跨进程写入，写完 fsync。两者都不做"先写一半再回头补"的事，
所以任何时刻读到的都是完整行。

## stream 模式

`FITHEALTH_TRACE_STREAM=1` 时逐事件追加，用于排查挂死（缓冲模式下进程卡住就什么
都看不到）。代价是没有原子性：崩溃可能留下半行。默认关闭。

## 保留策略（TRACE-09）

三条上限**同时**生效，任一超限就从最旧开始删：回合数、天数、目录总字节。规则细节与
两条刻意的取舍（永不删最新那个回合、index 只在真删了东西时才重写）见 `prune`。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..atomic_json import atomic_write_text
from ..json_file_lock import JsonFileLock
from .config import TraceSettings, trace_dir


logger = logging.getLogger("fithealth")

INDEX_NAME = "index.jsonl"
TURN_PREFIX = "turn-"

#: 剩余空间低于这个数就不再写 trace。诊断产物不该是把盘写满的那根稻草。
MIN_FREE_BYTES = 64 * 1024 * 1024

#: 同一秒 + 同一随机后缀撞车时的重试上限（时钟回拨或多进程）。
_COLLISION_ATTEMPTS = 10

#: 日期目录名。ISO 日期的字典序等于时间序，排序与比较都直接用字符串。
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 认得出来的**根目录**产物。`clear()` 只删这些，绝不递归乱删：
#: `FITHEALTH_TRACE_DIR` 可以指向任何地方，用户往里放的别的东西不该被顺手抹掉
#: （与 `tool_output.clear_tool_output` 同一纪律）。`.lock` 是 `JsonFileLock`
#: 建的，`.write-probe-*` 是 `ensure_writable` 建的——这两类最容易在清理时被漏掉。
_ROOT_GLOBS = (INDEX_NAME, f"{INDEX_NAME}.lock", "*.tmp", ".write-probe-*")

#: 日期目录内的产物。`*.tmp` 是 `atomic_write_text` 崩溃时可能留下的残片。
_DAY_GLOBS = (f"{TURN_PREFIX}*.jsonl", "*.tmp")

#: 已校验过的目录 -> 是否可写。避免每个回合都探一次盘。
_writable: dict[Path, bool] = {}


def reset_writable_cache() -> None:
    """清空目录校验缓存。测试切换目录后调用；生产不需要。"""
    _writable.clear()


def ensure_writable(directory: Path) -> bool:
    """校验 trace 目录可用。任何一项不满足都返回 False，由调用方整体停用。

    校验四件事（审查意见）：不是符号链接、能创建、能写、剩余空间够。
    结果按目录缓存——这几项都要真的碰盘，不能每个回合来一遍。
    """
    key = directory
    cached = _writable.get(key)
    if cached is not None:
        return cached

    ok = False
    try:
        # 符号链接直接拒绝：一条指向别处的软链会让"数据只落在数据目录内"这条
        # 承诺静默失效，而症状只是"备份里没有 trace"。
        if directory.is_symlink():
            logger.warning("trace 目录 %s 是符号链接，已停用 trace", directory)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(directory).free
            if free < MIN_FREE_BYTES:
                logger.warning(
                    "trace 目录 %s 剩余空间 %d 字节，低于 %d，已停用 trace",
                    directory, free, MIN_FREE_BYTES,
                )
            else:
                probe = directory / f".write-probe-{os.getpid()}"
                probe.write_text("", encoding="utf-8")
                probe.unlink(missing_ok=True)
                ok = True
    except OSError as exc:
        logger.warning("trace 目录 %s 不可用（%s），已停用 trace", directory, exc)

    _writable[key] = ok
    return ok


def day_dir(settings: TraceSettings, when: datetime) -> Path:
    """按本地日期分目录。`when` 必须带时区，日期边界跟着它走。"""
    return settings.directory / when.date().isoformat()


def reserve_turn_path(
    settings: TraceSettings, turn_id: str, when: datetime
) -> Path | None:
    """为一个回合定下落盘路径，返回 None 表示这次不落盘。

    撞名会重试：`turn_id` 已经带了 6 字节随机后缀，但系统时钟回拨或多进程仍可能
    产生同名。宁可写成 `turn-<id>-1.jsonl`，也不能悄悄覆盖别人的回合。
    """
    if not settings.enabled or not ensure_writable(settings.directory):
        return None
    target_dir = day_dir(settings, when)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("trace 日期目录 %s 创建失败：%s", target_dir, exc)
        return None

    base = target_dir / f"{TURN_PREFIX}{turn_id}.jsonl"
    if not base.exists():
        return base
    for suffix in range(1, _COLLISION_ATTEMPTS):
        candidate = target_dir / f"{TURN_PREFIX}{turn_id}-{suffix}.jsonl"
        if not candidate.exists():
            logger.warning("trace 回合文件 %s 已存在，改写 %s", base.name, candidate.name)
            return candidate
    logger.warning("trace 回合 %s 连续撞名 %d 次，本回合不落盘", turn_id, _COLLISION_ATTEMPTS)
    return None


def _line(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def append_event(path: Path, event: dict[str, Any]) -> None:
    """stream 模式：逐事件追加。失败只降级为一条 warning。"""
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_line(event))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        logger.warning("trace 事件追加失败（%s）：%s", path.name, exc)


def write_turn(path: Path, events: Iterable[dict[str, Any]]) -> bool:
    """缓冲模式：把整个回合一次原子写出去。"""
    try:
        atomic_write_text(path, "".join(_line(event) for event in events))
    except OSError as exc:
        logger.warning("trace 回合写入失败（%s）：%s", path.name, exc)
        return False
    return True


def append_index(settings: TraceSettings, summary: dict[str, Any]) -> None:
    """把一行摘要追加到 index。

    用 `JsonFileLock` 串行化：index 是**唯一**被多进程/多线程同时追加的文件，
    没有锁的话两条摘要可能拼在一行，而症状只是"列表里少了一个回合"。
    """
    index_path = settings.directory / INDEX_NAME
    try:
        with JsonFileLock(index_path):
            with index_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(_line(summary))
                handle.flush()
                os.fsync(handle.fileno())
    except (OSError, TimeoutError) as exc:
        # 索引丢一行不影响回合文件本身，所以只降级告警，不往上抛。
        logger.warning("trace index 追加失败：%s", exc)


# ── 保留策略（阶段 6）────────────────────────────────────────────────────


@dataclass(frozen=True)
class PruneResult:
    """一次保留策略执行的结果。给日志与测试用，主流程不看它。"""

    turns_removed: int = 0
    bytes_removed: int = 0
    days_removed: int = 0
    #: 删到只剩最新一个回合仍然超字节上限（单个回合过大）。见 `prune` 的取舍说明。
    over_budget: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.turns_removed or self.days_removed)


@dataclass(frozen=True)
class _TurnFile:
    day: str
    name: str
    path: Path
    size: int

    @property
    def sort_key(self) -> tuple[str, str]:
        """(日期目录, 文件名)。两段都是 ISO 时间前缀，字典序即时间序。

        同一秒内的多个回合只差 6 字节随机后缀，它们之间的先后是任意的——保留策略
        以回合为粒度，同秒内删哪个都不改变"删的是最旧那一批"这个结论。
        """
        return (self.day, self.name)


def _turn_files(directory: Path) -> list[_TurnFile]:
    """全部回合文件，**最旧在前**。

    用 `os.scandir` 而不是 `Path.glob` + `Path.stat()`：`DirEntry.stat()` 的
    `st_size` 在 Windows 上由目录枚举一次带回，不再额外发系统调用。`prune` 每个回合
    都要跑一遍，这个差别直接决定它是"几乎免费"还是"每次请求多几百次 stat"。
    """
    found: list[_TurnFile] = []
    try:
        # `os.scandir` 必须及时关闭：Windows 上没关的目录句柄会让随后的 `rmdir` 失败，
        # 症状是"空日期目录删不掉"而没有任何报错。
        with os.scandir(directory) as scan:
            days = [
                (entry.name, entry.path) for entry in scan
                if entry.is_dir(follow_symlinks=False) and _DAY_DIR.match(entry.name)
            ]
    except OSError:
        return []
    for day_name, day_path in days:
        try:
            with os.scandir(day_path) as scan:
                for entry in scan:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if not (entry.name.startswith(TURN_PREFIX) and entry.name.endswith(".jsonl")):
                        continue
                    found.append(
                        _TurnFile(day_name, entry.name, Path(entry.path), entry.stat().st_size)
                    )
        except OSError:
            continue
    found.sort(key=lambda item: item.sort_key)
    return found


def _doomed(entries: list[_TurnFile], settings: TraceSettings, today: date) -> tuple[set[_TurnFile], bool]:
    """三条上限**同时**判断，返回 (该删的集合, 是否仍然超字节预算)。

    顺序是刻意的：先按天数和回合数筛，再用**剩下的**文件算字节。被前两条判掉的文件
    反正要走，把它们算进字节账只会让字节规则多删无辜的文件。
    """
    doomed: set[_TurnFile] = set()
    # `max_days=1` 表示"只留今天"，所以 cutoff 是 today-(N-1) 而不是 today-N。
    cutoff = (today - timedelta(days=settings.max_days - 1)).isoformat()
    doomed.update(entry for entry in entries if entry.day < cutoff)
    if len(entries) > settings.max_turns:
        doomed.update(entries[: len(entries) - settings.max_turns])

    remaining = [entry for entry in entries if entry not in doomed]
    total = sum(entry.size for entry in remaining)
    index = 0
    # `len(remaining) - 1`：**永不删最新那个回合**。一个超大回合本身就超上限时，
    # 删光其余回合也救不回来，而把刚刚出问题的那次对话抹掉恰恰是最坏的结果。
    while total > settings.max_bytes and index < len(remaining) - 1:
        total -= remaining[index].size
        doomed.add(remaining[index])
        index += 1
    return doomed, total > settings.max_bytes


def prune(settings: TraceSettings, *, today: date) -> PruneResult:
    """按三条上限清理旧回合。**绝不抛**：清理失败不该影响一次已经答完的对话。

    `today` 由调用方给（`trace._finalise` 传 `datetime.now(TZ).date()`）：日期目录是
    按本地时区命名的，让本模块自己取"今天"就等于让它自己猜时区。

    整个过程持 index 的 `JsonFileLock`，于是跨进程只有一个 prune 在跑，也不会与
    `append_index` 交错。删除本身对"文件已经不在了"是容忍的（另一个进程可能刚删过）。
    """
    if not settings.enabled:
        return PruneResult()
    directory = settings.directory
    try:
        with JsonFileLock(directory / INDEX_NAME):
            return _prune_locked(directory, settings, today)
    except (OSError, TimeoutError) as exc:
        # 只读目录、权限不足、锁超时：全都只降级为一条告警。消息里只有目录名和
        # 异常文本，不含任何回合内容。
        logger.warning("trace 保留策略未能执行（%s）：%s", directory, exc)
        return PruneResult()


def _prune_locked(directory: Path, settings: TraceSettings, today: date) -> PruneResult:
    entries = _turn_files(directory)
    doomed, over_budget = _doomed(entries, settings, today)
    removed = bytes_removed = 0
    touched_days: set[str] = set()
    for entry in sorted(doomed, key=lambda item: item.sort_key):
        try:
            entry.path.unlink()
        except FileNotFoundError:
            continue  # 另一个 prune 已经删了，不算失败
        except OSError as exc:
            logger.warning("trace 回合文件删除失败（%s）：%s", entry.name, exc)
            continue
        removed += 1
        bytes_removed += entry.size
        touched_days.add(entry.day)

    days_removed = sum(_remove_day_if_empty(directory / day) for day in sorted(touched_days))
    result = PruneResult(removed, bytes_removed, days_removed, over_budget)
    if result.changed:
        # 只在真删了东西时才重写 index：它是 O(N) 的原子写，而 prune 每个回合都跑。
        _rewrite_index(directory)
    if over_budget:
        logger.warning(
            "trace 目录 %s 仍超过 %d 字节上限：最新回合单独超限，已保留它",
            directory, settings.max_bytes,
        )
    return result


def _remove_day_if_empty(day: Path) -> int:
    """回合文件删空之后把日期目录也收掉，否则会攒下一堆空目录。

    `scandir` 用 `with`：Windows 上没关的目录句柄会让 `rmdir` 直接失败。
    """
    try:
        with os.scandir(day) as scan:
            if next(scan, None) is not None:
                return 0
    except OSError:
        return 0
    try:
        day.rmdir()
    except OSError:
        return 0
    return 1


def _rewrite_index(directory: Path) -> None:
    """只保留"回合文件还在盘上"的那些行。

    index 描述的是**盘上有什么**。已被清理的行、以及当初落盘失败留下的
    `file: null` 行一起丢掉：留着它们会让索引无限增长，而且指向不存在的文件。
    调用方必须已经持有 index 锁。
    """
    index_path = directory / INDEX_NAME
    try:
        raw = index_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("trace index 读取失败，本次不重写：%s", exc)
        return

    kept: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # 半行（stream 模式崩溃）直接丢
        name, day = row.get("file"), row.get("day")
        if isinstance(name, str) and isinstance(day, str) and (directory / day / name).is_file():
            kept.append(line + "\n")
    try:
        atomic_write_text(index_path, "".join(kept))
    except OSError as exc:
        logger.warning("trace index 重写失败：%s", exc)


# ── 治理入口（阶段 6，TRACE-06）──────────────────────────────────────────


class TraceStore:
    """trace 目录的治理入口：`/data/reset` 的一步，以及"恢复备份后清空"。

    做成实例而不是模块函数，是为了和其余 11 个 store 一样能被
    `mock.patch.object(deps, "trace_store", ...)` **整体替换**（见 `runtime/deps.py`
    开头那段约定）。构造时刻意**不碰文件系统**：`deps` 在应用启动时就 import，
    在那里建目录会破坏"trace 关闭即零副作用"。

    路径每次调用重新解析（`trace_dir()` 每次重读环境变量），并且**不看
    `FITHEALTH_TRACE` 开关**——关掉 trace 之前留下的产物同样要能删掉，否则"删除全部
    数据"会漏掉一整个目录的健康细节。
    """

    @property
    def directory(self) -> Path:
        return trace_dir()

    def clear(self) -> int:
        """删掉全部 trace 产物，返回删除的**文件**数。

        **绝不抛**：它同时挂在 `/data/reset` 的一步和备份恢复的 `on_restored` 回调上。
        后者尤其要紧——恢复已经生效之后再抛异常，会让一次成功的恢复被报成失败。
        逐个文件独立处理，单个文件被占用（Windows 上很常见）不影响其余项。

        幂等：第二次调用返回 0。
        """
        directory = self.directory
        try:
            if directory.is_symlink() or not directory.is_dir():
                return 0
            with os.scandir(directory) as scan:
                day_dirs = sorted(
                    Path(entry.path) for entry in scan
                    if entry.is_dir(follow_symlinks=False) and _DAY_DIR.match(entry.name)
                )
            removed = sum(self._clear_day(day) for day in day_dirs)
            for pattern in _ROOT_GLOBS:
                removed += _unlink_matching(directory, pattern)
            return removed
        except OSError as exc:
            logger.warning("trace 目录清理失败（%s）：%s", directory, exc)
            return 0

    @staticmethod
    def _clear_day(day: Path) -> int:
        removed = sum(_unlink_matching(day, pattern) for pattern in _DAY_GLOBS)
        _remove_day_if_empty(day)
        return removed


def _unlink_matching(directory: Path, pattern: str) -> int:
    """删掉目录里匹配 `pattern` 的文件（不递归），返回删除数。绝不抛。"""
    removed = 0
    try:
        candidates = sorted(directory.glob(pattern))
    except OSError:
        return 0
    for item in candidates:
        if item.is_symlink() or not item.is_file():
            continue
        try:
            item.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("trace 产物删除失败（%s）：%s", item.name, exc)
        else:
            removed += 1
    return removed



