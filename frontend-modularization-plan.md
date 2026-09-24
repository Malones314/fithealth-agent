# FitHealthAgent 前端模块化与结构化实施计划

> 修订日期：2026-09-14
> 适用仓库：FitHealthAgent
> 最终核对状态：阶段 0-12 已完成，验证日期 2026-09-14
> 实施原则：先冻结契约，再建立构建链，随后按领域逐片迁移；每片迁移必须同时删除旧实现并迁移测试。

## 最终状态

前端现已由 TypeScript/Vite 构建，七个领域分别拥有 controller/view/state/tests；入口只安装
共享基础设施并启动领域 controller。旧模板、runtime 单体、动态导入和迁移期豁免均已
删除，长期架构闸门禁止兼容单体、API 绕过和跨领域 DOM 访问重新出现。

## 0. 2026-09-11 状态纠偏（历史）

此前把“已创建领域目录、controller/view/state 文件和测试壳”误判成了“业务已经按领域
迁移”。复核运行入口和代码所有权后，确认前端仅完成了构建、交付、共享基础设施和回退
删除，**业务实现仍是单体**。从本次修订起，目录或文件存在不再作为模块化完成证据。

当前可复现基线：

| 指标 | 当前值 | 完成目标 |
| --- | ---: | ---: |
| `templates/index.html` | 约 4,498 行、264 KB | 文件删除 |
| `frontend/src/app/runtime.ts` | 约 3,600 行、188 KB | 文件删除 |
| runtime 顶层/嵌套函数 | 128 | 0 |
| runtime 事件监听注册 | 103 | 0 |
| runtime DOM 查询 | 163 | 0 |
| runtime 绕过 API 层的 `globalThis['fetch']` | 20 | 0 |
| 仍读取旧模板的 Python 测试 | 10 个文件、19 个测试函数 | 0 |

`frontend/src/main.ts` 当前仍动态导入 `app/runtime.ts`。除 uploads 的少量活动选择器逻辑外，
各领域 controller/view 基本只写状态标记，没有接管真实 API、DOM、事件和业务状态。因此：

- 阶段 0-2：基础设施完成；
- 阶段 3：仅完成目录骨架，业务迁移未完成，验收撤销；
- 阶段 4-5：共享能力完成，但仍主要由单体 runtime 消费；
- 阶段 6：只完成回退资源移除和治理骨架，不代表前端模块化完成；
- 真正的领域迁移由新增阶段 7-12 完成。

## 1. 审查结论与采纳情况

Claude 审查指出的主要架构风险成立，已合并到本计划正文，不再把审查意见作为附录保留。

| 审查项                          | 结论       | 本次处理                                                                            |
| ------------------------------- | ---------- | ----------------------------------------------------------------------------------- |
| `index.html` 源码断言会阻塞迁移 | 采纳       | 阶段 0 建立断言迁移台账和 `tests/frontend_map.py`，每次迁移同步更新 ARCH-09 白名单  |
| 缺少静态资源挂载                | 采纳       | 新增独立前端交付模块，生产环境挂载 `/assets`，保持 `main.py` 瘦外观                 |
| Docker 没有 Node 构建链         | 采纳       | 阶段 1 使用 Node builder + Python runtime 多阶段镜像，并排除 `node_modules`         |
| `requestId` 与后端契约不符      | 采纳并调整 | 不要求后端新增 request ID；前端使用自生成的 `clientCorrelationId`                   |
| 当前没有 CI                     | 采纳       | 本计划负责创建 CI 骨架和前端 job，其他发布治理仍由《下一阶段开发计划及实现.md》负责 |
| Playwright 决策过晚             | 采纳       | 阶段 0 即确定 Playwright，并用同一配置建立和比较截图基线                            |
| “DOM 减少 30%”不可测            | 采纳       | 删除比例指标，改用依赖检查、共享组件调用点和内联脚本行数闸门                        |
| Google Fonts 与 XSS 测试遗漏    | 采纳       | 字体、marked、DOMPurify 一并本地化，并增加恶意 Markdown 用例                        |
| 排期偏乐观                      | 重新估算   | 基础设施完成后，真实业务模块化仍需单人约 19-27 个工作日                              |

数量说明：截至当前工作树，扫描到 10 个测试文件直接引用 `templates/index.html`，其中 7 个含 `source_assertion_guard` 能识别的源码文本断言。数量会随代码变化，因此正式实施以阶段 0 自动生成的台账为准。

## 2. 当前约束

### 2.1 前端现状

- 生产页面已由 Vite 构建并由 `/assets` 交付，字体、marked 和 DOMPurify 已本地化。
- `templates/index.html` 不参与生产构建，但仍被 10 个 Python 测试文件读取，不能直接删除。
- 默认入口仍加载约 3,600 行的 `app/runtime.ts`；它拥有绝大多数状态、事件、DOM 和请求。
- `runtime.ts` 使用 20 处 `globalThis['fetch']` 绕过类型化 API 层，现有 direct-fetch 闸门未识别这种写法。
- 七个领域目录已存在，但大部分 controller/view 是占位壳，不拥有对应业务流程。

