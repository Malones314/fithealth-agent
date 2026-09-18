# 前端模块化阶段 2 交付说明

> 完成日期：2026-09-11

阶段 2 建立了 TypeScript DTO、统一 API client 和共享安全层。`frontend/src/api`
中的领域适配器不直接使用 `fetch`，所有请求都经过 `api/client.ts`；该 client
统一处理 JSON、FormData、文件下载、超时、AbortSignal、非 JSON 响应和前端生成的
`X-Client-Correlation-ID`。

错误不会假设后端已经提供统一结构。client 会从顶层或嵌套 `error.code` 提取
`serverCode`，按 HTTP 状态、请求方法和操作类型推导 `retryable`，并保留原始响应在
`details` 中。取消请求保持 AbortError 语义，超时和网络异常转换为可展示的客户端错误。

`shared/sanitize.ts` 是唯一 Markdown → HTML 入口，禁止脚本、事件属性、危险 URL、
表单和嵌入标签；`shared/dom.ts`、`formatters.ts`、`validation.ts` 提供安全 DOM、日期、
数值和文件校验工具。健康数据、密钥和完整聊天正文不写入 localStorage 或前端日志。

验证命令：

```powershell
cd frontend
npm ci
npm run lint
npm run typecheck
npm run check:boundaries
npm test
```

`scripts/check_frontend_boundaries.mjs` 是 CI 中的依赖闸门：除 `api/client.ts` 外，
源码不得直接调用 `fetch`；API 层不得依赖 DOM；shared 层不得依赖 app、domains 或
components。
