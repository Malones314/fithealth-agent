"""settings.py

应用运行配置的集中解析点。目前包括数据目录，以及主 ReAct Agent 的执行和模型参数。

为什么需要它
------------
所有 store 原先各自硬编码 `Path("data") / ...`，这是相对**当前工作目录**的
路径。带来两个问题：

1. 从非项目根目录启动，会在别处凭空建一个 `data/`，老数据看起来"消失"了；
2. 容器里数据目录不可配置，只能寄希望于有人把整个项目目录 bind-mount 进去。
   `docker-compose.yml` 原先只挂了 `./:/app`（为了源码热重载），一旦有人按
   正常方式跑镜像——不挂源码——所有健康数据就写在容器可写层里，**容器重建即
   全量丢失**。DATA-02 里那些 `/opt/project/...` 的绝对路径就是这个问题的实证：
   数据确实在环境之间漂移过。

约定
----
* 环境变量 `FITHEALTH_DATA_DIR` 指定数据目录；未设置时回落到仓库根目录的 `data`。
* 默认路径必须与当前工作目录无关。从其他目录启动脚本时，不能悄悄创建第二份数据。
* 数据目录和 Agent 参数每次调用都重新读环境变量，不做模块级缓存——同样是为了让
  测试能在运行中切换配置，而不必重新导入整个包。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


DATA_DIR_ENV = "FITHEALTH_DATA_DIR"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

AGENT_MAX_STEPS_ENV = "FITHEALTH_AGENT_MAX_STEPS"
LLM_TEMPERATURE_ENV = "LLM_TEMPERATURE"
LLM_MAX_TOKENS_ENV = "LLM_MAX_TOKENS"
LLM_TIMEOUT_ENV = "LLM_TIMEOUT"
LLM_MAX_RETRIES_ENV = "LLM_MAX_RETRIES"
_runtime_settings_provider: Callable[[], "AgentRuntimeSettings"] | None = None


class AgentSettingsError(ValueError):
    """Raised when an Agent runtime setting is invalid."""


@dataclass(frozen=True, slots=True)
class AgentRuntimeSettings:
    """Validated settings for the main and plan-correction ReAct agents."""

    max_steps: int = 15
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: int = 90
    max_retries: int = 0

    def __post_init__(self) -> None:
        _validate_int(AGENT_MAX_STEPS_ENV, self.max_steps, minimum=1, maximum=100)
        _validate_float(
            LLM_TEMPERATURE_ENV, self.temperature, minimum=0.0, maximum=2.0
        )
        if self.max_tokens is not None:
            _validate_int(LLM_MAX_TOKENS_ENV, self.max_tokens, minimum=1)
        _validate_int(LLM_TIMEOUT_ENV, self.timeout, minimum=1, maximum=600)
        _validate_int(LLM_MAX_RETRIES_ENV, self.max_retries, minimum=0, maximum=10)


def _validate_int(
    name: str,
    value: object,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentSettingsError(f"{name} 必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        expected = (
            f"{minimum}..{maximum}" if maximum is not None else f"不小于 {minimum}"
        )
        raise AgentSettingsError(f"{name} 必须为 {expected}")
    return value


def _validate_float(
    name: str, value: object, *, minimum: float, maximum: float
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgentSettingsError(f"{name} 必须是数字")
    converted = float(value)
    if not math.isfinite(converted) or not minimum <= converted <= maximum:
        raise AgentSettingsError(f"{name} 必须在 {minimum:g}..{maximum:g} 范围内")
    return converted


def _parse_int(
    environ: Mapping[str, str],
    name: str,
    default: int | None,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int | None:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AgentSettingsError(f"{name} 必须是整数，当前值为 {raw!r}") from exc
    return _validate_int(name, value, minimum=minimum, maximum=maximum)


def _parse_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise AgentSettingsError(f"{name} 必须是数字，当前值为 {raw!r}") from exc
    return _validate_float(name, value, minimum=minimum, maximum=maximum)


def load_agent_runtime_settings(
    environ: Mapping[str, str] | None = None,
) -> AgentRuntimeSettings:
    """Read and validate Agent settings without caching environment values."""

    if environ is None and _runtime_settings_provider is not None:
        return _runtime_settings_provider()
    source = os.environ if environ is None else environ
    return AgentRuntimeSettings(
        max_steps=_parse_int(
            source, AGENT_MAX_STEPS_ENV, 15, minimum=1, maximum=100
        ),
        temperature=_parse_float(
            source, LLM_TEMPERATURE_ENV, 0.7, minimum=0.0, maximum=2.0
        ),
        max_tokens=_parse_int(source, LLM_MAX_TOKENS_ENV, None, minimum=1),
        timeout=_parse_int(
            source, LLM_TIMEOUT_ENV, 90, minimum=1, maximum=600
        ),
        max_retries=_parse_int(
            source, LLM_MAX_RETRIES_ENV, 0, minimum=0, maximum=10
        ),
    )


def set_runtime_settings_provider(
    provider: Callable[[], AgentRuntimeSettings] | None,
) -> None:
    """Install the application-level persisted settings source."""
    global _runtime_settings_provider
    _runtime_settings_provider = provider


def data_dir() -> Path:
    """返回数据目录。设了环境变量就用它，否则使用仓库内绝对路径。"""
    configured = os.environ.get(DATA_DIR_ENV, "").strip()
    return Path(configured) if configured else DEFAULT_DATA_DIR


def data_path(*parts: str) -> Path:
    """拼出数据目录下的一个路径，例如 `data_path("health.db")`。"""
    return data_dir().joinpath(*parts)