### 2.2 仓库治理约束

- 多个 Python 测试读取 `templates/index.html` 并断言函数名、字符串或 DOM 结构。
- `tests/test_arch09_test_governance.py` 要求遗留源码断言白名单只能随迁移同步缩小，不能产生新债务或留下 stale 条目。
- `tests/test_main_facade.py` 要求 `main.py` 不超过 200 行，且顶层函数集合保持精确。
- Dockerfile 当前没有 Node 构建阶段，`.dockerignore` 也没有排除 `node_modules/`。
- 当前没有 `.github/workflows/`，发布门槛尚无自动执行入口。

### 2.3 不可破坏的业务约束

- FastAPI API 路径、请求字段、响应语义和路由顺序保持兼容。
- 训练编辑草稿不得被轮询或刷新覆盖。
- 409、403、422、503 等业务错误必须保留差异化提示。
- 健康数据、密钥和完整聊天正文不得进入 localStorage、前端日志或测试快照。
- 模块化期间不夹带视觉重设计和业务规则重写。

## 3. 已确定的技术决策

以下决策在编码前冻结，不再推迟到阶段 1 结束：

1. **技术栈**：本轮使用 TypeScript + 原生 DOM + Vite，不中途切换 React/Vue。未来若引入框架，以 controller 和 API 层为稳定边界替换 renderer。
2. **测试工具**：Vitest + jsdom 负责单元/模块测试；Playwright 负责 E2E、截图和浏览器控制台检查。
3. **产物目录**：Vite 输出到 `frontend/dist/`，不放进 `templates/`。
4. **产物策略**：开发分支不提交 `frontend/dist/`；Docker 通过多阶段构建生成；源码发布包由 CI 构建并包含产物。
5. **生产交付**：新增 `fithealth_agent/runtime/frontend.py`，负责选择首页和挂载 `/assets`；`main.py` 只做导入与注册，不新增顶层函数。
6. **开发模式**：Vite dev server 提供页面并代理 API 到 `127.0.0.1:9999`，避免受 `load_index_html` 缓存影响；FastAPI 不负责开发期热更新前端。
7. **回退模式**：阶段 6 已在两个稳定观测版本后移除 `FITHEALTH_FRONTEND_LEGACY` 和冻结资源；发布故障统一回滚上一稳定镜像。
8. **请求关联**：前端为每个请求生成 `clientCorrelationId`，只用于本地诊断；不把它冒充后端 trace ID。后端未来若提供 `X-Request-ID`，再单独扩展。
9. **CI 边界**：本计划创建基础 workflow 并维护 Python 回归、前端 lint/typecheck/test/build、Playwright 冒烟。制品签名、版本发布和部署仍归《下一阶段开发计划及实现.md》。

## 4. 目标结构

```text
frontend/
├─ package.json
├─ package-lock.json
├─ tsconfig.json
├─ vite.config.ts
├─ playwright.config.ts
├─ index.html
├─ public/
│  ├─ fonts/
│  └─ legacy/                 # 冻结的可回退页面及本地依赖
├─ src/
│  ├─ main.ts
│  ├─ app/
│  │  ├─ bootstrap.ts
│  │  ├─ app-state.ts
│  │  └─ events.ts
│  ├─ api/
│  │  ├─ client.ts
│  │  ├─ chat.ts
│  │  ├─ uploads.ts
│  │  ├─ workout.ts
│  │  ├─ health.ts
│  │  ├─ records.ts
│  │  ├─ plans.ts
│  │  ├─ memories.ts
│  │  ├─ settings.ts
│  │  └─ maintenance.ts
│  ├─ domains/
│  │  ├─ session/
│  │  ├─ workout/
│  │  ├─ uploads/
│  │  ├─ chat/
│  │  ├─ health/
│  │  ├─ checkin/
│  │  └─ data-management/
│  ├─ components/
│  ├─ shared/
│  │  ├─ dom.ts
│  │  ├─ formatters.ts
│  │  ├─ sanitize.ts
│  │  ├─ validation.ts
│  │  └─ types.ts
│  └─ styles/
│     ├─ tokens.css
│     ├─ base.css
│     ├─ layout.css
│     └─ components.css
├─ tests/
└─ e2e/

fithealth_agent/runtime/frontend.py  # 生产静态资源与 legacy 选择
tests/frontend_map.py                # Python 侧前端文件定位的单一来源
```

依赖方向：

```text
main -> app/bootstrap -> domains -> api
                         |        -> shared
                         +-------> components -> shared

api 不依赖 DOM；shared 不依赖 app、domains 或 components。
```

## 5. 分阶段实施

### 阶段 0：冻结基线与消除前置不确定性（2-3 天）

> 状态：已完成（2026-09-10）。产物见 `docs/frontend-baseline.md`、`docs/frontend-assertion-migration.md`、`docs/frontend-api-contract.md`、`tests/frontend_map.py` 和 `scripts/frontend_*_inventory.py`。基线提交仍需维护者在处理现有工作树后确认；当前使用 commit SHA + 页面 SHA-256 作为可复现参照。

#### 实现步骤

