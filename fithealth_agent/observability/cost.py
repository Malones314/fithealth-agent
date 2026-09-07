"""observability/cost.py — 回合级 token 与成本汇总（阶段 5，修 TRACE-04）。

## 为什么要单独一个模块

`turn_end` 需要"这一轮一共花了多少"，而答案散在两类事件里：7 个外部模型触点的
`model_call`，和两个 ReAct 循环的 `react_step`。汇总逻辑本身是纯函数（事件列表 +
价格表 → 一组数字），既不该塞进 `trace.py`（那里管生命周期），也不能塞进
`model_trace.py`（`trace` → `model_trace` 会成环）。

## 三条口径（审查意见要求写明）

1. **0 不是"免费"，是"不知道"。** 框架在 `response.usage` 为空时把
   `usage.total_tokens` 写成 0，而它自己的 `cost` 更是**硬编码 0.0**。所以这里只累加
   正整数，其余计进 `usage_missing`——读方看到"5 次调用、2 次没有用量"就知道总数偏低，
   而不是以为真的这么便宜。
2. **没有价格表就不给成本。** `cost_basis="unknown"` 而不是 `cost_estimate=0`。
3. **算不了的部分要报出来。** 框架只给 ReAct 每步的 `total_tokens`，没有输入/输出
   拆分，而价格表是分方向的。这类 token 不硬算（按输出价会高估几倍，按输入价会低估），
   而是计进 `cost_unpriced_tokens`：`cost_estimate` 是**已定价那部分**的准确值，
   缺口有多大明写着。

跳过的调用（`model_call.ok is None`：关了联网模型、缺 key）不计入 `model_calls`——
它压根没发生，算进分母只会让"用量缺失率"这个数失去意义。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


#: 会贡献 token 的事件类型。`react_end` 刻意**不**在列：它记的是单个 role 的累计，
#: 与 `react_step` 逐步的值重复，一起算就会翻倍。
USAGE_KINDS = ("model_call", "react_step")


def _positive_int(value: Any) -> int | None:
    """只认正整数。bool 是 int 的子类，必须显式排掉。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def summarise_usage(
    events: Iterable[Mapping[str, Any]],
    *,
    prices: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """把一个回合的事件流汇总成 `turn_end` 的用量字段。绝不抛。

    Returns:
        只含**有意义**的键：没有任何调用时只有 `model_calls: 0`；没有用量时不写
        `total_tokens`（而不是写 0）；没有价格表时不写 `cost_estimate`。
    """
    table = prices or {}
    calls = missing = total = unpriced = priced = 0
    cost = 0.0
    try:
        for event in events:
            kind = event.get("kind")
            if kind not in USAGE_KINDS:
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if kind == "model_call" and payload.get("ok") is None:
                continue  # 根本没调，见模块头
            calls += 1
            tokens = _positive_int(payload.get("total_tokens"))
            if tokens is None:
                missing += 1
                continue
            total += tokens
            row = table.get(str(payload.get("model") or ""))
            prompt = _positive_int(payload.get("prompt_tokens"))
            completion = _positive_int(payload.get("completion_tokens"))
            if row and prompt is not None and completion is not None:
                cost += prompt * float(row.get("in", 0.0)) + completion * float(row.get("out", 0.0))
                priced += 1
            else:
                unpriced += tokens
    except Exception:  # noqa: BLE001 - 汇总失败不该挡住 turn_end 落盘
        return {"cost_basis": "unknown"}

    summary: dict[str, Any] = {"model_calls": calls}
    if calls:
        summary["usage_missing"] = missing
    if total:
        summary["total_tokens"] = total
    if priced:
        # 6 位小数：单价的量级是 1e-7/token，四舍五入到分是把结果抹成 0。
        summary["cost_estimate"] = round(cost, 6)
        summary["cost_basis"] = "estimated"
        if unpriced:
            summary["cost_unpriced_tokens"] = unpriced
    elif total:
        summary["cost_basis"] = "unknown"
    return summary


__all__ = ["USAGE_KINDS", "summarise_usage"]
