# Garmin恢复时间 bug 修复思路

## 1. 问题描述

当前肌群恢复计算的逻辑近似为：

```text
基础恢复剩余时间 = max(0, 预计恢复时刻 - 当前时刻)
最终恢复剩余时间 = 基础恢复剩余时间 + Garmin 恢复时间
```

这会导致一个已恢复的肌群被 Garmin 值“重新激活”。例如：

- 周一训练了 A 肌群；
- A 肌群的恢复窗口为 10 小时；
- 到周五时，A 肌群的基础恢复剩余时间已经是 `0`；
- 本次会话 Garmin 输入 `0.1` 小时；
- 当前结果变成 `0 + 0.1 = 0.1` 小时，A 肌群被错误地列为恢复中。

这不符合“Garmin 恢复时间是对仍在恢复肌群的补充估计”这一产品语义。Garmin 的当前建议不能让一个已经完成恢复的肌群倒退回恢复状态。

## 2. 根因

Garmin 加成目前放在 `max(0, baseline_remaining)` 之后，并且对所有进入快照的肌群无条件执行。这样计算过程丢失了一个重要状态信息：

```text
该肌群在 Garmin 加成之前，究竟是“仍在恢复”还是“已经恢复”
```

一旦剩余时间被截断成 `0`，后续逻辑无法区分“刚好还剩 0 小时”和“几天前就已经恢复”，于是 Garmin 的任意正数都会把两者都变成恢复中。

## 3. 推荐修复方案

### 3.1 先计算基础恢复状态，再有条件地应用 Garmin

计算顺序调整为：

1. 计算基础恢复窗口（包含训练容量调整）；
2. 应用用户酸痛状态对恢复窗口的影响；
3. 计算未加 Garmin 前的剩余时间 `baseline_remaining`；
4. 先判断肌群是否已经恢复；
5. 只有在 `baseline_remaining > 0` 时，才增加 Garmin 恢复时间；
6. 最终再次生成 `hours_remaining`、`recovered_at`、`recovering` 和 `ready`。

建议规则如下：

```text
baseline_remaining = max(0, adjusted_recovered_at - now)

如果用户明确反馈“已恢复”：
    final_remaining = 0
否则如果 baseline_remaining <= 0：
    final_remaining = 0
否则：
    final_remaining = baseline_remaining + garmin_recovery_hours
```

其中“用户明确反馈已恢复”仍然是最高优先级，Garmin 不得覆盖该结论；疼痛状态也继续走现有安全信号路径，不因 Garmin 值改变安全级别。

### 3.2 保持 Garmin 值的当前会话属性

本次修复只改变“Garmin 何时生效”，不改变已有输入契约：

- 合法范围仍为 `0–96.0`；
- 步长仍为 `0.1`；
- 只作用于当前会话；
- 不写入长期档案或酸痛记录；
- 不为从未训练的肌群创建恢复状态。

### 3.3 推荐的伪代码

```text
adjusted_recovered_at = last_trained_at + adjusted_recovery_hours
baseline_remaining = max(0, hours(adjusted_recovered_at - now))

if soreness_level == "recovered":
    final_remaining = 0
elif baseline_remaining <= 0:
    final_remaining = 0
else:
    final_remaining = baseline_remaining + garmin_recovery_hours

final_recovered_at = now + final_remaining
```

展示和优先级裁决全部使用 `final_remaining`。因此只要基础状态已经恢复，肌群就会继续进入“已恢复”区域，即使 Garmin 输入为 `0.1` 或更大值。

## 4. 示例结果

| 场景 | 基础剩余 | Garmin | 最终剩余 | 状态 |
|---|---:|---:|---:|---|
| 周一训练，周五查看，已恢复 | 0 | 0.1 | 0 | 已恢复 |
| 训练后还剩 2 小时 | 2 | 0.1 | 2.1 | 恢复中 |
| 训练后还剩 0.05 小时 | 0.05 | 0.1 | 0.15 | 恢复中 |
| 已恢复且 Garmin 为 0 | 0 | 0 | 0 | 已恢复 |
| 明确反馈已恢复 | 任意 | 任意 | 0 | 已恢复 |
| 明确反馈疼痛 | 任意 | 任意 | 按安全路径处理 | 安全阻断/提示 |

这里的关键边界是 `baseline_remaining <= 0`，而不是 Garmin 值是否为零。

## 5. 对现有功能的影响

### 5.1 恢复状态展示

- 基础上已经恢复的肌群不再因 Garmin 正值出现在“正在恢复”列表；
- 仍在恢复的肌群继续显示 Garmin 加成后的剩余小时；
- “正在恢复”仍排在上方；“已恢复”仍排在下方并用 `~~~~...~~~~` 包裹。

### 5.2 PlanContext 优先级

- Garmin 只会延长原本处于恢复中的肌群；
- 不会压制每周计划中已经恢复的肌群；
- 不会生成虚假的 `recovering`、`reduce` 或 `blocking_reasons`；
- 现有“明确当前指令高于恢复建议”的规则保持不变。

### 5.3 数据与备份

无需改变酸痛存储、备份、恢复或重置协议，因为 bug 位于恢复快照的派生计算层，Garmin 值本身也不持久化。

## 6. 验收测试建议

至少增加以下测试：

1. 构造一个基础恢复窗口为 10 小时、训练时间距当前超过 10 小时的肌群，Garmin 输入 `0.1`，断言 `hours_remaining == 0`，且归入 `ready` 而非 `recovering`；
2. 构造一个基础剩余 2 小时的肌群，Garmin 输入 `0.1`，断言最终剩余为 2.1 小时；
3. 覆盖基础剩余恰好为 `0`、略大于 `0` 和略小于 `0` 的边界；
4. 断言已恢复肌群的 `recovered_at` 不会因 Garmin 正值被推迟；
5. 断言同一快照中，已恢复肌群仍排在恢复中肌群下方，并继续使用 `~~~~...~~~~` 展示；
6. 断言 PlanContext 不会因为已恢复肌群 + Garmin 正值而新增恢复约束；
7. 保持输入边界测试：`96.0` 接受，`96.1` 拒绝，步长 `0.1` 不变。

## 7. 不推荐的修复方式

### 方案 A：把 Garmin 加成改成 `max(baseline_remaining + garmin, 0)`

不推荐。它只能处理负数，无法解决本 bug，因为 `baseline_remaining` 已经是 `0`，结果仍然是 Garmin 值本身。

### 方案 B：全局直接忽略小于 1 小时的 Garmin 值

不推荐。这会误伤仍在恢复中的肌群，例如基础剩余 0.5 小时、Garmin 0.1 小时的情况仍然应该得到 0.6 小时。

### 方案 C：只在 UI 层隐藏这类肌群

不推荐。这样后端快照和 PlanContext 仍会把肌群视为恢复中，展示、计划裁决和接口数据会互相矛盾。

## 8. 结论

应把 Garmin 恢复时间定义为“对基础上尚未恢复肌群的额外恢复时间”，而不是“对所有肌群的全局延迟”。修复重点是保留并使用 Garmin 加成前的恢复状态：

```text
已恢复 → 保持 0，不接受 Garmin 重新激活
未恢复 → 在基础剩余时间上增加 Garmin 值
```

这样既符合用户直觉，也不会改变当前会话输入、酸痛优先级和安全阻断的既有设计。
