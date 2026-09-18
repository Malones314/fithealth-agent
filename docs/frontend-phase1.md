# 前端模块化阶段 1 交付说明

> 历史记录：本文描述的 legacy 回退已在阶段 6 删除，当前发布操作见
> `docs/frontend-phase6.md`。

> 完成日期：2026-09-10

默认首页由 `frontend/dist/index.html` 提供，`/assets/` 提供 Vite 哈希产物。
`FITHEALTH_FRONTEND_LEGACY=1` 可在重启后切换到 `frontend/public/legacy/index.html`。
构建产物不提交 Git，由本地构建、CI 或 Docker 的 Node builder 生成。

冻结脚本 `scripts/freeze_frontend_phase1.mjs` 从阶段 0 基线页面机械提取 DOM、CSS
和业务脚本。原 `templates/index.html` 暂时保留，供阶段 2/3 原子迁移源码断言；
它不再是运行时页面。marked、DOMPurify 和 Inter 字体均由 npm 锁文件固定并复制到
`/legacy/`，默认版与回退版均不访问 Google Fonts 或 jsDelivr。

本地验证：

```powershell
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
cd ..
python -m pytest -q
docker build -t fithealthagent-phase1 .
```

Vite 开发服务器监听 `127.0.0.1:5173`，代理 API 到 `127.0.0.1:9999`。
Docker 开发覆盖层只运行 Python watcher，前端 watcher在宿主机运行，避免双重监听。
