# 前端模块化阶段 11 完成记录

> 完成日期：2026-09-14

## Chat 所有权

- `domains/chat` 接管会话历史、发送与取消、Markdown 消息、计划 artifact、`draft_id`、
  计划校验和档案更新确认。
- chat 通过 `AppState.sessionContext` 使用共享会话快照，通过应用事件同步外部模型状态，
  不跨领域导入 session 私有实现。
- 档案确认卡展示器械新增/移除差异，计划保存始终原样传递后端草稿 ID。

## Data-management 所有权

- `domains/data-management` 接管记录、计划、记忆事实、酸痛、健康导入、文件审计、隔离
  训练、恢复点、备份导入导出、重置与训练/营养查看器。
- 记忆事实优先使用稳定 `fact_id`，支持确认、拒绝、编辑和回滚；周计划无效日期会明确
  展示。隔离训练支持预览、载入、不再提醒和永久删除。
- 计划预览下载当前 Markdown；营养编辑保留总量、食物明细和备注；部分重置可仅重试
  失败项目。所有请求均经过 typed API adapter，并由 operation registry 负责取消。

## Runtime、测试与产物

`runtime.ts` 已收缩为空兼容壳，inventory 为 `0/0/0/0`；文件和动态导入留待阶段 12
物理删除。遗留模板耦合扫描为 0 文件、0 测试函数。

Vitest 覆盖聊天上下文、draft ID、档案器械差异、稳定 fact ID、周计划无效日期、隔离训练、
营养编辑、重置重试和取消。Playwright 增加数据管理折叠区顺序契约。生产构建不再生成
runtime chunk，JS/CSS gzip 合计 66,179 字节。
