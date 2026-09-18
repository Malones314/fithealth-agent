# 前端模块化阶段 9 完成记录

> 完成日期：2026-09-11

## 领域所有权

- `domains/workout` 接管服务器快照、编辑草稿、pending/saved 模式、选择集合、确认凭据、
  revision 和 conflict 状态；轮询不会覆盖聚焦中的草稿，stop 会取消计时器和请求。
- 历史训练由 `workout:edit-saved` 进入同一编辑器，保存通过 `workoutApi.updateSavedRecord`
  携带 revision；409 保留当前草稿并显式进入 conflict。
- `domains/uploads` 接管 FIT、计划、健康 ZIP/CSV 批次、餐盘图片和多 ZIP 活动选择；活动
  使用 `zip + "\\0" + name` 唯一标识，并把选中活动对应的 ZIP 交给 typed API。
- 上传结果通过 `workout:loaded`、`chat:submit-plan`、`chat:message`、`checkin:food`
  交给其他领域，不安装 `window.openActivityPicker` 等兼容出口。

## Runtime 收缩

阶段 9 从 runtime 删除 workout/uploads 的 35 个函数、17 个监听、34 个 DOM 查询和 8 个
直接请求。当前剩余 83/81/118/8，归属于阶段 10-11 的 health、checkin、chat、
data-management 和应用布局。完成领域的函数、API 路径及 DOM id 均由 inventory 自动禁止。

## 测试迁移

workout/uploads 的 Vitest 覆盖草稿隔离、轮询保护、撤销/恢复、历史 revision、409、FIT
事件、多 ZIP 同名活动、源 ZIP 选择、校验、重复上传和取消。Playwright 多 ZIP 基线改为
真实文件输入。9 条旧 Python 前端源码断言已删除，后端行为测试继续保留。
