# 前端模块化基线

> 历史快照：本文件记录迁移前状态。阶段 12 已删除旧模板和临时断言扫描器；当前验证
> 命令见 `docs/frontend-phase12.md`。

> 建立日期：2026-09-10  
> 代码基准：`3a7450756245c2229c688a123a1fd026c591e44f`  
> 基准提交时间：`2026-09-10T14:12:27+08:00`  
> 基准提交说明：`feat(settings): add LLM connectivity test`

## 1. 基线口径

本基线不创建 Git 提交，也不清理工作树。建立时仓库存在用户未提交内容，包括测试临时文件删除、数据目录文件删除、`frontend-modularization-plan.md` 修改，以及未跟踪的 `0`、`=` 文件。这些内容不属于阶段 0，不得由前端迁移提交代为清理。

默认代码参照使用上述 commit；页面参照额外使用文件哈希，避免工作树变化造成歧义：

| 文件 | 行数 | 字节 | SHA-256 |
| --- | ---: | ---: | --- |
| `templates/index.html` | 4,740 | 264,563 | `BBAE75E01011EA9B6D96A9E36DAFC8078B7F055642CA06FC0B3C2EDD9D68B027` |

阶段 1 开始前，维护者应把希望纳入迁移的业务改动形成独立提交。若页面哈希改变，必须先重新运行本文件中的测试和截图命令，并在此表新增一条基线记录，不能直接覆盖旧记录。

## 2. 功能矩阵

| 领域 | 当前入口/能力 | 关键回归 |
| --- | --- | --- |
| session | 启动加载、档案提示、存储提示、轮询、退出 | 启动不阻塞；退出状态可恢复 |
| chat | 消息发送、Markdown、计划保存、档案变更确认 | 错误可见；draft ID 原样回传 |
| workout | pending workout、组编辑、撤销、恢复、冲突保存 | 草稿不被轮询覆盖；revision 冲突可处理 |
| uploads | FIT、计划、健康 ZIP/CSV、餐盘图片 | 多 ZIP 活动与源文件匹配 |
| health | 日概览、日/周/月趋势、睡眠 | 累计热量不做平均；日期错误可见 |
| checkin | 每日状态、营养估算、餐盘确认 | 已有记录可载入；签名错误可见 |
| data-management | 记录、计划、记忆、酸痛、文件审计、备份恢复 | 删除确认；恢复点；隔离训练非阻塞提示 |
| settings | 外部模型开关、LLM 连通性 | 403/失败状态可见；不泄露密钥 |

完整 HTTP 接口见 [frontend-api-contract.md](frontend-api-contract.md)。前端源码断言迁移见 [frontend-assertion-migration.md](frontend-assertion-migration.md)。

## 3. 可复现视觉基线

工具固定为 `@playwright/test@1.63.0`、Chromium `1243`。测试使用独立端口 `10019` 和 `.test-tmp/frontend-baseline`，并拦截启动阶段 API，禁止读取仓库真实 `data/`。

```powershell
cd frontend
npm.cmd ci --cache ..\.npm-cache
npx.cmd playwright install chromium
npm.cmd run test:e2e:baseline:update
npm.cmd run test:e2e:baseline
```

可用 `FITHEALTH_E2E_PORT` 覆盖端口。截图位置为 `frontend/e2e/__screenshots__/`，每个场景分别生成 desktop-1440、tablet-768、mobile-390 三份 golden：

- `main-page-*.png`：首屏、聊天、健康趋势和训练列表。
- `health-overview-*.png`：单日健康总览弹窗。
- `data-management-*.png`：数据管理弹窗。
- `activity-picker-*.png`：多 ZIP 上传后的活动选择器。

截图 fixture 固定会话文本、档案完成状态、存储状态、趋势日期/数值和空训练状态。更新 golden 必须独立提交并由维护者审阅。

## 4. 已知基线问题

- 390px viewport 下顶栏横向空间不足，右侧退出按钮被裁切。
- 390px viewport 下聊天输入区状态文字与右侧操作按钮空间紧张，文字换行后接近遮挡。
- 页面仍请求 Google Fonts、jsDelivr 的 marked 和 DOMPurify；离线与隐私边界将在阶段 1 修复。
- 因上述 CDN 依赖，网络偶发断开时 Chromium 会产生 `Failed to load resource` 控制台错误；基线测试仅忽略这一类已知资源错误，其他 console error 仍导致失败。
- 页面初始化和多个领域直接调用 `fetch`，错误体处理不一致。
- `load_index_html()` 使用进程内缓存，直接修改模板后需要重启 FastAPI。

这些问题属于迁移前现状。阶段 1 至 4 修复时必须带独立行为/视觉测试，不能在机械搬迁提交中静默改变。

## 5. 基线验证命令

```powershell
python scripts\frontend_api_inventory.py
python -m pytest tests\test_arch09_test_governance.py tests\test_frontend_phase0_governance.py -q
python -m pytest -q
cd frontend
npm.cmd run test:e2e:baseline
npm.cmd run check:architecture
```

## 6. 2026-09-10 验证结果

- 阶段 0 专项治理：11 passed。
- Playwright：4 个场景 x 3 个 viewport，共 12 passed。
- Python 全量：1117 passed、5 failed、1 warning，用时 189.07 秒。

全量失败均在阶段 0 前已存在，且本次没有修改应用业务代码：

| 失败 | 归属 | 基线处理 |
| --- | --- | --- |
| `test_f3_memory_features.py::...plan_auto_correction...` | chat/plan 后端 | 返回 artifact=null；由计划工作流维护者单独诊断，不纳入机械前端迁移 |
| `test_route_snapshot.py` 三项 | route snapshot | 基准提交新增 `/settings/llm-connectivity` 后 snapshot 未更新；由该功能维护者确认契约后更新 |
| `test_trace_react_sink.py::...endpoint_host...` | observability/environment | 测试期望 `api.deepseek.com`，当前 `.env` 为 `www.micuapi.ai`；应隔离环境依赖，不改用户配置 |

阶段 1 不得把这些失败归因于前端构建。开始阶段 1 前应先由对应维护者修复，或在 CI 基线中显式隔离并登记到期条件。
