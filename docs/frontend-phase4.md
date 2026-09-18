# 前端模块化阶段 4 交付说明

> 历史记录：本文中的冻结资源、生成脚本和 legacy-runtime 名称已在阶段 6 清理。

> 完成日期：2026-09-11

阶段 4 把共享 UI 组件和样式结构化，不夹带视觉改版。等价性由阶段 0 的三视口
截图基线担保：`frontend/e2e/__screenshots__/` 下 12 张 golden **一张未改**，
拆分与改写前后逐像素一致。

## 共享组件

`frontend/src/components/` 是这批行为的唯一实现处：

| 组件 | 统一了什么 | 替代的 legacy 写法 |
| --- | --- | --- |
| `modal.ts` | 焦点圈定、Escape、焦点恢复、backdrop、body 滚动锁 | 4 个模态框各自 `classList.add('show')` + `aria-hidden` + `body.style.overflow`，外加一条三分支 Escape 监听 |
| `toast.ts` | 全局提示与"已保存摘要"块 | `addNotify` 手拼 `msg system` 结构，摘要经 `innerHTML` |
| `empty-state.ts` | 空态与加载态 | 13 处 `innerHTML = '<div class="data-empty">…'` |
| `confirm-dialog.ts` | 破坏性操作二次确认 | 28 处直接 `window.confirm` |
| `file-dropzone.ts` | 拖拽深度计数、`dropEffect`、input 复位、文件校验 | chat panel 上手写 4 个 drag 监听 + 各入口重复校验 |
| `tabs.ts` | 单选按钮组的 `active` class、ARIA、方向键 | 趋势周期与数据类型两处手写 `classList.toggle('active', …)`，均无 ARIA |
| `button.ts` | 禁用、`aria-busy`、loading 文案还原 | 各处自行 `disabled = true` 并可能忘记还原文案 |

`app/shell.ts` 在启动时实例化这些组件一次，领域模块和迁移期的 `legacy-runtime`
都通过 `shell()` 取用同一实例——不会出现两份模态框或两条 Escape 监听。

改写是**机械的**：`scripts/rewire_frontend_phase4.mjs` 定义 22 条规则，每条都要求
命中次数精确匹配，由 `npm run freeze:legacy` 在生成 `legacy-runtime.ts` 时执行。
基线一变规则立刻失败，而不是静默漏改一处、留下两套实现。因此**不要手改**
`src/app/legacy-runtime.ts`。

## 样式结构化

`public/legacy/legacy.css`（833 行）拆成按导入顺序层叠的 9 个文件：

```text
src/styles/index.css        # 导入顺序即层叠顺序，不能重排
├─ tokens.css               # 设计 tokens
├─ base.css                 # reset 与 body
├─ layout.css               # header、main-grid、sidebar、resizer
├─ components.css           # 模态框骨架、data-* 列表、预览、隐私设置
├─ domains/chat.css
├─ domains/health.css
├─ domains/workout.css
├─ responsive.css           # @media (max-width: 900px)
└─ theme-overrides.css      # 深色主题覆写与 1050/640 断点
```

默认页面不再引用冻结的 `legacy.css`，字体改由 `@fontsource/inter` 打包进
`/assets`（不请求 Google Fonts）。冻结页面保留自己的 `legacy.css` + `fonts.css`，
仅用于 `FITHEALTH_FRONTEND_LEGACY=1` 回退。

拆分不允许改变层叠结果，由 `scripts/check_frontend_styles.mjs` 担保：选择器多重
集合相同、每个选择器的声明逐条相同、重复选择器（54 个，主题覆写造成）的相对
顺序相同、每行非空基线内容被覆盖且只覆盖一次。当前结果：468 条规则全部对齐。

## 阶段 4 修掉的三个真实缺陷

写组件测试时暴露出来的，都不是拆分引入的：

1. **模态框栈泄漏**：宿主被整体替换时模态框不走 `close()`，永远留在栈里——
   Escape 打在看不见的框上，`body` 滚动锁再也解不开。`modal.ts` 现在每次读栈前
   剔除已脱离文档的条目。
2. **焦点圈定不生效**：可见性判断用了 `offsetParent`，在 jsdom 里恒为 null，把焦点
   环缩成一个元素；且焦点起始在框外时不会被拉回框内，用户能一路 Tab 到底层页面。
   改用 `hidden` 属性与计算样式，并补上"框外 → 先进入框内"分支。
3. **`valueOf` 命名碰撞**：`TabsOptions.valueOf` 与 `Object.prototype.valueOf` 同名，
   `options.valueOf ?? fallback` 永远拿到继承来的方法，默认实现根本不会生效。
   改名为 `readValue`。

## 闸门

新增两条静态检查和一条 e2e，均已进入 CI：

- `npm run check:components`：领域模块不得自行操作模态框 `show`/`aria-hidden`/body
  overflow、拼 `msg system` 通知条或 `data-empty` 空态、调用 `window.confirm`、
  注册 Escape 或 drag/drop 监听；`innerHTML` 只允许出现在 `shared/sanitize.ts`。
  `legacy-runtime.ts` 里剩余的 23 处 `innerHTML`（训练卡片渲染）是**既有**债务，
  用只降不升的预算单独计量，搬进 `domains/workout/view.ts` 属于阶段 5。
- `npm run check:styles`：拆分与冻结基线层叠等价。
- `npm run test:e2e:layout`：三视口下无横向页面溢出（剔除 `overflow-x:auto` 的
  刻意横向滚动区）、固定格式控件尺寸不因动态文本改变、模态框 Escape 关闭并归还
  焦点、Tab 圈定在框内、按钮组是可键盘导航的 tablist、无外部静态请求与未处理
  控制台异常。

## 验证结果

```powershell
cd frontend
npm run lint; npm run typecheck
npm run check:boundaries; npm run check:migration
npm run check:components; npm run check:styles
npm test; npm run build
npx playwright test e2e/
```

- 前端单测 98 passed（19 个文件），其中共享组件 58 条覆盖键盘、ARIA、禁用、
  加载与错误状态。
- Playwright 48 passed：12 张视觉基线未变，33 条阶段 4 布局/无障碍检查，
  3 条资产冒烟。
- Python 全量 1133 passed。
- ARCH-09 恢复干净：删除了阶段 3 遗留的
  `test_migration_inventory_has_no_default_legacy_script`——它读 `index.html` 做正则
  匹配，是一条源码文本断言，从阶段 3 起就让守门器失败。同样的检查已由
  `npm run check:migration` 机械执行且覆盖更宽，故直接删除而非新增豁免。