1. 先处理当前工作树基线：不丢弃现有改动；由维护者确认后形成专用基线提交，并在 `docs/frontend-baseline.md` 记录 commit SHA、测试结果和页面文件哈希。
2. 编写扫描脚本，输出所有直接读取 `templates/index.html` 的测试函数、读取方式和断言类型。
3. 新建前端断言迁移台账，每条测试选择一种归属：
   - 用户行为转为 Playwright；
   - 纯函数或状态行为转为 Vitest；
   - 必须由 Python 保证的 DOM/API 契约改为读取构建产物或契约清单；
   - 暂时无法迁移的源码断言显式保留，并记录删除阶段。
4. 新建 `tests/frontend_map.py`，集中定义 legacy 页面、Vite 源码、构建首页和资源目录；Python 测试不得自行拼接路径。
5. 规定测试迁移的原子提交规则：实现迁移、测试迁移、`LEGACY_SOURCE_ASSERTION_TESTS` 更新必须在同一提交完成。
6. 安装并固定 Playwright 浏览器版本，以 1440x900、768x1024、390x844 建立截图基线；动态时间、健康数值和 request token 使用固定 fixture 或 mask。
7. 盘点所有 API 的方法、路径、成功响应、错误体及状态码，形成 `docs/frontend-api-contract.md`。
8. 明确与《下一阶段开发计划及实现.md》的 CI 分工，并记录在两份文档的交叉引用中。

#### 验收门槛

- 基线文档指向一个明确 commit，工作树额外差异被单独记录，不能用“当前状态”作为模糊基线。
- 断言迁移台账覆盖扫描脚本发现的全部调用点，无未归属项。
- Playwright 能用固定数据重复生成三种 viewport 的同一组截图。
- Python 全量测试已执行；本阶段不得新增失败。既有基线失败必须记录负责人、原因和后续处理方式。

### 阶段 1：构建、静态交付、Docker 与 CI 骨架（4-5 天）

> 状态：已完成（2026-09-10）。Vite/TypeScript 工程、冻结 legacy 与本地依赖、
> `runtime/frontend.py` 静态交付、多阶段 Docker、基础 CI 和 HTTP/Playwright
> 冒烟均已落地，并补齐了测试顺序与本机 `.env` 隔离。全量 Python、前端检查、
> 三视口 Playwright 和 Docker 默认/legacy 回退演练均通过。

#### 实现步骤

1. 创建 Vite/TypeScript 工程，启用 strict 类型检查、ESLint、Prettier 和 Vitest。
2. 冻结当前页面为 `frontend/public/legacy/`，将 Google Fonts、marked、DOMPurify 全部本地化；验证断网状态仍可启动和渲染 Markdown。
3. 新建最小 Vite 页面壳，暂时加载 legacy 业务脚本，保留关键 DOM id/class 和现有用户流程。
4. 在 `fithealth_agent/runtime/frontend.py` 实现：
   - 生产模式从 `frontend/dist/index.html` 返回首页；
   - `app.mount('/assets', StaticFiles(...))` 提供构建资源；
   - legacy 开关选择冻结页面；
   - 产物缺失时返回明确启动/请求错误，不静默展示残缺页面。
5. `main.py` 只导入并调用注册入口，不新增顶层函数；同步运行 `test_main_facade.py`。若框架限制必须改其结构，测试修改与代码放在同一提交并解释边界变化。
6. 调整维护中间件，让首页所需的 `/assets/` 在维护模式下可读，同时不放宽业务 API；添加专项测试。
7. Dockerfile 改成 Node builder + Python runtime：builder 执行 `npm ci && npm run build`，runtime 只复制 `frontend/dist`，不包含 `node_modules`。
8. `.dockerignore` 明确加入 `frontend/node_modules/` 和其他前端缓存；不得排除 Docker builder 所需源码。
9. 开发方式固定为 FastAPI 9999 + Vite 5173，Vite 代理 API；更新 `docker-compose.dev.yml` 或明确前端在宿主机运行的命令，避免容器内外双重 watcher。
10. 新建 CI workflow，先接入 Python 回归、前端 lint/typecheck/test/build、Docker build 和一个首页资产 200 冒烟测试。
11. 当场演练默认构建版与 `FITHEALTH_FRONTEND_LEGACY=1` 两条路径，包括 Docker Compose 场景。

#### 验收门槛

- `npm ci`、lint、typecheck、test、build 均通过。
- `GET /`、构建生成的 JS/CSS、legacy 页面及其本地依赖均返回 200。
- 浏览器 Network 面板无 Google Fonts、jsDelivr 或其他非业务外部静态请求。
- Docker 镜像可独立启动，镜像内不存在 `node_modules`，首页和健康检查正常。
- `main.py` 门面测试、维护模式资源测试和全量 Python 测试通过。
- CI 能在一次提交上完整运行上述检查。

### 阶段 2：类型、API 客户端与共享安全层（4-6 天）

> 状态：已完成（2026-09-11）。已建立共享 DTO、统一 API client、领域 API 适配器、
> Markdown 安全入口、日期/数值/文件/DOM 工具和依赖边界检查；CI 已接入 boundary
> gate。阶段 2 单测覆盖取消、超时、网络异常、非 JSON、403、409、422、503、5xx、
> FormData、下载及恶意 Markdown。

