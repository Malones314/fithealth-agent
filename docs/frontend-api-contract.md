# 前端 HTTP API 基线契约

> 建立日期：2026-09-10  
> 路由基准：`3a7450756245c2229c688a123a1fd026c591e44f`  
> 自动清单：`python scripts/frontend_api_inventory.py`（当前 72 个路由）

## 1. 契约边界

本文件冻结前端迁移依赖的 HTTP 形状，不把所有响应提升为长期公开 API。阶段 2 建 TypeScript DTO 时应以实际响应 fixture 补齐字段；无法由路由源码确认的 workflow/store payload 标为“透传”。

通用规则：

- JSON 业务错误多数为 `{ "error": string }`，少数为嵌套 `error.code/message` 或额外带 `code`。
- FastAPI 参数解析失败使用框架 422 结构；前端必须兼容非 JSON、空响应和文件响应。
- 400 表示字段/格式错误，403 表示外部模型被禁用等策略限制，404 表示资源不存在，409 表示确认或版本冲突，413 表示文件过大，503 表示维护或存储降级。
- `/health/storage-status` 即使 `available=false` 仍返回 200；这是状态查询，不得按 HTTP 成功误判为存储可写。
- `/data/reset` 的部分失败返回 200 且 `partial=true`；客户端必须读取逐项结果。
- 2026-09-24：`/data/reset/retry` 是重新确认的选择性重置，请求必须含 `keys: string[]` 和 `confirmation: "重试删除所选数据"`；每次执行前独立生成最新恢复点，所选项目在上次重置后新增的数据也会删除。
- 维护期间 `/health/storage-status` 返回 `{available: false, check_deferred: true, maintenance, message}`，暂不读取或重校验各存储，因此各 store 状态字段缺省。
- 当前响应没有统一 request ID、error code 或 retryable 字段。阶段 2 使用前端生成的 `clientCorrelationId`，并在客户端推导 retryable。

## 2. 页面、会话与设置

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `GET /` | HTML 页面 | 500：模板读取失败 |
| `POST /chat` | workflow 透传：reply、artifact、workflow state 等 | 400/403/413/5xx，形状由 chat workflow 决定 |
| `POST /logout` | `status` 为 saved/not_saved/no_messages/error，附摘要结果 | workflow 状态码透传 |
| `GET /profile/status` | `complete, missing_fields, prompt` | 全局 503/5xx |
| `POST /profile/confirm-update` | `saved, fields, profile, complete` | 400：无有效变更；全局 503 |
| `GET /session/intro` | `message, garmin_recovery_hours, soreness_prompt_regions` 等 | 400：恢复小时无效 |
| `GET /settings/external-models` | 外部模型设置对象 | 存储异常 |
| `PUT /settings/external-models` | 更新后的设置 | 400：字段错误；存储异常 |
| `POST /settings/llm-connectivity` | `ok, model, latency_ms, message` | 403：外部模型关闭；配置/网络错误状态 |
| `POST /data/profile/reset` | profile reset 结果 | 400：确认无效；存储异常 |

## 3. 上传

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `POST /analyze_food` | food workflow 透传，含营养估算和签名 | 400/403/413/5xx 透传 |
| `POST /upload_fit` | upload workflow 透传，活动或健康导入结果 | 400/413/503/5xx 透传 |
| `POST /upload_plan` | upload workflow 透传，计划解析结果 | 400/403/413/5xx 透传 |
| `POST /upload_health` | 导入汇总，可能含 `activities[{zip,name}]` | 400/413/503/5xx 透传 |
| `POST /upload_health/activity` | 指定 ZIP 内活动解析结果 | 400/404/413/5xx 透传 |

## 4. 训练状态与隔离恢复

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `GET /workout_state` | pending workout 快照 | 全局 5xx |
| `POST /workout_state/update` | 更新结果及状态快照 | 400：action/索引/字段无效；409：冲突 |
| `DELETE /data/pending-workout` | 删除结果 | 404：无 pending workout；5xx |
| `GET /workout_state/quarantined` | `{files}`，支持 `include_dismissed` | 全局 5xx |
| `GET /workout_state/quarantined/{name}/preview` | `{preview}` | 404 |
| `POST /workout_state/quarantined/dismiss` | dismiss 结果 | 400/500 |
| `POST /workout_state/quarantined/delete` | delete 结果 | 400/500 |
| `POST /workout_state/quarantined/restore` | restore 结果和 `state` | 200：部分恢复；400：不可重放；409：已有 pending workout |

## 5. 训练、营养与每日记录

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `GET /data/training-records` | `{items}`，可选 day | 400：日期格式 |
| `GET /data/training-records/{record_id}` | 记录详情、日期和 sidecar 信息 | 404 |
| `PATCH /data/training-records/{record_id}` | `{updated, record, notice}` | 400：revision/字段/动作无效；404；409：`STALE_RECORD_REVISION` |
| `GET /data/nutrition-records` | `{items}`，可选 day | 400：日期格式 |
| `PATCH /data/nutrition-records/{group_id}` | `{updated, record}` | 400：revision/字段无效；404；409：版本冲突 |
| `DELETE /data/nutrition-records/{group_id}` | `{deleted, id, date}` | 400：revision；404；409：版本冲突 |
| `GET /data/checkins/{day}` | `{checkin, date, id?}`；不存在时 checkin=null | 400：日期格式 |
| `POST /data/checkins` | 保存结果，可能含 nutrition/meal details | 400：字段或餐盘签名无效；503：存储降级 |
| `GET /data/overview` | records/plans/memories/soreness/imports 等聚合 | 全局 5xx |
| `DELETE /data/records/{record_id}` | `{deleted, id}` | 404 |
| `POST /data/records/delete-batch` | `{deleted}` | 400：ID 无效/未选择；404：无可删记录 |

