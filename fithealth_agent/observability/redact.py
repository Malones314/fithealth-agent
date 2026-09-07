"""observability/redact.py — 按 schema 裁剪 payload（agent-trace 阶段 1）。

## 摘要为什么用带密钥的 HMAC，而不是裸 sha256

`meta` 级别把自由文本换成"长度 + 摘要"，目的是既能判断"这两个回合是不是同一句
话"，又不落原文。但健康短语的熵很低——"膝盖疼""左肩酸"这类候选集小到可以穷举，
裸 sha256 等于明文。所以摘要走 HMAC，密钥优先取 `FITHEALTH_SIGNING_KEY`
（`runtime/deps.py` 里的餐盘分析签名已经在用同一把），没配就每进程随机生成。

**代价**：不配 `FITHEALTH_SIGNING_KEY` 时，摘要跨重启不可比。这是刻意的取舍——
默认更安全，需要跨重启比对的人自己配密钥。

## 未注册字段的处理

字段名来自我们自己的源码，值可能来自用户。所以名字列进 `dropped`（便于发现漏
注册），值一律丢弃。详见 `schema.py` 的说明。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from typing import Any

from .config import DETAIL_FULL
from .schema import EVENT_SPECS, LIMITS, S, T, TL


_SIGNING_KEY_ENV = "FITHEALTH_SIGNING_KEY"

#: (环境变量原值, 实际使用的密钥)。环境变量变了就重新派生，便于测试。
_key_cache: tuple[str | None, bytes] | None = None


def _signing_key() -> bytes:
    global _key_cache
    configured = os.environ.get(_SIGNING_KEY_ENV) or None
    if _key_cache is not None and _key_cache[0] == configured:
        return _key_cache[1]
    key = configured.encode("utf-8") if configured else os.urandom(32)
    _key_cache = (configured, key)
    return key


def text_digest(value: str) -> str:
    """自由文本的摘要：HMAC-SHA256 取前 12 个 hex。"""
    return hmac.new(
        _signing_key(), value.encode("utf-8", "replace"), hashlib.sha256
    ).hexdigest()[:12]


_SENTINEL = object()


def _structural(value: Any, depth: int) -> Any:
    """校验并裁剪一个 structural 值。不合规返回 `_SENTINEL`（由调用方丢弃）。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN / Infinity 会被 json.dumps 写成非法 JSON，读方直接崩。
        return value if math.isfinite(value) else _SENTINEL
    if isinstance(value, str):
        return value[: LIMITS.structural_chars]
    if depth <= 0:
        return _SENTINEL
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [
            item
            for item in (
                _structural(entry, depth - 1)
                for entry in list(value)[: LIMITS.list_items]
            )
            if item is not _SENTINEL
        ]
        return items
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, entry in list(value.items())[: LIMITS.list_items]:
            if not isinstance(key, str):
                continue
            item = _structural(entry, depth - 1)
            if item is not _SENTINEL:
                cleaned[key[: LIMITS.structural_chars]] = item
        return cleaned
    # 其余类型（datetime、Path、自定义对象…）一律不猜。调用方应自己转成基本类型，
    # 否则会静默出现 "PosixPath('/home/alice/...')" 这类顺手泄露。
    return _SENTINEL


def _text(name: str, value: Any, *, full: bool) -> dict[str, Any]:
    """把一个自由文本字段展开成落盘用的键值对。"""
    if value is None:
        return {}
    text = value if isinstance(value, str) else str(value)
    out: dict[str, Any] = {f"{name}_len": len(text), f"{name}_hmac12": text_digest(text)}
    if full:
        out[name] = text[: LIMITS.text_chars]
        if len(text) > LIMITS.text_chars:
            out[f"{name}_truncated"] = True
    return out


def _text_list(name: str, value: Any, *, full: bool) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, (str, bytes)):
        # 传了字符串而不是列表：按单元素处理，比整条丢掉有用。
        value = [value if isinstance(value, str) else value.decode("utf-8", "replace")]
    try:
        items = [item if isinstance(item, str) else str(item) for item in value]
    except TypeError:
        return {}
    kept = items[: LIMITS.list_items]
    out: dict[str, Any] = {
        f"{name}_count": len(items),
        f"{name}_hmac12": [text_digest(item) for item in kept],
    }
    if len(items) > len(kept):
        out[f"{name}_truncated"] = True
    if full:
        out[name] = [item[: LIMITS.text_chars] for item in kept]
    return out


def _serialised_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def redact_payload(
    kind: str, span: str, payload: dict[str, Any], *, detail: str
) -> tuple[dict[str, Any], list[str]]:
    """按 `kind`/`span` 的契约裁剪 payload。

    Returns:
        (可落盘的 payload, 被丢弃的字段名列表)

    绝不抛异常留给调用方兜——本函数自己保证：无论传进来什么，都返回一对合法值。
    """
    spec = EVENT_SPECS.get(kind)
    if spec is None:
        # 未注册的 kind：整条 payload 都没有契约可依，值全丢，只留名字。
        return {}, sorted(str(key) for key in payload)

    allowed = spec.allowed(span)
    full = detail == DETAIL_FULL
    clean: dict[str, Any] = {}
    dropped: list[str] = []

    for key, value in payload.items():
        name = str(key)
        field_class = allowed.get(name)
        if field_class is None:
            dropped.append(name)
            continue
        if field_class is T:
            clean.update(_text(name, value, full=full))
        elif field_class is TL:
            clean.update(_text_list(name, value, full=full))
        else:
            checked = _structural(value, LIMITS.depth)
            if checked is _SENTINEL:
                dropped.append(name)
            else:
                clean[name] = checked

    clean, oversize_dropped = _enforce_event_bytes(clean, kind, span, full=full)
    dropped.extend(oversize_dropped)
    return clean, sorted(set(dropped))


def _enforce_event_bytes(
    payload: dict[str, Any], kind: str, span: str, *, full: bool
) -> tuple[dict[str, Any], list[str]]:
    """兜住上面几条上限都没拦住的组合爆炸。

    两步降级，都不改变"哪些字段存在过"这个事实：
    1. `full` 级别下先把原文丢掉，只留长度与摘要——这一步通常就够了；
    2. 还超的话按序列化体积从大到小丢字段，丢到进上限为止。

    `_oversize` 标记必须留下：读方看到它就知道这条事件不完整，而不是以为程序
    本来只记了这么多。
    """
    if _serialised_size(payload) <= LIMITS.event_bytes:
        return payload, []

    dropped: list[str] = []
    if full:
        # 只有原文字段才有"降级为摘要"这一档：它们的 _len / _hmac12 已经在旁边了。
        for name in [key for key in payload if f"{key}_hmac12" in payload]:
            payload.pop(name, None)
            payload.pop(f"{name}_truncated", None)
            dropped.append(name)
        if _serialised_size(payload) <= LIMITS.event_bytes:
            payload["_oversize"] = True
            return payload, dropped

    ranked = sorted(
        payload.items(),
        key=lambda item: len(json.dumps(item[1], ensure_ascii=False)),
        reverse=True,
    )
    for name, _value in ranked:
        if _serialised_size(payload) <= LIMITS.event_bytes:
            break
        payload.pop(name, None)
        dropped.append(name)
    payload["_oversize"] = True
    return payload, dropped