#### 实现步骤

1. 定义 ChatMessage、Workout、TrainingRecord、HealthOverview、Checkin、Plan、MemoryFact、UploadResult 等 API DTO；编辑草稿类型与服务器 DTO 分开。
2. 实现 `api/client.ts`，统一处理 JSON、FormData、文件下载、超时、AbortSignal 和非 JSON 错误。
3. 错误结构使用前端可真实获得的信息：

```ts
type ApiError = {
  status: number;
  message: string;
  serverCode?: string;
  clientCorrelationId: string;
  retryable: boolean;
  details?: unknown;
};
```

4. `serverCode` 从现有顶层或嵌套 `error.code` 中规范化；`retryable` 由状态码、请求方法和操作类型在前端计算，不假设后端返回该字段。
5. 按后端路由建立 API 模块，领域代码和组件不得直接调用 `fetch`。
6. 抽取日期、时区、数值、HTML escape、表单读取、文件校验和 Markdown 安全渲染。
7. `shared/sanitize.ts` 是唯一 Markdown 到 HTML 的入口；测试必须覆盖 `<script>`、事件属性、`javascript:` URL 和受限表单标签。
8. 增加依赖边界检查，阻止 `api -> DOM`、`shared -> domains` 和跨领域私有模块导入。

#### 验收门槛

- 除 `api/client.ts` 和明确列入白名单的下载适配器外，`frontend/src` 不出现直接 `fetch`。
- API 测试覆盖成功、超时、取消、网络异常、非 JSON、403、409、422、503 和 5xx。
- 恶意 Markdown 测试通过，所有 `innerHTML` 写入均来自安全入口或静态常量。
- 类型检查和依赖边界检查进入 CI。

### 阶段 3：按领域逐片迁移（8-12 天）

> 状态：**未完成，原验收结论撤销（2026-09-11）**。七个领域仅建立了结构和少量测试壳；
> 默认入口仍加载单体 `app/runtime.ts`，真实 API、DOM、事件和业务状态尚未迁移。原计划步骤
> 保留为设计依据，实际执行改由阶段 7-11 接续。

迁移顺序：

1. **session**：启动加载、轮询、全局通知、退出。
2. **workout**：pending workout、编辑草稿、集合选择、冲突解决、训练保存。
3. **uploads**：FIT、计划、健康 ZIP/CSV、餐盘图片、活动选择器。
4. **chat**：消息历史、请求状态、计划保存面板、档案更新确认。
5. **health/checkin**：健康概览、趋势、每日打卡、营养估算、日期切换。
6. **data-management**：记录、计划、记忆、酸痛、文件审计、隔离训练、恢复点、备份恢复。

每个领域统一采用以下结构：

```text
domains/<name>/
├─ state.ts
├─ controller.ts
├─ view.ts
├─ events.ts
├─ types.ts
└─ *.test.ts
```

每个迁移片必须执行：

1. 先把数据访问切到对应 API 模块。
2. 分离服务器快照与本地编辑草稿。
3. 抽取 renderer 和事件绑定，controller 通过依赖注入获得 API、DOM 根节点和通知接口。
4. 将对应 Python 源码断言迁到 Vitest、Playwright 或构建产物契约测试。
5. 同一提交删除 legacy 中对应实现和事件监听，禁止长期双实现。
6. 更新断言迁移台账、ARCH-09 白名单和领域 README。
7. 运行该领域测试、Python 全量测试和 Playwright 核心冒烟。

#### 机械闸门

- 新增脚本统计 `app/runtime.ts` 中函数、请求、DOM 查询和事件监听；每个领域迁移提交必须单调下降，阶段 11 结束为 0。
- runtime 与领域模块不得同时注册同一 DOM 事件；开发模式增加重复监听诊断。
- `tests/frontend_map.py` 是 Python 测试定位前端文件的唯一入口。
- 不允许新增 ARCH-09 源码文本断言豁免。

#### 验收门槛

- 七个领域全部真实拥有 state/controller/view/tests，`main.ts` 只负责依赖组装和启动。
- `app/runtime.ts` 的业务函数、请求、DOM 查询和监听为 0，并在阶段 12 删除文件。
- 原有源码断言已按台账迁移，ARCH-09 白名单同步缩小且无 stale/unexpected。
- 训练编辑竞态、上传活动选择、计划 draft ID、保存冲突、档案确认和隔离训练恢复均有行为测试。

### 阶段 4：共享组件与样式结构化（4-5 天）

> 状态：已完成（2026-09-11）。共享 Modal/Toast/Button/Tabs/EmptyState/ConfirmDialog/
> FileDropzone 已建立并由 `app/shell.ts` 单例装配；`legacy.css` 拆成 tokens/base/
> layout/components/领域/响应式/主题覆写 9 个文件，默认页面不再引用冻结样式，字体
> 改由 `@fontsource` 打包。改写通过 `scripts/rewire_frontend_phase4.mjs` 机械执行
> （22 条规则、命中次数精确校验）。新增 `check:components`、`check:styles` 静态闸门和
> 三视口布局/无障碍 e2e，均已进入 CI。12 张视觉 golden 一张未改，Python 全量 1133、
> 前端单测 98、Playwright 48 全部通过。详见 `docs/frontend-phase4.md`。

