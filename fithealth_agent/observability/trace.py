"""observability/trace.py — 回合级 trace 的记录入口（agent-trace 阶段 1）。

## 公开 API 与保证

| 函数 | 返回 | 异常保证 |
| --- | --- | --- |
| `trace_event(kind, span, **payload)` | 事件 `seq`，未记录时 `None` | 绝不抛 |
| `start_turn(route, ...)` | 上下文管理器，产出 `TurnTrace \\| None` | 只透传业务异常 |
| `span(kind, name, **payload)` | 上下文管理器 | 只透传业务异常 |
| `set_turn_result(**fields)` | `None` | 绝不抛 |
| `current_turn()` | `TurnTrace \\| None` | 绝不抛 |

**"绝不抛"是硬承诺**：可观测性故障不能变成业务故障。所有失败降级为一条 warning
（每回合最多 3 条，防刷日志），并计入 `turn_end.trace_errors`——降级必须可见，
否则 trace 坏掉会静默累积到没人发现。

## 关键约束（决定 A 的落地细节）

`chat()` 里 5 处 `run_in_threadpool` 会把执行切到工作线程。已实测 starlette 的
`run_in_threadpool` 会传播 contextvars，但工作线程拿到的是 **Context 的副本**：
线程内 `var.set()` 不会回传主线程。所以：

* `TurnTrace` 是**可变对象**，只在异步层 `set` 一次，工作线程只往里 append；
* append 走 `threading.Lock`——同步 ReAct 是串行的，但框架的
  `_execute_tools_async` 是并行的；
* **事件顺序只看 `seq`**，不看落盘顺序，也不看 `ts`（并行工具的墙钟会交错）。

**已知边界**：裸 `threading.Thread` 起的线程拿到的是**空 Context**，记不进当前
回合。要让事件进 turn，必须走 `run_in_threadpool` / `asyncio.to_thread` /
`contextvars.copy_context().run`——这三者都复制上下文。本项目现有的 5 处线程切换
都是 `run_in_threadpool`，所以没问题；加后台线程的人需要知道这条。

## 嵌套规则

* **嵌套 turn**：`start_turn` 在已有回合内是空操作，产出同一个 `TurnTrace`，
  不会写出第二个文件。HTTP 边界与 workflow 都可能开 turn，谁先谁定。
* **嵌套 span**：`span()` 自带 `sid`，并把外层的 `sid` 记成 `parent`。span 栈存在
  ContextVar 里（不可变元组），所以工作线程里开的 span 天然挂在它自己的外层下。
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from . import sink
from .config import TraceSettings, load_settings
from .cost import summarise_usage
from .redact import redact_payload
from .schema import EVENT_SPECS, LIMITS, SCHEMA_VERSION


logger = logging.getLogger("fithealth")

#: 全项目统一的时区锚点（与 `chat_workflow` / `weekly_summary` 一致）。
#: 日期目录、`ts` 和保留天数都按它算，避免"跨时区日期边界"含糊。
TZ = ZoneInfo("Asia/Shanghai")

#: 每回合最多打几条降级告警，超出只计数不打日志。
_MAX_WARNINGS_PER_TURN = 3

#: `turn_end` 里由生命周期独占的字段。调用方通过 `set_turn_result` 传同名字段会被
#: 拒绝：`status` 是 ok/error/aborted 这个三态，被业务的 status（比如 logout 的
#: saved/not_saved）覆盖掉之后，"这次到底是不是崩了"就再也读不出来了。
#:
#: 用量与成本（阶段 5）同样独占：它们由 `cost.summarise_usage` 从事件流算出，是可复算的
#: 派生值。允许调用方另报一份，就等于允许两个数字对不上而没人知道哪个对。
RESERVED_TURN_END_FIELDS = frozenset({
    "status", "complete", "events_dropped", "fields_dropped", "trace_errors",
    "total_tokens", "model_calls", "usage_missing",
    "cost_estimate", "cost_basis", "cost_unpriced_tokens",
})


def _producer() -> str:
    """产出方版本。用 importlib.metadata 读，**不 import** hello_agents。

    直接 `import hello_agents` 会把整个 LLM 栈拉进来，破坏
    `test_package_lazy_import.py` 守着的惰性契约。
    """
    def _version(distribution: str, fallback: str) -> str:
        try:
            return metadata.version(distribution)
        except Exception:  # noqa: BLE001 - metadata 缺失不该影响记录
            return fallback

    # 本项目按源码目录运行（README 明说不提供 PyPI wheel），所以常态是 "src"。
    return (
        f"fithealth/{_version('fithealth-agent', 'src')}"
        f" hello-agents/{_version('hello-agents', '?')}"
    )


def new_turn_id(when: datetime) -> str:
    """`t-<YYYYMMDD>-<HHMMSS>-<6位hex>`。

    随机后缀用 `secrets`（密码学安全源），而不是 `uuid4().hex[:6]` 之外的弱源：
    turn_id 会出现在文件名里，可预测的 id 等于可预测的路径。撞名仍有兜底，见
    `sink.reserve_turn_path`。
    """
    return f"t-{when.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


@dataclass
class TurnTrace:
    """一次用户回合的事件缓冲区。

    只在异步层构造一次，工作线程共享同一个实例（见模块头的约束说明）。
    """

    turn_id: str
    route: str
    request_id: str
    started_at: datetime
    settings: TraceSettings
    path: Path | None
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    events_dropped: int = 0
    fields_dropped: int = 0
    trace_errors: int = 0
    _seq: int = 0
    _sid: int = 0
    _warnings: int = 0
    _finished: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def degrade(self, message: str, *args: Any) -> None:
        """记一次降级。前几次打日志，之后只计数。"""
        with self._lock:
            self.trace_errors += 1
            should_log = self._warnings < _MAX_WARNINGS_PER_TURN
            if should_log:
                self._warnings += 1
        if should_log:
            logger.warning("trace 降级（turn=%s）：" + message, self.turn_id, *args)

    def next_span_id(self) -> int:
        with self._lock:
            self._sid += 1
            return self._sid

    def add(
        self,
        kind: str,
        span: str = "",
        *,
        step: int | None = None,
        dur_ms: int | None = None,
        sid: int | None = None,
        parent: int | None = None,
        **payload: Any,
    ) -> int | None:
        """裁剪并追加一条事件，返回它的 `seq`；未记录时返回 None。

        这里**会**抛异常（例如调用方传了一个 `__str__` 就炸的对象）；由
        `trace_event` 兜住并降级。分工是刻意的：`add` 保持可测的直白语义，
        "绝不抛"的承诺集中在唯一的公开入口上。
        """
        spec = EVENT_SPECS.get(kind)
        unknown_kind = spec is None
        unknown_span = bool(spec and spec.spans is not None and span not in spec.spans)

        clean, dropped = redact_payload(kind, span, payload, detail=self.settings.detail)
        if unknown_kind:
            # 名字来自我们自己的源码，照记；值已经在 redact 里全丢了。
            clean["_unknown_kind"] = True
        if unknown_span:
            clean["_unknown_span"] = True
        if dropped:
            clean["_dropped"] = dropped

        event: dict[str, Any] = {
            "v": SCHEMA_VERSION,
            "turn_id": self.turn_id,
            "kind": kind,
            "span": span,
            "ts": datetime.now(TZ).isoformat(timespec="milliseconds"),
        }
        for name, value in (("step", step), ("dur_ms", dur_ms), ("sid", sid), ("parent", parent)):
            if value is not None:
                event[name] = value
        event["payload"] = clean

        with self._lock:
            if self._finished:
                # 回合已经落盘。再记就会写出一条永远读不到的事件，直接丢掉更诚实。
                self.events_dropped += 1
                return None
            if len(self.events) >= LIMITS.events_per_turn:
                self.events_dropped += 1
                return None
            self._seq += 1
            event["seq"] = self._seq
            self.fields_dropped += len(dropped)
            self.events.append(event)
            streaming = self.settings.stream and self.path is not None

        if streaming:
            sink.append_event(self.path, event)
        return event["seq"]


_current: ContextVar[TurnTrace | None] = ContextVar("fithealth_turn_trace", default=None)
_span_stack: ContextVar[tuple[int, ...]] = ContextVar("fithealth_trace_spans", default=())
#: 递归护栏。`add` 内部触发的失败绝不能再走一次记录路径。
_recording: ContextVar[bool] = ContextVar("fithealth_trace_recording", default=False)


def current_turn() -> TurnTrace | None:
    """当前回合，不在回合内返回 None。绝不抛。"""
    try:
        return _current.get()
    except Exception:  # noqa: BLE001
        return None


def trace_event(kind: str, span: str = "", **fields: Any) -> int | None:
    """全局记录入口。

    trace 关闭或不在回合内时，代价就是一次 `ContextVar.get()`——这是"关闭时零开销"
    这条验收标准的落地点，不要在这个分支之前做任何工作。
    """
    trace = _current.get()
    if trace is None:
        return None
    if _recording.get():
        # 记录过程本身触发的再次记录：丢掉，只计数。否则一次坏 payload 能把栈打穿。
        trace.trace_errors += 1
        return None
    token = _recording.set(True)
    try:
        return trace.add(kind, span, **fields)
    except Exception as exc:  # noqa: BLE001 - 可观测性故障不能变成业务故障
        trace.degrade("事件记录失败 kind=%s span=%s（%s）", kind, span, type(exc).__name__)
        return None
    finally:
        _recording.reset(token)


def set_turn_result(**fields: Any) -> None:
    """暂存 `turn_end` 的字段。

    为什么不让调用方直接 `trace_event("turn_end", ...)`：那样无法保证 exactly-once
    ——异常路径会漏记，重试路径会记两条。改成暂存 + 由 `start_turn` 的 finally
    统一写出，`turn_end` 就恒好是一条，`turn_id` 天然可去重。

    `RESERVED_TURN_END_FIELDS` 里的名字会被**拒绝并告警**，而不是静默丢弃：传它们
    进来一定是调用方搞错了字段名（比如把 logout 的业务 `status` 当成生命周期
    `status`），静默丢掉只会让人以为记上了。
    """
    trace = _current.get()
    if trace is None:
        return
    try:
        reserved = sorted(RESERVED_TURN_END_FIELDS.intersection(fields))
        if reserved:
            trace.degrade("turn_end 保留字段不可覆盖：%s", "、".join(reserved))
        trace.result.update(
            {name: value for name, value in fields.items() if name not in RESERVED_TURN_END_FIELDS}
        )
    except Exception:  # noqa: BLE001
        trace.degrade("turn 结果暂存失败")


@contextmanager
def span(kind: str, name: str, **fields: Any) -> Iterator[None]:
    """自动计时的一段执行。退出时记一条事件，异常时带 `error_type`。

    只在退出时记一条（而不是进出各一条）：事件量减半，而顺序信息由 `sid`/`parent`
    保留，不依赖开闭配对。
    """
    trace = _current.get()
    if trace is None:
        yield
        return
    sid = trace.next_span_id()
    stack = _span_stack.get()
    token = _span_stack.set(stack + (sid,))
    started = time.perf_counter()
    error_type: str | None = None
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 - 只为记录，随后原样抛出
        error_type = type(exc).__name__
        raise
    finally:
        _span_stack.reset(token)
        payload = dict(fields)
        if error_type is not None:
            payload.setdefault("error_type", error_type)
        trace_event(
            kind,
            name,
            sid=sid,
            parent=stack[-1] if stack else None,
            dur_ms=int((time.perf_counter() - started) * 1000),
            **payload,
        )


@contextmanager
def start_turn(route: str, **meta: Any) -> Iterator[TurnTrace | None]:
    """开一个回合。**只在异步层调用**（工作线程里 set 不会回传）。

    嵌套调用是空操作：产出已有的 `TurnTrace`，不会写出第二个文件。

    退出时（含异常、含取消）必定写出恰好一条 `turn_end` 并落盘：
    * 正常退出 → `status="ok"`；
    * `Exception` → `status="error"` + `error_type`；
    * `BaseException`（`asyncio.CancelledError` / `KeyboardInterrupt` / `SystemExit`）
      → `status="aborted"` + `error_type`。取消要能和真失败分开，否则一次用户关页面
      看起来就像一次服务错误。
    """
    existing = _current.get()
    if existing is not None:
        yield existing
        return

    settings = load_settings()
    if not settings.enabled:
        # 关闭时不解析目录、不建目录、不写文件，也不设 ContextVar。
        yield None
        return

    started_at = datetime.now(TZ)
    turn_id = new_turn_id(started_at)
    path = sink.reserve_turn_path(settings, turn_id, started_at)
    if path is None:
        yield None
        return

    trace = TurnTrace(
        turn_id=turn_id,
        route=route,
        request_id=str(meta.pop("request_id", "") or secrets.token_hex(8)),
        started_at=started_at,
        settings=settings,
        path=path,
    )
    token = _current.set(trace)
    status, error_type = "ok", None
    try:
        trace_event(
            "turn_start",
            route,
            route=route,
            request_id=trace.request_id,
            detail_level=settings.detail,
            tz=str(TZ),
            producer=_producer(),
            **meta,
        )
        yield trace
    except Exception as exc:
        status, error_type = "error", type(exc).__name__
        raise
    except BaseException as exc:  # noqa: BLE001 - 取消/中断要与真失败分开
        status, error_type = "aborted", type(exc).__name__
        raise
    finally:
        _current.reset(token)
        _finalise(trace, status=status, error_type=error_type)


def _finalise(trace: TurnTrace, *, status: str, error_type: str | None) -> None:
    """写 `turn_end` 并落盘。本函数绝不抛。"""
    token = _current.set(trace)  # turn_end 也要走同一条记录路径
    try:
        duration_ms = int(
            (datetime.now(TZ) - trace.started_at).total_seconds() * 1000
        )
        result = dict(trace.result)
        result.setdefault("route", trace.route)
        if error_type is not None:
            result.setdefault("error_type", error_type)
        # 用量汇总必须在写 turn_end **之前**算，而且要在锁内取快照：并行工具线程
        # 理论上还可能在 append（`_execute_tools_async`），边迭代边追加会漏事件。
        with trace._lock:  # noqa: SLF001 - 同模块内
            recorded = list(trace.events)
        result.update(summarise_usage(recorded, prices=trace.settings.prices))
        trace_event(
            "turn_end",
            trace.route,
            dur_ms=duration_ms,
            status=status,
            # 只有正常写出 turn_end 的文件才是完整的。硬崩溃时文件里没有这一行，
            # 读方据此判断"尾部事件丢了"，而不是以为程序只记了这么多。
            complete=True,
            events_dropped=trace.events_dropped,
            fields_dropped=trace.fields_dropped,
            trace_errors=trace.trace_errors,
            **result,
        )
    except Exception:  # noqa: BLE001
        trace.degrade("turn_end 记录失败")
    finally:
        _current.reset(token)

    with trace._lock:  # noqa: SLF001 - 同模块内，_finished 必须与 add 互斥
        if trace._finished:
            return
        trace._finished = True
        events = list(trace.events)
        path = trace.path
        streamed = trace.settings.stream

    if path is None:
        return
    try:
        if streamed:
            # stream 模式下事件已经逐条落过盘了，这里只需要 index。
            written = True
        else:
            written = sink.write_turn(path, events)
        sink.append_index(
            trace.settings,
            {
                "v": SCHEMA_VERSION,
                "turn_id": trace.turn_id,
                "request_id": trace.request_id,
                "route": trace.route,
                "started_at": trace.started_at.isoformat(timespec="milliseconds"),
                "status": status,
                "events": len(events),
                "detail_level": trace.settings.detail,
                "file": path.name if written else None,
                "day": path.parent.name,
            },
        )
        # 保留策略排在 index 追加**之后**：prune 会把"回合文件已不在盘上"的索引行丢掉，
        # 顺序反了的话本回合那条刚写的行会被自己的 prune 当成陈旧行清掉。
        pruned = sink.prune(trace.settings, today=trace.started_at.date())
        if pruned.changed:
            logger.debug(
                "trace 保留策略清理了 %d 个回合 / %d 字节 / %d 个日期目录",
                pruned.turns_removed, pruned.bytes_removed, pruned.days_removed,
            )
    except Exception:  # noqa: BLE001
        logger.warning("trace 回合 %s 落盘失败", trace.turn_id, exc_info=True)







