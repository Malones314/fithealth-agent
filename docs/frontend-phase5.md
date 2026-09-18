# 前端模块化阶段 5 交付说明

> 历史记录：本文记录的回退开关及阶段 6 待清债务均已完成清理，现状见
> `docs/frontend-phase6.md`。

> 完成日期：2026-09-11

阶段 5 收口：默认构建版正式生效、竞态与取消、按需渲染、异常捕获、bundle 预算和
视觉回归进 CI。12 张三视口视觉 golden **一张未改**。

## 取消与操作 token

新增 `shared/operations.ts`：按操作名持有递增 token 与 AbortController。

- **旧响应覆盖新状态**：只比"有没有在飞"不够，先发的请求可能后到。每轮拿一个
  token，只有 `isCurrent()` 为真才允许写 UI。
- **离开后仍在写**：关弹窗、会话结束、页面卸载时取消在飞请求。卸载走 `pagehide`
  而不只是 `beforeunload`——移动端 Safari 进后台不触发后者。

接入的路径（`scripts/rewire_frontend_phase5.mjs`，17 条规则，命中次数精确校验）：

| 路径 | 处理 | 基线的问题 |
| --- | --- | --- |
| `/health/trend` | token + signal + abort 不算失败 | **完全没有守卫**，切日期/指标/周期会让旧数据盖掉新数据 |
| `/health/overview` | 同上，且关弹窗时取消 | 无守卫；关掉后仍会渲染一次 |
| `/workout_state` 轮询 | token + 会话结束/退出时取消 | `clearInterval` 只停下一次调度，在飞的那一发仍会写训练卡片 |
| `/chat` | signal + abort 不写错误气泡 | 取消会显示成"请求失败：..." |
| `/upload_fit`、`/upload_health` | signal | 卸载后请求挂着直到超时 |

`loadViewerDay` 基线自带 `viewerLoadToken`，语义已正确，没有重复包装。

聊天与上传**不做**"新请求取代旧请求"：发送按钮在飞行期禁用、上传是显式一次一个，
静默丢弃已上屏的提问比等它回来更糟。它们只接 signal，供卸载时停止。

## 按需渲染（先量后改）

实测一次性渲染的成本（jsdom，每行 5 节点）：

```text
rows=  50 →   8.0ms      rows=1000 →  63.2ms
rows= 200 →  16.4ms      rows=2000 → 136.1ms
rows= 500 →  34.9ms      rows=5000 → 316.9ms
```

线性，无断崖。所以**不引入虚拟滚动**——它要接管滚动容器、行高测量和滚动锚定，
这个量级上换不来收益，且列表本来就有 `max-height:240px` 滚动区。计划明确禁止
"无依据的提前虚拟化"。

`components/chunked-list.ts` 只在超过 200 条时分批（每批 100）。阈值取 200 是因为
实测 16ms 仍在一帧预算内，且覆盖绝大多数真实数据量——常见场景不会多出按钮，
未超阈值时与一次性渲染逐节点等价。

只改真正无界增长的两个列表：训练记录、每日/营养记录。计划、记忆、酸痛、恢复点、
隔离文件都有天然上限或会被清理，几十条是个位数毫秒，改了只会多个没人点的按钮。

## 异常捕获

`app/error-reporting.ts` 只记录**模块、endpoint、状态码、serverCode、
clientCorrelationId**。刻意不记录 message、stack 和任何请求/响应 body——那些可能
带回健康数据或用户输入。endpoint 剥掉查询串（`?day=2026-09-11` 本身就是信息）。
只写控制台，不外发：任何上报端点都会把诊断信息送出本机。

取消不是异常：`AbortError` 直接丢弃并 `preventDefault()`，否则控制台会充满假错误，
"控制台无未处理异常"这条门槛也就失去意义。

为此给 `ApiError` 加了 `endpoint` 字段——否则上报处只能去翻 `details`，而那里装着
服务器响应体。

## bundle 预算

预算写在 `frontend/bundle-budget.json`（不是文档里的数字），由
`npm run check:bundle` 在 CI 与 Docker 构建阶段执行。比较 gzip 后字节。

```text
app-js             24943 B  limit 28600 B
app-css             8153 B  limit  9400 B
shared-chunks-js    3983 B  limit  4300 B
legacy-runtime-js  32170 B  limit 36900 B
TOTAL              69249 B  limit 79200 B
```

余量约 15%：太小会让无关改动天天撞线，太大则失去意义。`legacy-runtime` 单列，
因为它随阶段 6 删除回退后整块消失——混算会把那份收益平均掉、看不出来。