#### 实现步骤

- 抽取 Modal、Button/IconButton、Tabs、Toast、Loading、EmptyState、ConfirmDialog、FileDropzone。
- 样式拆为 tokens、base、layout、components 和领域样式，保留现有视觉与响应式行为。
- Modal 统一处理焦点圈定、Escape、焦点恢复和 backdrop；FileDropzone 统一处理拖拽与文件校验。
- 用静态检查限制领域模块自行创建共享弹窗、toast 和空态结构。

#### 可测量门槛

- 所有模态框通过统一 Modal 接口打开/关闭，不再各自操作 `show` class 和键盘事件。
- 所有全局提示通过统一 Toast 接口；所有文件拖拽入口复用 FileDropzone 的校验流程。
- 共享组件拥有键盘、ARIA、禁用、加载和错误状态测试。
- Playwright 在 1440x900、768x1024、390x844 下无横向页面溢出；固定格式控件尺寸不因动态文本改变。

### 阶段 5：默认切换、回归与性能收口（3-4 天）

> 状态：已完成（2026-09-11）。`shared/operations.ts` 提供操作 token 与取消，已接入
> 趋势、单日总览、轮询、聊天和上传；`app/error-reporting.ts` 只记录模块、endpoint、
> 状态码和 clientCorrelationId，取消不算异常；按需渲染基于实测（线性、无断崖）只对
> 两个无界列表分批，未引入虚拟滚动；bundle 预算写入 `frontend/bundle-budget.json`
> 并由 `check:bundle` 在 CI 与 Docker 构建阶段执行；视觉回归与竞态回归进 CI。
> 顺带修掉四个既有缺陷：阶段 4 空态改写的变量名错误（`$shell()`）、阶段 4 起就坏掉的
> Docker 构建（`.dockerignore` 白名单没跟上）、`main.ts` 碰巧成立的加载顺序，以及一条
> 会命中两处的改写规则。并给生成文件补上 `no-undef` 闸门，堵住那类错误的来源。
> 前端单测 133、Playwright 69（含 12 张未改动的视觉 golden）、Python 全量 1133 全部
> 通过；Docker 默认版与 legacy 回退均已实机演练。详见 `docs/frontend-phase5.md`。

#### 实现步骤

- 默认启用构建版，保留 legacy 开关至少两个发布版本。
- 为轮询、上传、聊天增加取消与卸载清理；使用操作 token 防止旧响应覆盖新状态。
- 对长聊天、健康趋势和大型数据列表按需渲染，不做无依据的提前虚拟化。
- 捕获未处理异常，只记录模块、endpoint、状态码和 clientCorrelationId。
- 生成 bundle 报告，并以阶段 1 的实测结果设预算；预算写入配置而不是文档中的拍脑袋数值。
- 使用阶段 0 同一套 Playwright fixture 和截图配置进行视觉回归。

#### 验收门槛

- 快速切日期、重复弹窗、连续刷新、重复上传、断网恢复和并发保存场景通过。
- Playwright screenshot 测试在 CI 通过；动态区域已固定或 mask。任何 golden 更新必须由维护者在独立提交中审阅。
- 浏览器控制台无未处理异常；请求取消不展示为业务失败。
- 默认版和 legacy 版均完成一次 Docker 回退演练。

### 阶段 6：移除回退与持续治理（发布后）

> 状态：**回退删除已完成，模块化未完成（2026-09-11）**。已删除回退开关、`/legacy`
> 挂载、冻结资源和生成链，并加入初步治理检查；但把生成代码改名为 `app/runtime.ts`
> 只是解除生成依赖，不是领域迁移。阶段 6 不再计入“前端模块化完成”的证明。

- 记录每次发布是否启用 legacy 回退及原因。
- 连续两个发布版本未使用回退，并且关键 E2E、视觉回归和错误率无回归后，单独提交删除 legacy。
- 删除时同步清理环境变量、冻结资源、文档、Docker 配置和相关测试。
- 后续新增 API 必须同时添加类型、错误映射和调用测试；新增领域不得绕过依赖边界。

### 阶段 7：建立真实迁移基线与硬闸门（1-2 天）

> 状态：已完成（2026-09-11）。`runtime-budget.json` 锁定只减不增指标，
> `runtime-function-ownership.json` 覆盖全部剩余函数，`dom-ownership.json` 覆盖页面
> 全部 id；`check:runtime` 与规则反向测试进入 CI，可识别 direct/property/computed/alias
> fetch、新增 runtime 函数、无归属函数/DOM 和跨领域 DOM 访问。

目标：先让 CI 能识别“业务仍留在 runtime”以及“通过计算属性绕过 fetch 检查”，确保后续
每个领域提交都产生可量化进展。

#### 实现步骤

