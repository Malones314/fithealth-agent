# 前端模块化阶段 12 完成记录

> 完成日期：2026-09-14

## 单体删除

- 删除 `frontend/src/app/runtime.ts` 及 `main.ts` 动态导入。
- 删除 `templates/index.html`、`LEGACY_INDEX`、旧模板断言扫描器与阶段性 runtime
  预算/函数所有权文件。
- ESLint、边界检查、package scripts 和 CI 均不再包含 runtime 特例。

## 最终页面结构

`frontend/index.html` 只保留文档骨架、三个语义片段挂载点和 `main.ts` 入口。Vite 在开发
和构建时注入 `src/templates/header.html`、`workspace.html` 与 `modals.html`。页面 ID、
可访问性属性和视觉结构保持不变，不包含内联业务脚本。

## 长期治理

`npm run check:architecture` 取代迁移期 runtime inventory，并持续验证：

- 已删除单体和旧模板不能重新出现；
- API 访问不能绕过 `api/client.ts`；
- HTML 中每个 ID 都有唯一所有者，领域不得访问其他领域的私有 DOM；
- 非测试 TypeScript 文件不得超过 700 行，防止形成等价单体。

领域状态、共享组件、样式边界和 bundle 预算继续由现有治理脚本覆盖。生产镜像只包含
Vite 构建产物，不包含前端源码或 Node 依赖。最终 JS/CSS gzip 合计 65,629 字节。

## 最终验证

- Vitest：165 passed。
- Python：1113 passed（仅保留一条第三方 Starlette 弃用警告）。
- Playwright：78 passed，覆盖 desktop/tablet/mobile 三种 viewport 与视觉基线。
- CI 改为执行完整 `npm run test:e2e`，阶段 10/11 领域用例也纳入发布门槛。
- Docker：镜像构建、首页、哈希资源、健康检查和内容审计均通过，容器状态 healthy。
- Prettier、ESLint、TypeScript、boundaries、governance、components、styles、architecture 和
  bundle 检查全部通过。
