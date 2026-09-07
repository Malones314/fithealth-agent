"""observability/config.py — trace 的环境变量解析（agent-trace 阶段 1）。

本模块**只做纯解析**，不碰文件系统：目录归属、权限与剩余空间的校验在 `sink.py`
落盘前做（那里有缓存，不会每个回合都探一次盘）。这样分层的好处是 `load_settings()`
可以在每个回合调用而不产生 I/O。

## 两条硬约定

1. **默认关闭**。`FITHEALTH_TRACE` 不设或不是 `on` 就整套停用，现有部署行为零变化。
2. **非法值一律安全关闭**，不抛异常、不猜意图。可观测性配置写错不该让服务起不来，
   但也绝不能"猜一个值继续跑"——那会让人以为 trace 开着，其实记的是别的东西。

每次调用都重读环境变量，不做模块级缓存——与 `settings.data_dir()` 同一约定，
让测试能在运行中切换而不必重新导入整个包。同一条非法值只告警一次，避免刷日志。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..settings import data_dir


logger = logging.getLogger("fithealth")

ENV_ENABLED = "FITHEALTH_TRACE"
ENV_DETAIL = "FITHEALTH_TRACE_DETAIL"
ENV_DIR = "FITHEALTH_TRACE_DIR"
ENV_STREAM = "FITHEALTH_TRACE_STREAM"
ENV_MAX_TURNS = "FITHEALTH_TRACE_MAX_TURNS"
ENV_MAX_DAYS = "FITHEALTH_TRACE_MAX_DAYS"
ENV_MAX_BYTES = "FITHEALTH_TRACE_MAX_BYTES"
ENV_PRICE_JSON = "FITHEALTH_TRACE_PRICE_JSON"

#: 数据目录下的子目录名（与 `tool_output.DIR_NAME` 同一套做法）。
DIR_NAME = "traces"

DETAIL_META = "meta"
DETAIL_FULL = "full"
DETAIL_LEVELS = (DETAIL_META, DETAIL_FULL)

DEFAULT_MAX_TURNS = 500
DEFAULT_MAX_DAYS = 14
DEFAULT_MAX_BYTES = 256 * 1024 * 1024

#: 已告警过的 (变量名, 原值) 组合。只为压制重复日志，不参与任何判断。
_warned: set[tuple[str, str]] = set()


def _warn_once(variable: str, raw: str, reason: str) -> None:
    key = (variable, raw)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning("trace 配置 %s=%r 无效（%s），已按安全值处理", variable, raw, reason)


@dataclass(frozen=True)
class TraceSettings:
    """一个回合开始时生效的 trace 配置快照。

    做成 frozen 是刻意的：一个回合中途不该因为有人改了环境变量而换脱敏级别，
    否则同一个文件里会前后两种口径。
    """

    enabled: bool
    detail: str
    directory: Path
    stream: bool
    max_turns: int
    max_days: int
    max_bytes: int
    prices: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    @property
    def records_raw_text(self) -> bool:
        """是否会把用户原文落盘。只有 `full` 会。"""
        return self.detail == DETAIL_FULL


#: 全套停用时返回的实例。`directory` 仍然给一个合法路径，避免调用方拿到 None。
def _disabled(directory: Path) -> TraceSettings:
    return TraceSettings(
        enabled=False,
        detail=DETAIL_META,
        directory=directory,
        stream=False,
        max_turns=DEFAULT_MAX_TURNS,
        max_days=DEFAULT_MAX_DAYS,
        max_bytes=DEFAULT_MAX_BYTES,
        prices={},
    )


def trace_dir() -> Path:
    """返回 trace 目录。默认锚在数据目录下，随 Docker 卷走（修 TRACE-12）。"""
    configured = os.environ.get(ENV_DIR, "").strip()
    return Path(configured) if configured else data_dir() / DIR_NAME


def _positive_int(variable: str, fallback: int) -> int | None:
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        _warn_once(variable, raw, "不是整数")
        return None
    if value <= 0:
        _warn_once(variable, raw, "必须大于 0")
        return None
    return value


def _prices(raw: str) -> Mapping[str, Mapping[str, float]] | None:
    """解析价格表。格式错误一律降级为"成本未知"（空表），不猜。"""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        _warn_once(ENV_PRICE_JSON, raw[:80], "不是合法 JSON")
        return {}
    if not isinstance(parsed, dict):
        _warn_once(ENV_PRICE_JSON, raw[:80], "顶层必须是对象")
        return {}
    table: dict[str, dict[str, float]] = {}
    for model, entry in parsed.items():
        if not isinstance(model, str) or not isinstance(entry, dict):
            _warn_once(ENV_PRICE_JSON, str(model)[:40], "条目必须是 模型名 -> 对象")
            return {}
        row: dict[str, float] = {}
        for direction in ("in", "out"):
            value = entry.get(direction)
            # 单价必须是非负实数。bool 是 int 的子类，这里要显式排掉。
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _warn_once(ENV_PRICE_JSON, f"{model}.{direction}", "单价必须是数字")
                return {}
            if value < 0:
                _warn_once(ENV_PRICE_JSON, f"{model}.{direction}", "单价不能为负")
                return {}
            row[direction] = float(value)
        table[model] = row
    return table


def load_settings() -> TraceSettings:
    """读一次环境变量，返回本回合生效的配置。纯函数，不产生任何 I/O。"""
    directory = trace_dir()

    raw_enabled = os.environ.get(ENV_ENABLED, "").strip()
    if raw_enabled.casefold() != "on":
        # 空值是默认状态，不算配置错误，不告警；写了别的东西才提醒一句。
        if raw_enabled and raw_enabled.casefold() != "off":
            _warn_once(ENV_ENABLED, raw_enabled, "只接受 on / off")
        return _disabled(directory)

    raw_detail = os.environ.get(ENV_DETAIL, DETAIL_META).strip().casefold() or DETAIL_META
    if raw_detail not in DETAIL_LEVELS:
        _warn_once(ENV_DETAIL, raw_detail, f"只接受 {' / '.join(DETAIL_LEVELS)}")
        return _disabled(directory)

    limits = {
        ENV_MAX_TURNS: _positive_int(ENV_MAX_TURNS, DEFAULT_MAX_TURNS),
        ENV_MAX_DAYS: _positive_int(ENV_MAX_DAYS, DEFAULT_MAX_DAYS),
        ENV_MAX_BYTES: _positive_int(ENV_MAX_BYTES, DEFAULT_MAX_BYTES),
    }
    if any(value is None for value in limits.values()):
        # 保留上限非法时不能"先记着、以后再说"：那正是无限增长的开始。
        return _disabled(directory)

    prices = _prices(os.environ.get(ENV_PRICE_JSON, ""))

    if raw_detail == DETAIL_FULL:
        # full 是唯一会把健康原文落盘的开关，每次生效都要留一条痕。
        logger.warning(
            "trace 以 %s=%s 运行：用户消息、计划正文与记忆原文都会明文落盘到 %s，"
            "请仅在本地排障时使用",
            ENV_DETAIL,
            DETAIL_FULL,
            directory,
        )

    return TraceSettings(
        enabled=True,
        detail=raw_detail,
        directory=directory,
        stream=os.environ.get(ENV_STREAM, "").strip() == "1",
        max_turns=limits[ENV_MAX_TURNS],
        max_days=limits[ENV_MAX_DAYS],
        max_bytes=limits[ENV_MAX_BYTES],
        prices=prices,
    )



