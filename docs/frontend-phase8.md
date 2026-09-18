# 前端模块化阶段 8 完成记录

> 完成日期：2026-09-11

## 领域所有权

- `domains/session/state.ts` 保存会话结束状态、外部模型状态、Garmin 恢复小时、酸痛提示
  区域和有界对话历史。
- `domains/session/controller.ts` 通过依赖注入调用 session/settings/health API，处理启动、
  表单、设置、连通性、退出和 stop 清理。
- `domains/session/view.ts` 只操作 session 在 `dom-ownership.json` 中拥有的 DOM。
- `app/shell.ts` 提供经过 sanitize 的通用消息入口，session intro 不再直接操作 chat DOM。
- `app/bootstrap.ts` 传入 shell、operations 和 document，并提供可重复的 `destroy()`。

## API 与兼容边界

新增 `api/session.ts`，`/session/intro` 和 `/logout` 均经过统一 client。logout 使用后端真实
字段 `{messages}`，不再使用旧 adapter 的 `{history}`。runtime 暂时订阅 session state，
只负责在 chat/uploads/workout 尚未迁移前同步禁用对应控件并停止轮询；它不再注册
session/settings 的按钮事件或发起这些 API 请求。

## 验证重点

- 启动并发加载 intro、外部模型设置、档案和存储状态。
- Garmin 0-96 小时校验、旧请求取消和最新响应守卫。
- 设置失败回滚、403 连通性提示、重复点击退出只提交一次。
- stop 移除 DOM 监听，pagehide 调用应用 destroy 并取消全部在飞请求。
