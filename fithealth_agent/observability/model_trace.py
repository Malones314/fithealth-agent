"""observability/model_trace.py — 外部模型调用的记录器（agent-trace 阶段 4）。

## 为什么不是计划里那个装饰器

计划的草图是"装饰器记一条 + 每个 `except` 分支再记一条"，那会给**一次调用产生两条
`model_call` 事件**，任何按 span 计数的统计都会翻倍——审查意见点出的正是这个。

这里改成**一次调用一个记录器**：上下文管理器持有那唯一一条事件，函数内部只往上
"标注"（跳过 / HTTP 结果 / 回落原因 / 成功结论），退出时统一写出。于是：

* 一次调用 ⇒ 恰好一条事件，`call_id` 天然唯一，不需要额外的合并规则；
* `except` 分支只标注，不记录，所以不可能与外层重复；
* 忘记标注也不会漏事件——退出时仍然会写，只是 `ok` 停在初始值。

## `ok` 的三态（审查意见要求统一语义）

| `ok` | 含义 | 典型 `reason` 字段 |
| --- | --- | --- |
| `None` | **根本没调**：配置关掉、缺 key、输入不适用 | `skipped_reason` |
| `False` | 调了但没用上，已回落到确定性结果 | `fallback_reason` / `error_type` |
| `True` | 调通且结果被采用 | `result` |

这三态就是 BUG-02 的答案：以前"意图为空"分不清是用户没这个意图（`ok=True`
+ `result="none"`）、还是路由挂了（`ok=False`）、还是压根没调（`ok=None`）。

**跳过原因的优先级**（先判先记，避免同一次调用被归到两个原因）：
`external_models_disabled` → `no_api_key` → `empty_input`。配置关闭是用户的显式
选择，缺 key 是部署问题，输入不适用是业务判断——三者的处理人完全不同。

## 刻意不记的东西

异常**消息**、请求体、响应体、API key、图片内容。只记规范化的异常类名
（`type(exc).__name__`）与体积。异常消息里常常带 URL 和参数，那是最容易顺手泄露的
地方；schema 里也没有能装下它们的字段（`observability/schema.py` 是白名单）。

## 支持范围

只支持**同步**函数——6 个触点都是裸 `requests.post`，没有 async、没有流式、没有
框架重试。`attempt` / `retry_count` 字段留着，但当前恒为 1 / 0；真加重试时应该在
同一个 `model_call()` 块内递增，而不是开第二个记录器。
"""

from __future__ import annotations

import secrets
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit

from .schema import MODEL_CALL_SPANS
from .trace import trace_event


#: 跳过原因的优先级（越前越优先）。调用方按这个顺序判断，统计口径才不会重复。
SKIP_REASONS = ("external_models_disabled", "no_api_key", "empty_input")


def endpoint_host(url: str) -> str:
    """只留主机名。完整 URL 里可能带 query 参数，不进 trace。"""
    try:
        return urlsplit(str(url)).hostname or ""
    except ValueError:
        return ""


