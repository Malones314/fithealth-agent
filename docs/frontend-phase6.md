# 前端模块化阶段 6 完成记录

> 完成日期：2026-09-11

阶段 6 删除应用内的旧前端回退能力，故障恢复边界改为上一稳定镜像。两个观测发布
及其回退使用情况、E2E、视觉回归和错误观测结果记录在
`docs/frontend-release-history.json`，并由 CI 的 `npm run check:governance` 校验。

## 删除范围

- 删除 `FITHEALTH_FRONTEND_LEGACY`、`/legacy` 静态挂载和维护白名单。
- 删除 `frontend/public/legacy/` 的页面、脚本、样式、字体与 vendor 依赖。
- 删除 freeze/rewire/migration 脚本，`npm run build` 不再读取 `templates/index.html`。
- 将迁移期生成文件固化为 `src/app/runtime.ts`，构建产物只包含正式应用资源。
- 路由契约快照同步移除 `/legacy`，并增加旧路径返回 404 的交付测试。

## 持续治理

- `check:governance` 要求最近两个发布记录均未回退，且关键 E2E、视觉和错误率无回归。
- 新增 API adapter 必须使用共享 client、进入 API 调用契约测试，并保留 403、409、
  422、500、503 错误映射覆盖。
- 新领域必须包含 state/controller/view/events/types 和行为测试；领域不得直连 client，
  也不得导入其他领域私有模块。
- `check:components` 不再允许 runtime 豁免；所有 HTML 写入集中在 `shared/sanitize.ts`。

## 发布操作

每次发布追加 `docs/frontend-release-history.json`。发生前端故障时回滚镜像，同时在该次
发布记录中标明结果和证据路径；不再通过运行时环境变量切换两套页面。

## 验证结果

- 前端 lint、typecheck、6 个治理/预算检查全部通过，Vitest 135 passed。
- Python 全量 1133 passed（仅保留 httpx2 迁移提示）。
- Playwright 69 passed，覆盖 3 个 viewport、12 张视觉基线、竞态与控制台检查。
- Docker 镜像构建通过；首页和哈希资源返回 200，`/legacy/index.html` 返回 404；
  镜像不包含 Node 依赖、前端源码、旧模板或 legacy 目录。