1. 新增 runtime inventory，按函数、API 路径、DOM id、事件监听和全局变量输出 JSON 基线。
2. 建立 `docs/frontend-runtime-migration.md`，把每个 runtime 函数和 DOM 根节点归属到唯一领域。
3. 扩展依赖检查，识别 `fetch`、`window.fetch`、`globalThis.fetch`、`globalThis['fetch']` 和别名调用。
4. 禁止领域代码读取其他领域 DOM 根节点、导入其他领域私有文件或访问 runtime 全局函数。
5. 为每个领域记录迁移前后的 runtime 指标；任何提交不得让函数、监听、DOM 查询或网络调用增加。
6. 修正阶段 3 治理测试：不再只检查文件存在，而是检查 controller 调用领域 API、events 绑定真实事件、view 渲染领域根节点。

#### 验收门槛

- CI 能故意捕获一处 `globalThis['fetch']`、跨领域 DOM 查询和新增 runtime 函数。
- 128 个函数、103 个监听、163 个 DOM 查询和 20 个请求全部进入归属台账，无 `unknown`。
- `runtime.ts` 只允许减少，不允许新增任何业务函数、请求或监听。

### 阶段 8：迁移 session、settings 与应用生命周期（2-3 天）

> 状态：已完成（2026-09-11）。session controller/view/state 已接管会话介绍、Garmin
> 恢复时间、外部模型设置、档案/存储提示、LLM 连通性、退出及销毁；新增 `sessionApi`
> 并修正 logout 请求字段为后端实际契约 `messages`。runtime 指标降至 118 个函数、
> 98 个监听、152 个 DOM 查询和 16 个请求。训练启动与轮询按所有权留给阶段 9。

目标：让 `bootstrap` 真正拥有启动、销毁、轮询、维护状态、LLM 设置和退出流程，先移除
runtime 对整个应用生命周期的控制。

#### 实现步骤

1. 将会话介绍、档案/存储状态加载迁入 session controller；训练轮询和隔离训练通知明确归阶段 9 的 workout controller。
2. 将外部模型开关、隐私说明和 LLM 连通性测试迁入 settings API 与 session/settings view。
3. 将退出摘要、取消在飞请求、定时器清理和 `pagehide` 清理统一放入应用生命周期。
4. controller 通过注入获得 API、clock、operations、shell 和领域根节点，不读取全局函数。
5. 增加 fake timer、取消、403/503、重复启动和 destroy 后不更新 DOM 的测试。
6. 同提交删除 runtime 中对应函数、全局变量和监听。

#### 验收门槛

- session controller 存在真实 API 调用和用户行为测试，不再只是设置 dataset。
- runtime 不再注册会话启动、退出、LLM 测试或页面卸载事件；训练轮询作为阶段 9 的显式剩余项登记。
- `bootstrap()`/`destroy()` 重复调用不会产生重复定时器或监听。

### 阶段 9：迁移 workout 与 uploads（5-7 天）

> 状态：已完成（2026-09-11）。workout controller/view/state 已接管 pending 与历史训练
> 草稿、轮询、revision 保存冲突、撤销/恢复及隔离训练启动提示；uploads controller/view/state
> 已接管 FIT、计划、健康批次、多 ZIP 活动选择和餐盘图片。领域通过类型化事件协作，
> 已删除 runtime 中对应实现和全局选择器出口。runtime 指标降至 83 个函数、81 个监听、
> 118 个 DOM 查询和 8 个直接请求。

目标：迁移耦合最深的训练草稿、保存冲突和多文件上传流程，建立领域间通过事件而非 DOM
或全局函数协作的范式。

#### 实现步骤

1. workout state 分离服务器快照、编辑草稿、选择集合、revision、冲突和提交状态。
2. 将训练卡片、组编辑器、休息/力量/有氧段渲染迁入 workout view，禁止 HTML 字符串拼接。
3. 将 pending workout 获取、更新、保存、撤销、隔离预览/恢复全部切到 `workoutApi`。
4. uploads controller 接管 FIT、计划、健康 ZIP/CSV、餐盘图片以及多 ZIP 活动选择器。
5. 用领域事件传递“上传产生训练”“计划上传完成”等结果，不调用 `window.openActivityPicker`。
6. 覆盖旧响应、409 revision 冲突、重复上传、关闭弹窗取消、同名跨 ZIP 活动和字段校验。
7. 迁移对应 Python 源码断言并从 ARCH-09 白名单删除，同提交删除 runtime 实现。

#### 验收门槛

- workout/uploads 不存在 runtime 兼容出口、`window.*` 业务函数或直接网络请求。
- 训练草稿不会被轮询覆盖；409 显式进入冲突状态。
- 上传选择器只有一个事件所有者，多 ZIP 行为测试和 Playwright 流程通过。

### 阶段 10：迁移 health 与 checkin（4-5 天）

> 状态：已完成（2026-09-14）。health controller/view/state 已接管健康概览、趋势、
> daily/range/sleep、导入详情与删除操作；checkin controller/view/state 已接管按日期载入、
> 餐食估算草稿、表单保存和 422 字段错误映射。日期、营养与累计趋势计算已迁入 shared
> 纯函数。runtime 指标降至 58 个函数、64 个监听、90 个 DOM 查询和 5 个直接请求。

