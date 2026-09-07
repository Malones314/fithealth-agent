"""observability/http.py — 哪些 HTTP 路由开 turn（agent-trace 阶段 2）。

## 为什么要一份显式白名单

turn 的边界不能靠"谁想起来就加一句 `start_turn`"。这张表是**接入清单**，
`tests/test_trace_http_boundary.py` 按它逐条验证：列进来的路由必须真的开出恰好一个
回合，没列的必须一个都不开。新增一条跑模型的路由时，改表会让测试先红。

## 为什么不放在中间件里

`runtime/middleware.py` 的 `maintenance_guard` 包住**所有**路径，包括静态首页和
`/health/storage-status` 轮询。在那里开 turn 会给每次轮询都生成一个空 trace 文件。

## 刻意排除的东西（审查意见要求写明）

| 排除项 | 理由 |
| --- | --- |
| 请求体解析/体积拒绝（`read_chat_request` 抛 `ContextInputError`） | 请求根本没到 agent，没有"为什么这么答"可解释；4xx 本身已经可见。更关键的是它发生在读 body 期间——在那之前开 turn，等于让客户端用超大请求随意造 trace 文件。 |
| `/`、`/health/storage-status`、`/docs`、`/redoc` | 没有模型调用也没有闸门；前端会轮询存储状态，一开就是刷文件。 |
| 维护期中间件返回的 503 | 请求被挡在业务之前，没有任何决策发生。 |
| `/data/*`、`/plans/*`、`/profile/*`、`/workout_state*`、`/upload_fit`、`/upload_health` | 纯本地 CRUD 与文件解析，不碰外部模型、不走确定性闸门。计划正文的闸门发生在 `/chat`（`source="uploaded_plan"`），已被 `/chat` 覆盖。 |

排除不等于不可观测：这些路径的失败仍然走 `logger.exception` 与 HTTP 状态码。
"""

from __future__ import annotations


#: 开 turn 的路由 -> 为什么。值只是文档，不参与判断。
TRACED_ROUTES: dict[str, str] = {
    "/chat": "7 个外部模型触点 + 全部确定性闸门 + 两个 ReAct 循环",
    "/analyze_food": "餐盘图片走视觉模型（analyze_food_image）",
    "/logout": "退出摘要走信息路由（route_information），会写跨会话记忆",
    # 阶段 2 的排除表原本把这条列成"纯文件解析，不碰外部模型"——**那是错的**。
    # 阶段 4 的静态扫描发现 `plan_classifier._level2_llm_check` 也在往
    # /chat/completions 发请求（判断上传的 Markdown 是不是训练计划）。
    "/upload_plan": "上传计划的语义分类走轻量模型（validate_training_plan）",
}