class ModelCall:
    """一次外部模型调用的记录句柄。所有方法都不抛异常。"""

    __slots__ = ("span", "_fields")

    def __init__(self, span: str) -> None:
        self.span = span
        self._fields: dict[str, Any] = {
            "call_id": secrets.token_hex(4),
            # 初始值是"调了但没标注结论"，比默认成功保守：忘记标注会显示成回落，
            # 而不是显示成一次成功的调用。
            "ok": False,
            "attempt": 1,
            "retry_count": 0,
            "usage_source": "unknown",
        }

    @property
    def call_id(self) -> str:
        return str(self._fields["call_id"])

    def _set(self, **fields: Any) -> None:
        self._fields.update({name: value for name, value in fields.items() if value is not None})

    def skipped(self, reason: str) -> None:
        """根本没调模型。`ok` 置 None，与"调了但失败"区分开。"""
        self._fields["ok"] = None
        self._fields["skipped_reason"] = reason

    def request(self, *, model: str = "", url: str = "", request_bytes: int | None = None) -> None:
        self._set(model=model or None, endpoint_host=endpoint_host(url) or None,
                  request_bytes=request_bytes)

    def http(self, status: int | None = None, usage: Any = None) -> None:
        """记 HTTP 状态与 token 用量。

        `usage_source` 区分"供应商回的"和"不知道"——框架里那个硬编码的
        `cost: 0.0` 就是把"不知道"当成了"零成本"，不能重犯（TRACE-04）。
        """
        self._set(http_status=status)
        if isinstance(usage, dict):
            self._set(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            if any(
                isinstance(usage.get(name), int)
                for name in ("prompt_tokens", "completion_tokens", "total_tokens")
            ):
                self._fields["usage_source"] = "provider"

    def response(self, body: Any) -> None:
        """从 OpenAI 兼容响应体里一次性抽出 `usage` 与 `finish_reason`。

        为什么一定要记 `finish_reason`：`classify_user_health_statement` 的
        `max_tokens` 曾经设成 40，模型还没写完 JSON 就被截断，解析失败后静默返回
        `None`——而现场只能看到"解析失败"，推不出是**被截断**还是模型返回了垃圾。
        `finish_reason="length"` 一眼就能定性。这一条是阶段 4 用真实调用发现的。
        """
        if not isinstance(body, dict):
            return
        self.http(usage=body.get("usage"))
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reason = choices[0].get("finish_reason")
            if isinstance(reason, str):
                self._set(finish_reason=reason)

    def fallback(self, reason: str) -> None:
        """调了但结果没用上，已回落到确定性结果。"""
        self._fields["ok"] = False
        self._fields["fallback_reason"] = reason

    def failed(self, exc: BaseException) -> None:
        """异常导致的回落。只记规范化类名，不记消息——消息里常带 URL 和参数。

        **首因优先**：已经记过 `error_type` 就不覆盖。调用点常常把底层异常包成自己的
        业务异常再抛（`analyze_food_image` 抛 `FoodAnalysisError from exc`），而那层
        包装每次都一样、没有信息量；真正想知道的是 `ConnectTimeout` 还是 `HTTPError`。
        `model_call` 退出时的兜底捕获会第二次调用本方法，所以这条规则是必需的。

        **不覆盖已标注的 skipped**：有的调用点是"配置缺失 → 记跳过 → 抛异常告知
        用户"（`analyze_food_image` 就是），那次调用确实从未发生，`ok` 应当留在
        `None`，而不是被异常改写成"调了但失败"。
        """
        self._fields.setdefault("error_type", type(exc).__name__)
        if self._fields["ok"] is None:
            return
        self._fields["ok"] = False
        self._fields.setdefault("fallback_reason", type(exc).__name__)

    def succeeded(self, result: str = "") -> None:
        """结果被采用。`result` 是一个**短的结构化记号**，不是模型输出原文。"""
        self._fields["ok"] = True
        self._fields.pop("fallback_reason", None)
        if result:
            self._fields["result"] = result


@contextmanager
def model_call(span: str) -> Iterator[ModelCall]:
    """包住一次外部模型调用，退出时写出**恰好一条** `model_call` 事件。

    绝不改变返回值，也绝不改变异常传播——这 6 个函数的静默回落是**有意的**设计
    （本地数据不能基于猜测的意图去改），trace 只负责让回落变得可见。

    未捕获的异常会被标成 `ok=False` 后原样抛出：调用方的 `except` 之外还有别的
    异常路径（比如 `json.dumps` 炸了），那些也不该在 trace 里显示成成功。
    """
    handle = ModelCall(span)
    started = time.perf_counter()
    try:
        yield handle
    except BaseException as exc:  # noqa: BLE001 - 只为标注，随后原样抛出
        handle.failed(exc)
        raise
    finally:
        trace_event(
            "model_call",
            span,
            dur_ms=int((time.perf_counter() - started) * 1000),
            **handle._fields,  # noqa: SLF001 - 同模块内的私有累加器
        )


__all__ = ["MODEL_CALL_SPANS", "ModelCall", "SKIP_REASONS", "endpoint_host", "model_call"]