目标：迁移日期敏感和竞态密集的健康概览、趋势、睡眠、每日打卡与营养估算。

#### 实现步骤

1. health controller 接管 overview、daily、trend、range、sleep 和导入详情 API。
2. checkin controller 接管日期切换、表单草稿、餐食估算、保存和字段错误映射。
3. 将日期/时区、趋势累计值计算和展示格式保留在 shared 纯函数，不在 view 中计算业务规则。
4. 每种可取消请求使用独立 operation key；只有最新 token 可以提交状态和渲染。
5. view 仅渲染传入状态，不主动请求 API，不读取其他领域 DOM。
6. 迁移热量/BMR、趋势和打卡相关源码断言，同提交删除 runtime 对应代码。

#### 验收门槛

- 快速切日期/指标/周期时旧响应不能覆盖新状态，关闭弹窗后不得重绘。
- health/checkin 的 API、状态、事件和 DOM 全部由各自领域拥有。
- 对应 runtime 网络调用、监听和 DOM 查询归零。

### 阶段 11：迁移 chat 与 data-management（5-7 天）

> 状态：已完成（2026-09-14）。chat 与 data-management 已接管历史和发送、artifact、
> 档案确认、记录/计划/记忆/酸痛、审计、隔离训练、恢复点、备份恢复及数据查看器；
> runtime inventory 已归零，6 条遗留模板耦合断言已迁移为 Vitest/Playwright 行为测试。

目标：迁移最后两个大领域，并清除 runtime 中剩余业务实现。

#### 实现步骤

1. chat controller 接管历史、发送状态、Markdown 消息、计划 artifact、draft ID 和档案确认。
2. data-management controller 接管记录、计划、记忆、酸痛、文件审计、隔离训练、恢复点和备份恢复。
3. 大列表继续使用 chunked-list；破坏性操作统一使用 ConfirmDialog，并按资源精确刷新。
4. Markdown 只通过 `shared/sanitize.ts`；错误展示保留 403、409、422、503 差异。
5. 将剩余 Python 模板源码断言迁到 Vitest/Playwright/HTTP 契约测试并清理 ARCH-09。
6. 删除 runtime 中每个已迁移函数、事件和全局状态；不得保留“临时兼容调用”。

#### 验收门槛

- runtime inventory 的函数、请求、监听和 DOM 查询全部为 0。
- 七个领域都有 controller/API 协作测试和至少一条用户行为测试。
- `frontend/src` 不存在直接或变形 fetch，不存在跨领域私有导入。

### 阶段 12：删除单体与旧模板，最终收口（2-3 天）

> 状态：已完成（2026-09-14）。两个单体文件、动态导入和迁移期豁免已物理删除；最终
> HTML 壳拆为 header/workspace/modals 三个构建期片段。最终架构闸门持续禁止兼容
> 单体、API 绕过、跨领域 DOM 访问和超大源文件回流。

目标：物理删除两个单体来源，并证明应用完全由模块化源码运行。

#### 实现步骤

1. 删除 `frontend/src/app/runtime.ts`，移除 `main.ts` 中的动态导入和 eslint/prettier 豁免。
2. 删除 `templates/index.html`、`LEGACY_INDEX`、旧断言扫描器和只服务于模板源码的测试治理代码。
3. 将最终 HTML 壳按稳定页面区域拆为语义模板或领域 mount point；不得重新形成内联脚本。
4. 更新 bundle 预算，确保不存在 runtime chunk；检查所有产物 chunk 均有明确所有者。
5. 全量执行 lint、typecheck、boundaries、governance、Vitest、Python、Playwright 和 Docker smoke。
6. 人工审阅三视口视觉 diff、键盘流程、Network 面板和浏览器控制台。

#### 最终验收门槛

- 仓库中不存在 `runtime.ts`、`templates/index.html` 或任何同等职责的超大兼容文件。
- `main.ts` 只安装应用级基础设施并调用 `bootstrap`，不加载业务实现文件。
- API 访问 100% 经过 `frontend/src/api`，DOM/事件所有权 100% 落在唯一领域或共享组件。
- 旧模板耦合测试为 0，ARCH-09 无前端源码断言豁免。
- 删除任一领域 controller 会让对应行为测试失败，证明领域模块不是占位壳。

## 6. 状态与并发规则

- 每个领域拥有自己的状态；`app-state` 只保存跨领域且稳定的信息，如当前会话状态和维护状态。
- 服务器快照、编辑草稿和提交状态分开建模。
- 每个异步操作保存递增 token；只有最新 token 能更新 UI。
- 切换日期、关闭弹窗、离开编辑模式或卸载页面时取消不再需要的请求。
- 409 进入显式冲突解决状态；403 显示隐私/权限原因；422 映射到字段；503 显示维护或存储降级信息。
- 删除、恢复、重置等破坏性操作必须二次确认，成功后精确刷新相关领域，不做整页隐式重载。

## 7. 测试矩阵

