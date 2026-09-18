# Runtime 迁移台账

> 建立日期：2026-09-11

阶段 12 已关闭本台账。迁移期 runtime、预算和函数所有权文件均已删除；最终状态由
`npm run check:architecture` 持续验证。

## 指标

| 阶段 | 函数 | 事件监听 | DOM 查询 | 绕过 API 的请求 |
| --- | ---: | ---: | ---: | ---: |
| 阶段 6 复核基线 | 128 | 103 | 163 | 20 |
| 阶段 8 完成后 | 118 | 98 | 152 | 16 |
| 阶段 9 完成后 | 83 | 81 | 118 | 8 |
| 阶段 10 完成后 | 58 | 64 | 90 | 5 |
| 阶段 11 完成后 | 0 | 0 | 0 | 0 |
| 最终目标 | 0 | 0 | 0 | 0 |

阶段 12 已删除 runtime 文件及动态导入，不再产生 runtime chunk。

## 所有权来源

- `frontend/dom-ownership.json`：覆盖 `frontend/index.html` 的全部 id；领域访问其他领域
  id 会失败，新增或删除 id 必须同步更新。
- `scripts/check_frontend_architecture.mjs`：禁止已删除单体重新出现，检查直接/变形 fetch、
  DOM 所有权和反单体行数上限。
- `scripts/frontend_architecture_rules.test.mjs`：反向证明 fetch 变体和跨领域 DOM 能被检测。

## 原子迁移规则

迁移已完成。后续修改必须保持 typed API、领域 DOM 所有权和单一 controller 行为测试，
不得重新引入兼容单体或扩大架构豁免。