闸门还会拦两件事：产物改名导致预算静默失效（匹配不到即失败，不当 0 通过）、
新增 chunk 忘记加预算（预算外的 JS/CSS 一律报错）。冻结 legacy 资源显式排除，
理由写在配置里。

## 修掉的四个真实缺陷

都不是阶段 5 引入的：

1. **阶段 4 的空态改写写错了变量名**。规则用 `(\w+)` 抓宿主变量，而 `\w` 不含 `$`，
   于是 `$wList.innerHTML = …` 变成 `$shell().empty(wList, …)`：调用不存在的
   `$shell`，传不存在的 `wList`。生成文件带 `@ts-nocheck`，typecheck 不看；文件当时
   又整体被 eslint 忽略。两条空态分支（"该日期没有已保存的训练组"、"上传 .fit 文件
   后将在此显示训练组"）一走到就会抛。
2. **Docker 构建从阶段 4 起就是坏的**。`.dockerignore` 排除 `scripts/` 后只放行了
   `freeze_frontend_phase1.mjs`；阶段 4 给冻结脚本加上
   `import ... "./rewire_frontend_phase4.mjs"` 之后镜像构建一直失败，说明阶段 4 声称
   的 Docker 验证并没有真正跑过。改成按 `scripts/*frontend*.mjs` 放行。
3. **`main.ts` 的加载顺序是碰巧成立的**。它在第 11 行 `await import` legacy-runtime，
   却在 20-21 行才把 `DOMPurify`/`marked` 挂到 window——只因为它们在后续回调里才被
   用到才没炸。现在顺序显式化并注释了约束。
4. **规则歧义**：每日记录与健康导入两个列表的收尾行逐字相同，裸用会命中两处、改坏
   健康导入列表。命中次数校验把它拦下来了，改用独有的上文区分。

针对第 1 条补了闸门：`eslint.config.js` 单独对 `legacy-runtime.ts` 开 `no-undef`
（其余规则不适用于生成文件）。它当即又报出 `DOMPurify`/`marked` 两个全局，促成了
第 3 条的修复。机械改写引入的未定义标识符从此在 CI 里就会被拦住。

## 竞态回归测试

`e2e/frontend-races.spec.ts` 覆盖验收门槛里的场景：慢的旧响应不能覆盖新的、取消
不显示为业务失败、快速切日期、重复开关弹窗（不叠加、不泄漏滚动锁）、飞行中关闭
不再重绘、断网恢复、连续刷新。每条都断言可观察结果，不断言内部 token 数值。

**这些测试做过反向验证**：把趋势路径恢复成阶段 5 之前的写法后，
"慢的旧响应不能覆盖新的"确实失败（屏幕上留下 `2026-09-10`，而不是请求的
`2026-09-08`）。只删 token 守卫、保留 signal 时它仍然通过——因为 abort 已经拦住了
旧响应。也就是说这条测试确实在测竞态，而不是恒真。

## 验证结果

```powershell
cd frontend
npm run lint; npm run typecheck
npm run check:boundaries; npm run check:migration
npm run check:components; npm run check:styles
npm test; npm run build; npm run check:bundle
npx playwright test e2e/
```

- 前端单测 **133 passed**（22 个文件），新增 operations 11、error-reporting 12、
  chunked-list 12。
- Playwright **69 passed**：12 张视觉基线未变、33 条布局/无障碍、21 条竞态、
  3 条资产冒烟。
- Python 全量 **1133 passed**。
- Docker 两条路径均实机演练：默认版只引用 `/assets`（3 个资源全 200，无 legacy.css、
  无外部 CDN）；`FITHEALTH_FRONTEND_LEGACY=1` 提供冻结页面（6 个本地资源全 200，
  不引用 `/assets`）；镜像内无 `node_modules`、无前端源码；两者健康检查均 200。

## CI 变化

新增 `check:bundle`、`test:e2e:races`，并把**视觉回归**接入（阶段 4 只跑了布局与
冒烟）。CI 只比对截图、绝不 `--update-snapshots`——golden 更新必须由维护者在独立
提交中审阅。失败时上传 `playwright-report/` 以便查看 diff。

## 留给阶段 6 的债务

`legacy-runtime.ts` 里仍有 23 处 `innerHTML`（训练卡片渲染），由
`check:components` 的只降不升预算计量。搬进 `domains/workout/view.ts` 属于删除
legacy 回退时的收尾工作。