| 层级        | 工具                  | 必测内容                                                          |
| ----------- | --------------------- | ----------------------------------------------------------------- |
| 纯函数      | Vitest                | 格式化、日期、营养计算、校验、错误映射、reducer                   |
| 安全边界    | Vitest + jsdom        | Markdown sanitize、HTML 注入、危险 URL、敏感信息不持久化          |
| 领域模块    | Vitest + jsdom        | controller/API 协作、空态、加载、取消、冲突、重复事件             |
| Python 契约 | pytest/unittest       | 静态挂载、首页选择、路由兼容、ARCH-09、main facade                |
| E2E         | Playwright            | 聊天、FIT 编辑保存、健康导入、打卡、备份恢复、退出                |
| 视觉        | Playwright screenshot | 首屏、训练编辑器、健康概览、数据管理，三种 viewport               |
| 交付        | Docker + HTTP smoke   | 镜像构建、首页、assets、旧回退路径 404、健康检查、无 node_modules |

CI 必须按顺序执行：Python 快速治理测试、前端 lint/typecheck/unit、前端 build、Python 全量测试、Docker build/smoke、Playwright E2E/截图。失败时不得发布制品。

## 8. 风险与回滚

| 风险                             | 预防措施                                   | 回滚方式               |
| -------------------------------- | ------------------------------------------ | ---------------------- |
| 源码断言随文件迁移失效           | 扫描台账、frontend_map、同提交更新 ARCH-09 | 回滚该领域原子提交     |
| 静态资源 404 或缓存错误          | 独立交付模块、HTTP smoke、资源内容哈希     | 回滚上一稳定镜像       |
| Docker 缺产物或携带 node_modules | 多阶段构建、镜像内容检查                   | 使用上一稳定镜像       |
| 维护模式阻断页面资源             | `/assets/` 只读白名单和专项测试            | 回滚上一稳定镜像       |
| 异步响应覆盖编辑草稿             | AbortController、操作 token、草稿分离      | 禁用对应自动刷新       |
| Markdown XSS                     | 唯一 sanitize 入口和恶意 fixture           | 禁用富文本，回退纯文本 |
| 外部字体/CDN 泄露访问            | 全部资源本地化、断网 E2E                   | 回滚上一稳定镜像       |
| 新旧实现重复监听                 | 每片迁移同步删除、行数闸门、诊断           | 回滚当前领域提交       |
| CI 与其他计划重复建设            | 明确本计划只拥有基础 workflow 和前端 job   | 合并到唯一 workflow    |

## 9. 排期与提交策略

阶段 0-12 已全部完成。下表保留原始估算，用于复盘，不再表示剩余工作：

| 阶段                     | 预计时间 |
| ------------------------ | -------: |
| 阶段 7：真实基线与硬闸门 |   1-2 天 |
| 阶段 8：session/settings  |   2-3 天 |
| 阶段 9：workout/uploads   |   5-7 天 |
| 阶段 10：health/checkin   |   4-5 天 |
| 阶段 11：chat/data        |   5-7 天 |
| 阶段 12：删除单体并收口   |   2-3 天 |

建议提交序列：

1. `test(frontend): freeze baseline and add assertion migration ledger`
2. `build(frontend): add vite typescript and local vendor assets`
3. `build(app): serve frontend assets and add docker builder`
4. `ci: add frontend build test and smoke jobs`
5. `refactor(frontend): add typed api client and safety utilities`
6. 按领域分别提交 `refactor(frontend): migrate <domain>`，每个提交包含旧代码删除和测试迁移。
7. `refactor(frontend): consolidate shared components and styles`
8. `chore(frontend): enable built frontend by default`
9. 两个稳定版本后单独提交 `chore(frontend): remove legacy frontend`。
10. `test(frontend): inventory runtime ownership and close fetch bypasses`。
11. 按阶段 8-11 每个领域分别提交 `refactor(frontend): move <domain> out of runtime`；每个提交必须包含 runtime 删除量和行为测试。
12. `chore(frontend): remove runtime and obsolete template assertions`。

每个提交都必须可独立构建和回滚；不得把无关业务需求、视觉改版或后端重构混入迁移提交。

## 10. 完成定义

- 默认页面完全由 `frontend/src` 构建，入口只负责应用组装。
- legacy 回退及其环境变量、挂载、冻结资源和测试已删除。
- `frontend/src/app/runtime.ts` 与 `templates/index.html` 均已删除，不存在改名后的等价单体文件。
- 七个领域 controller 各自拥有真实 API、状态、事件和 DOM；不得用 dataset 写入或空回调充当完成证明。
- 所有网络访问经过类型化 API 层；所有富文本经过唯一 sanitize 入口。
- 不存在 `fetch`、`window.fetch`、`globalThis.fetch`、计算属性 fetch 或其别名绕过。
- ARCH-09 没有前端源码断言债务，Python 测试不再读取旧模板。
- Python、本地开发、Docker 和源码发布包四条运行链路均有文档和自动验证。
- 关键业务 E2E、三种 viewport 视觉回归和静态资源 smoke 全部通过。
- CI 成为发布制品的实际执行者，而不是文档中的人工约定。
