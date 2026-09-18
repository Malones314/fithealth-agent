# 前端模块化阶段 10 完成记录

> 完成日期：2026-09-14

## Health 所有权

- `domains/health/controller.ts` 通过 `healthApi` 接管 overview、daily、trend、range、sleep、
  导入审计、详情和删除请求，每类请求使用独立 operation key。
- 快速切换日期、指标或周期时，旧请求会被取消且无法提交状态；关闭概览会取消请求，
  响应返回后不会重绘隐藏面板。
- `domains/health/view.ts` 独占概览、趋势 DOM 和 SVG 渲染；累计热量摘要使用最后一个
  数据点，并明确说明静息热量按日速率均摊。

## Checkin 所有权

- `domains/checkin/controller.ts` 接管打开、切日、载入、保存、关闭与餐盘估算事件。
- state 保存服务器记录、日期、餐食草稿和已加载字段；同日保存保留字段合并语义，主动
  清空已加载字段时发送 `null`。
- `domains/checkin/view.ts` 只读写 checkin 自有 DOM；422 错误映射到具体表单字段。
- 营养归一化、上下限、合计和一致性校验位于 `shared/nutrition.ts`，日期与趋势摘要位于
  `shared/health-metrics.ts`。

## Runtime 与测试

阶段 10 从 runtime 删除 25 个函数、17 个监听、28 个 DOM 查询和 3 个直接请求，当前指标
为 58/64/90/5。health/checkin 的函数、API 路径和 DOM id 已由 runtime inventory 禁止。

Vitest 覆盖概览、累计趋势、旧响应、关闭取消、health API 事件、按日打卡、餐食估算、
保存、422 和 stop 清理。Playwright 继续覆盖三视口视觉与竞态，并增加真实打卡保存流程。
4 条 health 旧 Python 前端源码断言已迁移为行为测试。