## 6. 训练计划

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `GET /plans/{plan_id}` | 计划对象 | 404 |
| `POST /plans` | `{saved, ...plan}`；重复项 saved=false | 400：正文/draft/字段无效 |
| `PATCH /plans/{plan_id}` | 更新后的计划对象 | 400；404 |
| `DELETE /plans/{plan_id}` | `{deleted, id}` | 404 |
| `POST /plans/delete-batch` | `{deleted}` | 400：未选择 |

## 7. 记忆与酸痛

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `DELETE /data/memories/{entry_id}` | `{deleted, id}` | 404 |
| `DELETE /data/memories` | `{deleted}` | 存储异常 |
| `POST /data/memories/{entry_id}/confirm` | `{confirmed, id, ...result}` | 404；409：`MEMORY_CONFLICT` |
| `POST /data/memories/{entry_id}/facts/{fact_ref}/confirm` | `{confirmed, ...result}` | 404；409：`MEMORY_CONFLICT` |
| `POST /data/memories/{entry_id}/facts/{fact_ref}/reject` | `{rejected, ...result}` | 404 |
| `PATCH /data/memories/{entry_id}/facts/{fact_ref}` | `{updated, ...result}` | 400：值/证据；404 |
| `POST /data/memories/{entry_id}/facts/{fact_ref}/rollback` | `{rolled_back, requires_confirmation, ...result}` | 400：fact_id/history；404 |
| `POST /data/memories/forget` | forget 结果 | 400：namespace/key/value/reason；404 |
| `POST /data/soreness` | `{created, report}` | 400：区域/程度；503：存储失败 |
| `PATCH /data/soreness/{report_id}` | `{updated, report}` | 400；404 |
| `DELETE /data/soreness/{report_id}` | `{deleted, id}` | 404 |

## 8. 健康数据与文件审计

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `GET /health/storage-status` | `{available, memory_store, health_store, external_model_settings, maintenance, message?}` | 始终 200 表达状态 |
| `GET /health/daily/{day}` | 日健康对象 | 400：日期格式 |
| `GET /health/overview` | 日概览对象 | 400：日期格式 |
| `GET /health/range` | `{items}` | 400：日期范围 |
| `GET /health/trend` | metric/period/date/items/unit/cumulative | 400：指标、周期或日期 |
| `GET /health/sleep/{day}` | 睡眠对象 | 400：日期；404：无数据 |
| `GET /health/imports/{import_id}` | 导入详情 | 404 |
| `DELETE /health/imports/{import_id}` | `{deleted, id}` | 404；503：存储降级 |
| `GET /health/imports/raw-audit` | 原始文件审计对象 | 全局 5xx |
| `DELETE /health/imports/raw-orphans/{name}` | `{deleted, name}` | 400：不安全文件名；404 |
| `GET /data/hr-streams/audit` | 心率 sidecar 审计对象 | 全局 5xx |
| `DELETE /data/hr-streams/orphans/{name}` | `{deleted, name}` | 400：不安全文件名；404 |

## 9. 备份、恢复与重置

| Method/path | 成功响应 | 已知错误 |
| --- | --- | --- |
| `GET /data/backup/export` | ZIP 文件及 Content-Disposition | 500：导出失败 |
| `POST /data/backup/inspect` | `{valid, files, has_health_database}` | 400：校验失败；413：超过 1 GiB |
| `POST /data/backup/import` | `{restored, workout_state, memory_store_revalidated, ...}` | 400：备份无效，或 v2 备份缺少 health.db 且目标已有健康数据库；409：未确认/维护忙；413 |
| `POST /data/reset` | `{deleted, partial, recovery_point, steps, ...counts}` | 400：确认短语；409：维护忙；500：恢复点失败；部分失败为 200 |
| `POST /data/reset/retry` | `{retried, steps, recovery_point, partial, error}` | 400：缺少重新确认/keys 无效；409：维护忙；500：新恢复点失败；部分失败为 200 |
| `GET /data/recovery-points` | `{points}` | 全局 5xx |
| `GET /data/recovery-points/{name}` | ZIP 文件 | 400：名称；404 |
| `DELETE /data/recovery-points/{name}` | `{deleted, name}` | 400；404；500 |

## 10. 迁移期间的变更规则

API 路径或字段变化必须先更新本契约和对应 Python 行为测试，再更新 TypeScript DTO。只搬迁前端代码时不得顺手重命名 endpoint。新增统一错误字段或 `X-Request-ID` 属于独立后端契约变更，不在阶段 0/1 默认范围内。
