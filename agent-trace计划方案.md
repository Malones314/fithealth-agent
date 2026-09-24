# Agent Trace 现状评估与完善实施方案

> 适用分支：`agent-trace`　｜　基线提交：`68e9443`　｜　编制日期：2026-09-04
>
> 本文所有"现状"结论均经过实测或源码定位，附文件行号；未验证的推测统一标注「待确认」。

ChatGPT 5.6Sol审查意见：基线提交、适用分支和验证标注为审查提供了可追溯性；建议每次阶段提交同步更新基线、测量日期和未决假设清单。

---

## 0. 一句话结论

**这个项目已经在产出 agent trace，但没人有意打开过它。** `hello-agents 1.0.0` 的
`Config.trace_enabled` 默认为 `True`，而 `create_fithealth_agent()` 从未传 `config`，
于是每次 `/chat` 都会在 `memory/traces/` 落一对 JSONL + HTML 文件——目前已积累
**200 个文件（100 次会话，2026-08-15 ~ 08-28）**。

它的问题不是"没有 trace"，而是：

1. 只覆盖了 **7 个模型触点中的 1 个**（ReAct 主循环），另外 6 个走裸
   `requests.post` 的调用完全不可见，且全部**静默失败**；

ChatGPT 5.6Sol审查意见：覆盖率问题是最直接的排障缺口；建议先建立完整触点清单和统一调用标识，再逐步接入，避免不同模块产生无法横向比较的事件。
2. 本项目的行为**主要由确定性闸门决定**（健康风险、周计划决策、计划校验、
   自动修正），这些一行都没记；

ChatGPT 5.6Sol审查意见：判断符合业务特点；记录时应优先保留决策结果、版本和原因代码，避免为了“完整”把健康原文全部写入 trace。
3. 一次对话会产生 1~2 个互不关联的 trace 文件，**没有 turn_id**，无法与 HTTP
   请求、无法与响应里的 `source` 对上；

ChatGPT 5.6Sol审查意见：统一回合关联能显著提升可用性；建议同时保留请求 ID、重试/修正序号和父子 span，处理并发与多代理场景。
4. 含健康隐私（PHI）的明文落盘，**不受 `/data/reset` 与备份事务管辖**——
   "删除全部数据"之后健康细节仍留在 trace 里；

ChatGPT 5.6Sol审查意见：这是最高优先级风险。除脱敏和删除外，应明确文件权限、备份排除、恢复后清理及清理结果验证，形成可审计闭环。
5. 框架的 TraceLogger 在构造时就开文件句柄、只在特定返回路径 `finalize()`，
   存在**句柄泄漏 + 未闭合 HTML**，且 HTML 未做转义。

ChatGPT 5.6Sol审查意见：资源释放和输出编码应在任何新功能前止损；建议用异常、取消、并发和恶意输入测试固定这些安全不变量。

ChatGPT 5.6Sol审查意见：结论抓住了“可观测性覆盖不足、隐私治理缺口、框架副作用”三条主线，问题优先级基本合理。建议在正式执行前补充一项总体验证：确认当前分支、基线提交和样本数据的时间范围可复现，并把“200 个文件”这类动态数量改成带采样日期的快照，避免后续审查因数据增长而产生歧义。

方案分 7 个阶段，**阶段 0 是零新代码的止损**，建议单独提交。

ChatGPT 5.6Sol审查意见：七阶段拆分总体可行；建议在执行前标出关键依赖链和可并行工作，避免后续阶段因基础设施未稳定而返工。

---

## 第一部分：现状

### 1.1 trace 是怎么被打开的

调用链只有三跳，没有任何一跳是本项目主动写的：

ChatGPT 5.6Sol审查意见：调用链定位清楚；建议在后续版本同时记录框架版本和配置来源，以便区分代码缺陷与默认行为变化。

| 位置 | 代码 | 效果 | 审查意见 |
| --- | --- | --- | --- |
| `fithealth_agent/agent.py:103` | `ReActAgent(name=..., llm=..., tool_registry=..., system_prompt=..., max_steps=15)` | **没传 `config`** | ChatGPT 5.6Sol审查意见：调用方未显式传配置是根因，建议以构造契约测试锁定关键开关，而不是依赖框架默认值。 |
| `hello_agents/agents/react_agent.py:74` | `super().__init__(..., config, ...)` | `config=None` 透传 | ChatGPT 5.6Sol审查意见：透传本身简单，但第三方参数默认值可能随版本变化；应在升级检查中验证。 |
| `hello_agents/core/agent.py:43` | `self.config = config or Config()` | 落到默认值 | ChatGPT 5.6Sol审查意见：隐式回落会掩盖配置遗漏；若不能修改框架，至少在外层显式构造并记录配置快照。 |
| `hello_agents/core/agent.py:73` | `if self.config.trace_enabled:` → `TraceLogger(...)` | **开始记录** | ChatGPT 5.6Sol审查意见：构造阶段即产生副作用风险较高；应优先验证失败构造、未运行和取消场景的资源释放。 |

`hello_agents/core/config.py:42-45` 的默认值：

```python
trace_enabled: bool = True          # 是否启用 Trace 记录
trace_dir: str = "memory/traces"    # Trace 文件保存目录
trace_sanitize: bool = True         # 是否脱敏敏感信息
trace_html_include_raw_response: bool = False
```

ChatGPT 5.6Sol审查意见：默认开启 trace 与相对路径组合会放大隐私和部署风险；建议把默认值、配置来源和环境覆盖优先级写成明确契约。

实测验证（构造一个 ReActAgent，不调用 `run`）：

```
trace_logger: TraceLogger
trace files: memory\traces\trace-s-20260904-101929-74ca.jsonl
REGISTERED TOOLS: ['DevLog', 'Skill', 'Task', 'TodoWrite']
```

ChatGPT 5.6Sol审查意见：构造即创建文件的实测很有说服力；建议将该检查纳入回归测试，并同时断言目录、文件数量和句柄状态。

三个结论同时成立：trace 默认开、**文件在构造时就已创建**、以及框架顺手往
registry 里塞了 4 个与本项目无关的内置工具（见 TRACE-10）。

ChatGPT 5.6Sol审查意见：三个现象应分别验证，避免只修复 trace 开关而遗漏工具注册或构造副作用。

### 1.2 产物与格式

```
memory/traces/trace-s-<YYYYMMDD>-<HHMMSS>-<4位hex>.jsonl   机器可读，逐事件追加
memory/traces/trace-s-<YYYYMMDD>-<HHMMSS>-<4位hex>.html    人类可读，尾部带统计面板
```

ChatGPT 5.6Sol审查意见：同时生成 JSONL 和 HTML 会增加一致性与清理成本；保留单一事实源、离线渲染的方向更易治理，文件命名还应考虑跨进程冲突。

- 路径是**相对路径**，按进程 CWD 解析，**不走** `fithealth_agent/settings.py:35`
  的 `data_dir()`，也不受 `FITHEALTH_DATA_DIR` 影响（见 TRACE-12）。

ChatGPT 5.6Sol审查意见：路径脱离统一数据目录会导致容器重建丢失和测试污染；应通过解析后的绝对路径及可写性测试验证修复。
- `.gitignore:21` 已忽略 `memory/traces/`，所以没有入库泄漏；但同级的
  `memory/devlogs/` **没有**被忽略。

ChatGPT 5.6Sol审查意见：忽略规则只能防止误提交，不能防止运行时暴露；建议一次性审计 `memory/` 下所有可产生个人数据的目录。
- 单条事件结构（`trace_logger.py:97-103`）：

```json
{"ts": "...", "session_id": "s-...", "step": 1, "event": "tool_call", "payload": {...}}
```

ChatGPT 5.6Sol审查意见：现有结构缺少 schema 版本、回合关联和事件序号；新增字段时应保持向后兼容，或提供迁移/版本识别机制。

### 1.3 实际记录了哪些事件

全部由 `hello_agents/agents/react_agent.py` 的 `_run_impl` 发出，共 6 类：

ChatGPT 5.6Sol审查意见：事件来源集中便于改造；建议先确认框架事件是否还有未列出的异常/取消类型，避免“共 6 类”成为误导性边界。

| 事件 | 发出位置 | payload 字段 | 缺什么 | 审查意见 |
| --- | --- | --- | --- | --- |
| `session_start` | `core/agent.py:80` | `agent_name`, `agent_type`, `config`（整个 Config） | 无 `SYSTEM_PROMPT`、无模型 id、无工具清单 | ChatGPT 5.6Sol审查意见：启动事件应记录可复现所需的版本与配置摘要，但不应直接落盘完整配置或密钥。 |
| `message_written` | `react_agent.py:159` | `role`, `content` | 只有拼装后的 user 输入，无 system 消息 | ChatGPT 5.6Sol审查意见：消息内容属于高风险自由文本，建议默认只保留长度、哈希和角色，原文必须受严格权限控制。 |
| `model_output` | `react_agent.py:201` | `content`, `tool_calls`(计数), `usage.total_tokens`, `usage.cost` | `cost` **硬编码 0.0**；无 prompt/completion 拆分 | ChatGPT 5.6Sol审查意见：应区分供应商真实 usage 与本地估算，并记录缺失原因，避免把 0 解释成零成本。 |
| `tool_call` | `react_agent.py:275` | `tool_name`, `tool_call_id`, `args` | 无耗时 | ChatGPT 5.6Sol审查意见：工具调用需要关联父步骤、开始/结束时间和参数白名单；参数原文不宜默认记录。 |
| `tool_result` | `react_agent.py:292/342` | `tool_name`, `tool_call_id`, `result` | 无耗时、无成败状态（用户工具分支连 `status` 都没有）、无截断标记 | ChatGPT 5.6Sol审查意见：结果应统一状态枚举并带截断标志、字节数和错误分类，避免大结果或健康数据无限写入。 |
| `session_end` | `react_agent.py:227/314/376` | `duration`, `total_steps`, `final_answer`, `status` | — | ChatGPT 5.6Sol审查意见：结束事件应保证 exactly-once 或可去重，并在取消、超时和进程异常时标记完整性。 |
| `error` | `react_agent.py:184` | `error_type`, `message` | **仅 LLM 调用异常**，工具异常不记 | ChatGPT 5.6Sol审查意见：异常分类应覆盖工具、序列化、存储和清理错误；消息字段需脱敏并限制长度。 |

一个真实样本（`memory/traces/trace-s-20260828-171237-b9d6.jsonl`，24 条事件）：
`session_start` → `message_written` → 第 1 步 `model_output`（`tool_calls: 10`，
12406 tokens）→ `Thought` → 9 次 `search_youtube_video` → 第 2 步 `model_output`
→ `session_end`（`duration: 114.06s`，`total_steps: 2`）。

ChatGPT 5.6Sol审查意见：样本能证明 trace 对定位时序和结果差异有价值；建议把样本转成脱敏固定 fixture，并注明是否代表典型负载，避免单例推导普遍结论。

**这份 trace 已经产生过实际价值**：`开发日志.md:291` 记录的睡眠时长 bug
（多报 156 分钟）就是靠翻 `memory/traces/` 里"那次对话给用户报了 459 分钟、
实际只有 319 分钟"定位的。所以问题是投入不足，不是方向不对。

ChatGPT 5.6Sol审查意见：保留这一成功案例有助于说明投资回报；同时应记录误报/漏报案例，避免只用成功样本证明方案有效。

### 1.4 一次 `/chat` 的真实调用链全景

`fithealth_agent/workflows/chat_workflow.py` 的 `chat()`（第 478-1274 行）一次
最多触发 **7 次外部模型调用**，当前只有 2 次（两个 ReAct agent）被 trace，
且落在两个互不关联的文件里：

ChatGPT 5.6Sol审查意见：调用链全景是后续覆盖率和成本统计的基线；建议把“最多 7 次”按条件分支、重试和上传路径拆开定义。

| # | 触点 | 代码位置 | 客户端 | 超时 | 失败行为 | 现有 trace | 审查意见 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `classify_user_health_statement` | `chat_workflow.py:568` → `health_safety.py:160` | 裸 `requests.post` | 8s | 返回 `None`，静默 | ❌ | ChatGPT 5.6Sol审查意见：健康安全分类应优先记录是否调用、超时/状态和回落原因，避免记录症状原文。 |
| 2 | `route_information`（长期记忆候选） | `chat_workflow.py:326` → `information_router.py:247` | 裸 `requests.post` | 12s | `_no_save()`，静默 | ❌ | ChatGPT 5.6Sol审查意见：记忆候选失败可能改变持久化结果，建议记录候选数量、保存决策和失败类型，并明确不写入敏感内容。 |
| 3 | `route_chat_intent`（**意图路由**） | `chat_workflow.py:676` → `chat_intent_router.py:258` | 裸 `requests.post` | 12s | 空 `ChatIntent()`，静默 | ❌ | ChatGPT 5.6Sol审查意见：应区分真实空意图与调用失败，并记录路由 schema 版本及回落标志。 |
| 4 | `resolve_muscles_for_exercise` | `chat_workflow.py:209` → `muscle_map.py:439` | 裸 `requests.post` | 10s | 回落规则表，静默 | ❌ | ChatGPT 5.6Sol审查意见：规则表回落应可见且可比较，建议记录命中规则版本和是否使用模型结果。 |
| 5 | **ReAct 主循环** | `chat_workflow.py:1048` `agent.run` | `HelloAgentsLLM` | 框架默认 | 抛 `HelloAgentsException` → 503 | ✅ 文件 A | ChatGPT 5.6Sol审查意见：现有唯一可见触点应作为事件字段基准，统一延迟、token、工具和错误口径。 |
| 6 | `validate_plan_goal_alignment` ×最多 3 次 | `chat_workflow.py:838/1128/1182` → `plan_goal_validator.py:120` | 裸 `requests.post` | 10s | 回落本地 `_local_alignment`，静默 | ❌ | ChatGPT 5.6Sol审查意见：最多三次调用应记录 attempt 序号和最终采用的校验来源，防止重复计费或误判覆盖率。 |
| 7 | **自动修正 ReAct 循环** | `chat_workflow.py:1158` `correction_agent.run` | `HelloAgentsLLM` | 框架默认 | 捕获后置空，降级为校验失败 | ✅ 文件 B（与 A 无关联） | ChatGPT 5.6Sol审查意见：修正循环必须与原始计划校验共享 turn，并记录前后差异摘要，避免把修正失败误读成首次生成失败。 |

上传路径（`workflows/upload_workflow.py`）另有 `analyze_food_image`
（`upload_workflow.py:52` → `food_analysis.py:144`，60s 超时）与
`route_information`（`upload_workflow.py:92`），同样零 trace。

ChatGPT 5.6Sol审查意见：上传链路应纳入同一事件模型，尤其要注意图片内容、OCR 文本和食物健康信息的脱敏与大小限制。

---

## 第二部分：问题清单

分级标准：**P0** = 有数据安全／资源正确性风险，或零成本可修；**P1** = 直接决定
trace 能不能用来排障；**P2** = 效率与体验。

ChatGPT 5.6Sol审查意见：分级原则直观；建议补充 P0/P1/P2 的升级/降级规则和责任人，避免不同审查者对优先级理解不一致。

| ID | 级别 | 问题 | 证据 | 审查意见 |
| --- | --- | --- | --- | --- |
| TRACE-01 | P1 | **覆盖面只有 1/7**。6 个裸 `requests.post` 触点全部不可见，且 `except → 返回中性回落` 静默降级。`chat_workflow.py:701-705` 的 BUG-02 注释已经承认"关闭联网模型、缺 key、超时、网络抖动"会让 `route_chat_intent` 返回全 False 的空意图——但现场**没有任何信号**能区分"用户没这个意图"和"路由挂了" | §1.4 表格 | ChatGPT 5.6Sol审查意见：问题定义准确，建议统一 `model_call` 事件的失败枚举（网络、HTTP、解析、禁用、缺 key），并明确重试次数与采样策略，否则覆盖率提高后仍可能难以比较。 |
| TRACE-02 | P1 | **无 turn 关联**。`session_id` 按 `Agent()` 构造生成，一次对话 1~2 个文件（主 agent + 自动修正 agent）彼此无关联，也无法与 HTTP 请求、与响应体的 `source` 字段对上 | `trace_logger.py:73-81` | ChatGPT 5.6Sol审查意见：引入 `turn_id` 是正确方向；还应定义并发请求、重入、后台任务中的关联边界，并避免把可猜测的用户标识直接放入 ID。 |
| TRACE-03 | P1 | **确定性决策全不可见**。`chat()` 有 ~20 个提前返回分支，每个都带一个 `source` 标签（`health_risk_block` / `plan_context_safety_block` / `plan_context_rest` / `plan_context_clarification` / `soreness_clarification` / `local_soreness_feedback` / `external_models_disabled` / `onboarding` / `local_plan_save` / …），全部只回给前端、不落盘。`plan_context.decision`、`health_risk.level`、`plan_validation.violations`、`plan_auto_correction` 同理。用户问"为什么不给我生成计划"时，trace 里什么都没有 | `chat_workflow.py:500-538` 及各 `return _chat_response(...)` | ChatGPT 5.6Sol审查意见：优先级合理，但需限制结构化字段的枚举和值域，避免把自由文本健康信息通过 `reason`、`violations` 等字段重新写入。 |
| TRACE-04 | P1 | **Token/成本不可用**。`cost` 硬编码 `0.0`；只有 `total_tokens`，无 prompt/completion 拆分；另外 6 个触点贡献 0。无法回答"这一轮花了多少" | `react_agent.py:208` | ChatGPT 5.6Sol审查意见：成本估算应区分“缺失、估算、供应商返回”三种状态，并明确价格表版本、币种及缓存/重试 token 的计费口径。 |
| TRACE-05 | P1 | **不可复现**。system prompt 从不记录（`_build_messages` 里有，trace 里没有）；工具 schema、模型 id、`base_url` 均缺失。拿到一份 trace 无法重放那次请求 | `react_agent.py:389-406` vs `:159` | ChatGPT 5.6Sol审查意见：可复现设计应同时记录代码版本、配置版本、工具 schema 哈希和随机性参数；`base_url` 建议只记录主机或哈希，避免泄露内部拓扑。 |
| TRACE-06 | **P0** | **PHI 明文落盘且不受数据治理管辖**。trace 里有完整用户消息、档案摘要、记忆事实、健康数值。`trace_sanitize` 只脱敏 `sk-*` / `Bearer *` / `/Users/x`（`trace_logger.py:143-151`），对健康数据无效。`memory/traces/` **不在** `_reset_steps()` 的 10 个步骤里，也不进 `LocalBackupService`——`/data/reset` 删完六个 store，trace 里的健康细节原样保留。同一份 `.gitignore` 给 `data/` 写了"may contain personal health information"，却漏了 `memory/` | `maintenance_ops.py:73-101`；`.gitignore:20-34` | ChatGPT 5.6Sol审查意见：这是阻断发布的隐私问题。除删除闭环外，还应补访问控制、文件权限、加密/密钥管理、日志留痕和恢复失败后的可验证清理；`full` 模式最好增加显式确认或仅允许开发环境。 |
| TRACE-07 | **P0** | **文件句柄泄漏 + 未闭合 HTML**。`TraceLogger.__init__` 立即 `open()` 两个文件（`trace_logger.py:62/68`），`finalize()` 只在 `_run_impl` 的返回路径调用。因此 `chat_workflow.py:1041` 建好 agent 后、`:1045` 的 `build_agent_input` 抛 `ContextInputError` 时，agent 从未 run，**两个句柄泄漏、HTML 无尾部无统计**；循环内任何异常经 `run()` 重新抛出后同样不 finalize。已实测复现（构造不运行即生成两个文件） | `trace_logger.py:62-71`, `162-182`；`react_agent.py:104-134` | ChatGPT 5.6Sol审查意见：证据充分；验收不应只检查“无句柄”，还要在异常、取消、进程退出和并发场景验证文件完整性与可回收性。 |
| TRACE-08 | **P0** | **HTML 未转义**。`_write_html_event` 直接把 `payload_json` 插进 `<pre>{...}</pre>`，`_write_html_footer` 对错误信息同样不转义。trace 记录的是**原始用户输入**，一条含 `</pre><script>…` 的消息会在审计者打开 HTML 时执行。虽是本地文件、危害有限，但修复只需 `html.escape` | `trace_logger.py:418-433`, `:452` | ChatGPT 5.6Sol审查意见：应将“输出编码”作为所有查看器的统一约束，并加入属性上下文、URL上下文等测试；更稳妥的默认方案是只输出 JSONL，HTML 使用可信模板渲染。 |
| TRACE-09 | P2 | **无保留策略、无索引**。200 个文件、无轮转、无上限、无 `index`。"找昨天那次计划校验失败的对话"只能 grep 100 个 JSONL | `ls memory/traces \| wc -l` = 200 | ChatGPT 5.6Sol审查意见：保留策略应先明确数据生命周期和磁盘预算，再决定按回合、天数、字节数的优先级；索引更新需考虑崩溃一致性和并发写入。 |
| TRACE-10 | **P0** | **意外注册 4 个无关内置工具**。`Config` 默认 `skills_enabled / subagent_enabled / todowrite_enabled / devlog_enabled` 全为 `True`，`Agent.__init__` 于是往 registry 注册 `Skill` / `Task` / `TodoWrite` / `DevLog`。本项目 12 个工具 + `Thought` + `Finish` + 这 4 个 = **每次请求向模型发 18 个 schema**。`skills/` 目录是空的，全仓库没有任何一处引用这 4 个工具。除浪费上下文外，模型有可能真去调它们 | `core/agent.py:99-131`；实测 `SCHEMA NAMES` 输出 | ChatGPT 5.6Sol审查意见：关闭未使用工具合理，但应以实际运行时 schema 快照和关键任务回归测试确认没有隐含依赖，不能仅凭全仓库搜索下结论。 |
| TRACE-11 | P2 | **stdout 污染**。ReAct 每步 `print()` 表情符号。`agent.py:74/94` 只在**注册**时 `redirect_stdout`，`agent.run`（`chat_workflow.py:1048`）没有，所以每步输出直接进服务端控制台，并发请求下互相穿插 | `react_agent.py:164/168/288/335/356` | ChatGPT 5.6Sol审查意见：建议改为结构化 logger 并携带 `turn_id`，同时确认生产日志级别、采样和敏感字段过滤，避免从 stdout 问题转成日志泄露问题。 |
| TRACE-12 | P2 | **相对路径**。`trace_dir="memory/traces"` 按 CWD 解析。Docker 只挂 `data/`（`docker-compose.yml`），trace 落在容器内、重建即失；测试若漏 stub `create_fithealth_agent` 会往仓库里写文件（当前 84 个测试文件都 stub 了，属于运气而非约束） | `config.py:43`；`tests/__init__.py` 只隔离 `FITHEALTH_DATA_DIR` | ChatGPT 5.6Sol审查意见：路径修复应覆盖本地、Docker、Windows 服务和测试隔离，并在启动时校验目录可写性及权限；不要只依赖环境变量约定。 |

附带发现（同源，一并处理）：`memory/sessions/session-error.json`（9 KB，含
对话历史）来自 `react_agent.py:119` 的崩溃保存路径；`memory/devlogs/` 未被
`.gitignore` 覆盖。

ChatGPT 5.6Sol审查意见：附带发现应提升为独立清理验收项，并检查同类崩溃转储的命名变体；只删除已知文件名可能遗漏后续 PHI。

---

## 第三部分：目标设计

### 3.1 设计原则

ChatGPT 5.6Sol审查意见：设计原则整体方向正确，尤其是按用户回合关联、默认脱敏和不影响主流程三点；建议后续把每条原则转成可自动验证的契约，避免只停留在文字约束。

1. **不 fork hello-agents**。`vendor/` 只放行经锁文件校验的 wheel
   （`.gitignore:36-39`），改框架等于放弃这条约束。改为**在外层包一圈**，并把
   框架事件通过一个同接口的 sink 引流进来。

ChatGPT 5.6Sol审查意见：边界选择稳妥，能降低升级维护成本；建议补充框架版本升级时的兼容性契约测试，以及 sink 接口变更的监控。
2. **trace 以「一次用户回合」为单位，不以「一个 Agent 实例」为单位**。一个
   `turn_id` 串起 HTTP 请求、7 个模型触点、所有确定性闸门、两个 ReAct 循环。

ChatGPT 5.6Sol审查意见：回合粒度符合排障需求；需明确流式响应、超时取消和后台异步任务是否仍属于同一回合。
3. **确定性闸门与模型调用同等重要**。本项目的行为主要由闸门决定，trace 若只
   记模型就等于只记了少数派。

ChatGPT 5.6Sol审查意见：这是方案的核心价值。建议为每个 gate 固定 schema、版本和 outcome 枚举，避免后续字段漂移导致查询失效。
4. **默认可在生产开启**：默认脱敏到「结构 + 哈希 + 长度」，不含原文；原文需
   显式 `full` 模式。这是 trace 能长期开着的前提，也是 TRACE-06 的正解。

ChatGPT 5.6Sol审查意见：默认 meta 合理，但哈希仍可能被低熵健康短语反查；对短文本建议使用带密钥的 HMAC，并限制 full 模式的权限与时长。
5. **trace 绝不影响主流程**。所有记录点包在 `try/except Exception` 里，失败只
   降级为一条 `logger.warning`；trace 关闭时开销必须是一次 `ContextVar.get()`。

ChatGPT 5.6Sol审查意见：目标清晰；建议定义告警限流和降级计数，否则 trace 故障可能静默累积而无人发现。
6. **写入走既有的原子写通道**（`fithealth_agent/atomic_json.py`），不新开一套
   落盘语义。

ChatGPT 5.6Sol审查意见：复用原子写通道可减少一致性风险，但需确认大文件、跨盘移动、Windows 文件锁和 fsync 语义满足 trace 的耐久性要求。

### 3.2 关键技术决定

ChatGPT 5.6Sol审查意见：技术决策具有较强可实施性，但涉及线程上下文、原子落盘、脱敏和离线渲染等跨模块行为，建议在实施前先定义失败语义、兼容性边界和观测指标。

**决定 A：用 `contextvars.ContextVar` 传递 turn 上下文，不加函数参数。**

ChatGPT 5.6Sol审查意见：避免污染业务函数签名的取舍合理；应明确上下文缺失时的安全默认行为，并防止跨请求复用同一可变对象。

`chat()` 里 5 处 `run_in_threadpool` 会把执行切到工作线程
（`chat_workflow.py:568/637/675/837/1048`）。已实测 starlette 的
`run_in_threadpool` **会传播 contextvars**：

```
in threadpool: ('turn-abc123', 'AnyIO worker thread')
in loop: turn-abc123
```

因此 ContextVar 方案可行，且避免了给 7 个调用点逐个加 `trace=` 参数（那会污染
`deps.*` 的打桩契约，`test_deps_stub_reachability.py` 会连带失效）。

ChatGPT 5.6Sol审查意见：实测证据有帮助，但版本、ASGI 运行器和线程池配置应写入测试前提，不能把单一环境结果当作永久保证。

**约束**：工作线程拿到的是 Context 的**副本**，线程内 `var.set()` 不会回传主
线程。所以 `TurnTrace` 必须是**可变对象、只在异步层 set 一次**，工作线程只往
里 append。append 加 `threading.Lock`——同步 ReAct 是串行的，但 `_execute_tools_async`
是并行的，锁的成本可以忽略。

ChatGPT 5.6Sol审查意见：可变 trace 加锁能处理并发 append；建议验证事件顺序只依赖 seq 而不依赖线程完成顺序，并限制 buffer 增长。

**决定 B：缓冲 + `finally` 落盘，不逐事件 flush。**

ChatGPT 5.6Sol审查意见：批量写入能降低延迟，但进程崩溃会丢失尾部事件；应记录 incomplete 标志，并定义内存上限和溢出策略。

框架的逐事件 `flush()` 两个文件是 TRACE-07 的根源。改为在内存 buffer，`turn`
结束时（含异常路径）一次原子写。想看实时进度时用 `FITHEALTH_TRACE_STREAM=1`
切回追加模式。

ChatGPT 5.6Sol审查意见：stream 模式适合挂死排查，但应默认关闭、限速并防止写入原文或无限增长。

**决定 C：只出 JSONL，HTML 由离线脚本渲染。**

ChatGPT 5.6Sol审查意见：单一事实源能减少一致性问题；离线工具需要版本兼容、只读保护和恶意字段回归测试。

在线生成 HTML 是 TRACE-08 的根源，而且把渲染逻辑锁死在写入路径上。JSONL 是唯一
真相，`scripts/trace_report.py` 负责渲染（用 `html.escape`）。

ChatGPT 5.6Sol审查意见：`html.escape` 是必要条件但不是完整策略，还应避免危险 URL、属性和脚本上下文，并设置安全响应头或 CSP（若以后提供页面）。

**决定 D：脱敏是字段级白名单，不是正则黑名单。**

ChatGPT 5.6Sol审查意见：白名单优于黑名单；建议未知字段默认拒绝落盘，并为新增字段设立隐私评审和自动测试。

正则脱敏对健康数据无效（"膝盖疼"没有可匹配的模式）。改为：结构字段（决策名、
闸门结果、工具名、token 数、状态码）原样保留；自由文本字段按 detail 级别处理。

ChatGPT 5.6Sol审查意见：结构字段也可能间接暴露健康信息（例如具体部位或稀有规则名），应做敏感枚举审查和低熵哈希保护。

### 3.3 数据模型

ChatGPT 5.6Sol审查意见：事件模型字段较完整；建议增加 schema_version、producer_version 和 request_id，并明确 seq 在并发工具调用下的排序保证。

落盘布局（按天分目录，避免单目录膨胀）：

```
<trace_dir>/<YYYY-MM-DD>/turn-<turn_id>.jsonl
<trace_dir>/index.jsonl                        每回合一行摘要，供列表与检索
```

ChatGPT 5.6Sol审查意见：按天分目录有利于轮转，但索引与回合文件必须采用同一原子提交/恢复策略，并明确跨时区日期边界。

`turn_id` 格式：`t-<YYYYMMDD>-<HHMMSS>-<6位hex>`。

ChatGPT 5.6Sol审查意见：时间加随机后缀可读性好；建议使用密码学安全随机源并记录时区，避免多进程碰撞和系统时钟回拨。

每行一个事件：

```json
{
  "ts": "2026-09-04T10:19:29.412+08:00",
  "turn_id": "t-20260904-101929-74ca3f",
  "seq": 12,
  "kind": "model_call",
  "span": "chat_intent_router",
  "step": null,
  "dur_ms": 812,
  "payload": {}
}
```

ChatGPT 5.6Sol审查意见：示例字段足以支持基本检索；建议增加 `schema_version`、`request_id`、`producer_version` 和事件完整性/截断标志。

`kind` 字典（`span` 为同类事件的子标识）：

| kind | span 取值 | payload 关键字段 | 审查意见 |
| --- | --- | --- | --- |
| `turn_start` | 路由路径 | `source`, `msg_len`, `msg_sha256_12`, `history_len`, `external_models_enabled` | ChatGPT 5.6Sol审查意见：起始摘要应避免用户标识和原文；建议加入 request_id、配置版本与 detail_level。 |
| `gate` | `health_risk` / `soreness` / `plan_context` / `plan_validation` / `auto_correction` / `memory_candidate` / `profile_gate` | `outcome`, `reason`, 结构化输入摘要 | ChatGPT 5.6Sol审查意见：固定 span 枚举有利检索；需定义未知 gate 的兼容处理及敏感 reason 的白名单。 |
| `model_call` | 7 个触点名 | `model`, `endpoint_host`, `http_status`, `ok`, `fallback_reason`, `prompt_tokens`, `completion_tokens`, `total_tokens` | ChatGPT 5.6Sol审查意见：建议增加 call_id、attempt、retry_count 和 usage_source，区分跳过、失败与真实空结果。 |
| `react_step` | `agent` / `correction_agent` | `step`, `tool_calls`, `total_tokens` | ChatGPT 5.6Sol审查意见：应记录 parent_span、停止原因和步数上限命中情况，保证多 agent 顺序可还原。 |
| `tool_call` / `tool_result` | 工具名 | `args_keys`/`args`(full 模式), `status`, `dur_ms`, `result_bytes`, `truncated` | ChatGPT 5.6Sol审查意见：参数和结果都可能含 PHI，应默认白名单化并设置字节、深度和集合长度上限。 |
| `store_write` | store 名 | `entry_id`, `namespace`, `key`, `user_confirmed` | ChatGPT 5.6Sol审查意见：持久化事件需记录成功/失败和幂等键，但 key、namespace 也应经过敏感信息审查。 |
| `turn_end` | 路由路径 | `source`, `status_code`, `dur_ms`, `total_tokens`, `cost_estimate`, `artifact_type`, `error_type` | ChatGPT 5.6Sol审查意见：结束事件应包含完整性、取消和落盘状态，并可通过 turn_id 去重。 |

### 3.4 环境变量

ChatGPT 5.6Sol审查意见：默认关闭和按天分目录合理；环境变量涉及隐私、容量和成本，建议启动时做合法性校验、敏感配置告警及配置快照记录。

| 变量 | 默认 | 说明 | 审查意见 |
| --- | --- | --- | --- |
| `FITHEALTH_TRACE` | `off` | `off` / `on`。默认关，避免任何现有部署行为突变 | ChatGPT 5.6Sol审查意见：默认关闭能降低迁移风险；建议启动时记录有效配置但不记录密钥，并为非法值采用安全关闭。 |
| `FITHEALTH_TRACE_DETAIL` | `meta` | `meta` = 结构 + 哈希 + 长度；`full` = 含原文。设为 `full` 时启动打一条 `logger.warning` | ChatGPT 5.6Sol审查意见：`full` 属高风险开关，建议仅开发环境或经显式确认启用，并设置自动过期。 |
| `FITHEALTH_TRACE_DIR` | `<data_dir>/traces` | 默认挂到 `settings.data_dir()` 下，随 Docker 卷走（修 TRACE-12） | ChatGPT 5.6Sol审查意见：应校验目录归属、权限、剩余空间和符号链接，避免写到意外位置。 |
| `FITHEALTH_TRACE_STREAM` | `0` | `1` = 逐事件追加，用于排查挂死 | ChatGPT 5.6Sol审查意见：流式模式需限速、限量和崩溃恢复策略，避免性能或磁盘风险。 |
| `FITHEALTH_TRACE_MAX_TURNS` | `500` | 保留回合数上限 | ChatGPT 5.6Sol审查意见：回合上限应与隐私保留期限和实际磁盘预算联动，而非只给固定数字。 |
| `FITHEALTH_TRACE_MAX_DAYS` | `14` | 保留天数上限 | ChatGPT 5.6Sol审查意见：日期计算应明确时区、夏令时和时钟回拨行为。 |
| `FITHEALTH_TRACE_MAX_BYTES` | `268435456` | 目录总字节上限（256 MiB） | ChatGPT 5.6Sol审查意见：需定义达到上限时先删旧数据还是拒绝新写入，并防止单回合绕过限制。 |
| `FITHEALTH_TRACE_PRICE_JSON` | 空 | `{"deepseek-chat": {"in": 0.0000002, "out": 0.0000008}}`，用于估算成本 | ChatGPT 5.6Sol审查意见：价格配置应校验非负数、币种和版本；格式错误应安全降级为“成本未知”。 |

三条保留上限**同时**生效，任一超限即从最旧开始删。

ChatGPT 5.6Sol审查意见：组合规则清楚，但需定义删除顺序的稳定性、索引一致性和并发写入下的锁策略，并保留删除计数供审计。

---

## 第四部分：分阶段实施计划

每个阶段都是可独立提交、可独立回滚的最小单元。

ChatGPT 5.6Sol审查意见：阶段化降低了变更风险；建议为每阶段定义前置条件、产出物、回滚触发器和依赖关系，避免阶段间隐性耦合。

### 阶段 0 — 止损与归零（零新代码，P0）

ChatGPT 5.6Sol审查意见：先关闭框架默认副作用的顺序正确，且单独提交便于回滚。删除历史含 PHI 文件属于不可逆操作，建议先做受控备份、记录清单并取得明确审批；同时把“无新增文件”验证扩展到所有运行时异常路径。

**目标**：不写任何新逻辑，仅靠显式配置一次性消除 TRACE-07 / 08 / 10，并停止
`memory/sessions|todos|devlogs` 的意外写入。这一步**必须单独提交**，因为它同时
改变了发给模型的工具清单，需要一次干净的行为对照。

ChatGPT 5.6Sol审查意见：止损目标明确且变更面小；建议把“归零”定义为可测量的目录、句柄和工具 schema 不变量，并在提交前保存基线快照。

**改动 1**：`fithealth_agent/agent.py` — 显式传 `Config`。

ChatGPT 5.6Sol审查意见：配置显式化能消除默认行为漂移；建议集中定义关键开关，并用启动快照测试确认部署实际生效。

```python
from hello_agents.core.config import Config

#: 框架默认把 6 个可选子系统全打开（trace / skills / subagent / todowrite /
#: devlog / session）。本项目一个都不用：`skills/` 是空目录，全仓库没有任何
#: 一处引用 Skill / Task / TodoWrite / DevLog 这 4 个工具，但它们会被自动注册
#: 进 registry，使每次请求向模型多发 4 个 schema。trace 由本项目自己的
#: observability 层接管（阶段 5 会把 sink 装回 agent.trace_logger），框架的
#: 文件式 TraceLogger 必须关掉：它在构造时就开两个句柄、只在特定返回路径
#: finalize，而 build_agent_input 抛 ContextInputError 时 agent 从未 run。
AGENT_CONFIG = Config(
    trace_enabled=False,
    skills_enabled=False,
    subagent_enabled=False,
    todowrite_enabled=False,
    devlog_enabled=False,
    session_enabled=False,
)
```

并在 `agent.py:103` 的构造处加上 `config=AGENT_CONFIG`。

ChatGPT 5.6Sol审查意见：配置接入点正确；除开关外应显式确认数据目录、截断参数和日志级别，避免只修复 trace 而保留其他隐式副作用。

**改动 2**：`.gitignore` 补 `memory/devlogs/`（其余三个同级目录已忽略）。

ChatGPT 5.6Sol审查意见：忽略规则是必要的仓库防线，但不能替代运行时清理、容器挂载和备份包检查。

**改动 3**：删掉 `memory/sessions/session-error.json`（含历史 PHI 的崩溃转储）。

ChatGPT 5.6Sol审查意见：删除不可逆，建议先登记文件哈希和删除结果，并按项目合规要求执行受控销毁。

**验证**：新增 `tests/test_agent_tool_schema_contract.py`

ChatGPT 5.6Sol审查意见：除名称集合外，还应断言参数 schema、必填字段和工具清单哈希，防止名称不变而契约漂移。

```python
def test_agent_exposes_exactly_the_project_tools():
    """工具清单是发给模型的契约。框架默认会塞 Skill/Task/TodoWrite/DevLog，
    多出来的 schema 既浪费上下文，也让模型有机会调用本项目没有实现的能力。"""
    agent = create_fithealth_agent()
    names = {s["function"]["name"] for s in agent._build_tool_schemas()}
    assert names == {
        "Thought", "Finish",
        "save_daily_record", "query_daily_records",
        "query_daily_health", "query_health_range", "query_sleep",
        "query_heart_rate_window",
        "update_set", "merge_sets", "delete_set",
        "undo_last_edit", "restore_parsed_source",
        "search_youtube_video",
    }

def test_framework_trace_logger_is_disabled():
    assert create_fithealth_agent().trace_logger is None
```

ChatGPT 5.6Sol审查意见：两项测试覆盖了主要契约；还应断言未创建 trace、session、todo 和 devlog 文件，并在异常构造路径执行同样检查。

> 工具名以 `fithealth_agent/tools.py`、`fit_tools.py`、`health_tools.py`、
> `youtube_tool.py` 各类的 `name=` 实参为准，落地时逐个核对再填。

ChatGPT 5.6Sol审查意见：以源码真实名称为准是必要的；建议由运行时 schema 快照自动生成断言，减少手工清单过期风险。

**回滚**：还原 `agent.py` 一处 `config=` 实参即可。

ChatGPT 5.6Sol审查意见：代码回滚简单，但历史 PHI 删除无法靠代码回滚恢复，数据清理应单独审计。

**风险**：`Config(...)` 会同时改掉截断参数吗？不会——上表 6 个开关之外的字段
（`tool_output_max_lines` 等）仍取默认值，与今天一致。

ChatGPT 5.6Sol审查意见：该判断应由配置快照测试验证；框架升级可能改变默认值，关键截断参数最好显式锁定。

---

#### 阶段 0 实施记录（2026-09-04，已完成）

按上述审查意见落地，与原方案有五处差异，均为审查意见直接要求：

1. **锁定范围从 6 个开关扩到 13 个字段**（审查："关键截断参数最好显式锁定"）。
   新增锁定 4 个截断参数、`enable_smart_compression`（默认翻转会凭空多一次 LLM
   调用）、`debug`、`log_level`。这 7 个的值与框架当前默认一致，**不改变行为**。
2. **契约改用运行时快照**（审查："由运行时 schema 快照自动生成断言"）。仿
   `tests/route_snapshot.py`，新增 `tests/agent_snapshot.py` +
   `tests/baseline/agent_snapshot.json`：含 14 个工具的完整 schema、schema
   digest、**整份 51 字段配置 dump**、`framework_version`。再生成用
   `python -m tests.agent_snapshot --write`。
3. **验证覆盖四条执行路径**（审查："在异常/取消路径重复验证"）。正常构造、
   构造后从未 run、崩溃 run（`save_session("session-error")` 分支）、中断 run
   （`session-interrupted` 分支），四条都断言零落盘。
4. **历史清理与代码提交解耦**（审查："先登记文件哈希并取得明确审批"）。
   `scripts/purge_legacy_traces.py` 默认只盘点，销毁需 `--confirm 删除全部轨迹`，
   目录白名单硬编码且拒绝解析到仓库外的软链。实际销毁 203 个文件 / 4,392,639
   字节（traces 200、sessions 1、todos 2），清单留档在 `tool-output/`。
5. **顺带修掉 `tool_output_dir`**（审查："应显式确认数据目录"）。`ObservationTruncator`
   在超限时把**未截断的工具输出原文**（含训练与健康记录）写进 CWD 相对的
   `tool-output/`，且不受 `/data/reset` 管辖——与 TRACE-06/12 同型。新增
   `fithealth_agent/tool_output.py` 把它锚到 `settings.data_dir()` 下，并给
   `_reset_steps()` 加第 11 步 `tool_output_removed`。

`.dockerignore` 与 `.gitignore` 同步补齐（审查："不能替代容器挂载检查"）：前者
漏了 `memory/devlogs/`，后者还要额外加 `data/tool-output/`——`data/` 不是整目录
忽略的，只列了几个 glob。

全量测试 912 passed；唯一失败 `test_runtime_environment.py::test_container_and_docs_use_the_declared_default`
经 `git stash` 在纯 HEAD 上复现，是上一提交（`68e9443` README.md更新）的既有问题。

---

### 阶段 1 — trace 骨架（不接任何调用点）

ChatGPT 5.6Sol审查意见：先做无调用点骨架有利于控制变更面。建议为公共 API 增加类型检查、事件 schema 版本和最大 payload 限制，避免基础设施被异常输入拖垮。

**目标**：建立可 import、默认关闭、无副作用的基础设施。这一阶段结束时，全仓库
行为与阶段 0 完全相同。

ChatGPT 5.6Sol审查意见：阶段边界清晰；建议把“无副作用”具体化为不建目录、不写文件、不改变全局配置，并用进程级测试确认。

**新增文件**：

```
fithealth_agent/observability/__init__.py
fithealth_agent/observability/trace.py         TurnTrace + ContextVar + 记录 API
fithealth_agent/observability/redact.py        detail 级别与字段处理
fithealth_agent/observability/sink.py          落盘 + 保留策略（阶段 6 补全）
```

ChatGPT 5.6Sol审查意见：模块拆分职责合理；应明确 `sink.py` 骨架在阶段 1 只提供接口还是已经包含写入能力，避免阶段依赖含糊。

`trace.py` 的公开接口（骨架）：

```python
_current: ContextVar["TurnTrace | None"] = ContextVar("fithealth_turn_trace", default=None)

@dataclass
class TurnTrace:
    turn_id: str
    route: str
    started_at: datetime
    events: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, kind, span="", *, step=None, dur_ms=None, **payload) -> None: ...

def trace_event(kind, span="", **payload) -> None:
    """全局记录入口。trace 关闭或不在 turn 内时是一次 ContextVar.get()。

    绝不抛异常：可观测性故障不能变成业务故障。
    """
    trace = _current.get()
    if trace is None:
        return
    try:
        trace.add(kind, span, **payload)
    except Exception:                      # noqa: BLE001
        logger.warning("trace 事件记录失败：kind=%s span=%s", kind, span, exc_info=True)

@contextmanager
def start_turn(route: str, **meta): ...     # 只在异步层调用；finally 落盘

@contextmanager
def span(kind: str, name: str, **payload):  # 自动计时，异常时记 error_type
    ...
```

ChatGPT 5.6Sol审查意见：公开 API 足够小，便于调用点接入；建议补充返回类型、异常保证、最大字段大小和嵌套 span 规则。

**验证**：`tests/test_trace_infrastructure.py`

ChatGPT 5.6Sol审查意见：现有验证覆盖核心失败路径；建议补充并发追加、磁盘写满、权限拒绝、取消和重复 `start_turn` 测试。

- trace 关闭时 `trace_event(...)` 是 no-op，且不创建任何目录；

ChatGPT 5.6Sol审查意见：这是性能与副作用的核心不变量；测试应同时清空预先存在的目录并检查没有新文件或日志噪声。
- `start_turn` 异常退出时仍落盘且带 `turn_end.error_type`；

ChatGPT 5.6Sol审查意见：异常结束事件应覆盖业务异常、取消和系统退出，并确保错误类型经过白名单化而非写入完整堆栈。
- `trace.add` 内部抛异常时 `trace_event` 不向外传播（注入一个坏 payload）；

ChatGPT 5.6Sol审查意见：隔离观测故障很重要；建议同时验证告警限流、递归失败不会再次触发 trace，以及业务返回值不变。
- **contextvar 传播**：`run_in_threadpool` 中 `trace_event` 写入的事件，回到
  异步层后能在同一个 `TurnTrace` 里读到（这条断言锁住决定 A）；

ChatGPT 5.6Sol审查意见：除线程池外还应测试并发任务、嵌套 turn 和取消清理，确认不会串写其他请求。
- `meta` 级别下，落盘内容不含用户原文（用一句可识别的哨兵串断言不出现）。

ChatGPT 5.6Sol审查意见：哨兵串测试有效但不充分；应采用字段级反向断言和高风险样本集，覆盖 Unicode、嵌套对象及异常消息。

**回滚**：删目录。没有任何调用点依赖它。

ChatGPT 5.6Sol审查意见：删除目录可回滚代码，但仍应检查测试、配置和导入引用，确保没有残留契约。

---

#### 阶段 1 实施记录（2026-09-04，已完成）

**模块边界**（审查："先定义公共 API 和模块边界，再允许辅助实现扩张"）。比原方案
多拆出一个 `schema.py`，依赖单向：`trace` → `sink` → `config`，`redact` → `schema`。

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `config.py` | 环境变量解析与校验 | **不碰文件系统**（所以 `load_settings()` 可以每回合调用而不产生 I/O） |
| `schema.py` | 事件契约：kind/span 枚举、字段类别、上限 | 不做裁剪 |
| `redact.py` | 按契约裁剪 payload、HMAC 摘要 | 不知道回合的存在 |
| `sink.py` | 目录校验、原子写、index 追加 | **不做保留策略**（阶段 6） |
| `trace.py` | `TurnTrace`、ContextVar、公开记录 API | 不直接碰文件 |

**`sink.py` 的阶段边界**（审查明确要求写清）：阶段 1 **已含完整写入能力**。理由是
本阶段验收里有"`start_turn` 异常退出时仍落盘"这一条，只给接口就无从验证。

按审查意见做的九处补强：

1. **事件 schema 版本与产出方版本**。每条事件带 `v`（`SCHEMA_VERSION`），
   `turn_start` 带 `producer`（`fithealth/src hello-agents/1.0.0`）、`request_id`、
   `detail_level`、`tz`。`producer` 用 `importlib.metadata` 读，**不 import
   hello_agents**——否则会把 LLM 栈拉进来，破坏 `test_package_lazy_import` 的契约。
2. **payload 上限六项**：单回合 2000 条事件、structural 字符串 200 字、text 字段
   4000 字、列表 20 项、嵌套 3 层、单条事件 16 KiB。超限降级但保留
   `_oversize` / `_truncated` / `_count` 标记——降级不能抹掉"这个字段存在过"。
3. **未注册字段默认拒绝落盘**。规则按"名字 vs 值"分开：名字来自我们自己的源码
   （未注册的 span 照记并标 `_unknown_span`，未注册的字段名列进 `_dropped`，
   便于发现漏注册）；值可能来自用户，一律丢弃。
4. **摘要改带密钥的 HMAC**（审查："哈希仍可能被低熵健康短语反查"）。密钥优先取
   `FITHEALTH_SIGNING_KEY`（`deps.py` 的餐盘签名已在用同一把），没配就每进程随机。
   代价：不配密钥时摘要跨重启不可比——默认更安全，需要跨重启比对的人自己配。
5. **`turn_end` 恰好一条**。改用 `set_turn_result(**fields)` 暂存 + `start_turn`
   的 finally 统一写出，而不是让调用方自己 `trace_event("turn_end", ...)`——后者
   在异常路径会漏记、在重试路径会记两条。状态三态：`ok` / `error` /
   `aborted`（`CancelledError`、`KeyboardInterrupt`、`SystemExit`），取消要和真
   失败分得开。`error_type` 只记类名，不记 message、不记堆栈（消息里可能有原文）。
6. **降级限流 + 计数**。每回合最多 3 条 warning，但 `trace_errors` 全量计数并写进
   `turn_end`，另有 `events_dropped` / `fields_dropped`——降级必须可见。
7. **递归护栏**。`_recording` ContextVar：记录过程内部触发的再次记录直接丢弃。
   一个 `__str__` 就炸的对象否则能把栈打穿。
8. **嵌套规则**。嵌套 `start_turn` 是空操作，产出同一个 `TurnTrace`，绝不写出第二
   个文件；`span()` 自带 `sid` 并记 `parent`，栈存在 ContextVar 的不可变元组里。
9. **目录安全四查**（审查："目录归属、权限、剩余空间和符号链接"）：拒绝符号链接、
   能创建、能写、剩余空间 ≥ 64 MiB。结果按目录缓存，不每回合探盘。

**发现的一处边界并钉住**：裸 `threading.Thread` 起的线程拿到的是**空 Context**，
记不进当前回合。`run_in_threadpool` / `asyncio.to_thread` /
`copy_context().run` 三者都复制上下文，本项目现有 5 处线程切换都是第一种，所以
没问题；`test_a_bare_thread_inherits_no_turn` 把这条边界写成用例，供后来加后台
线程的人查。

**与原方案的两处偏离**：(a) `trace_dir()` 放在 `observability/config.py` 而不是改
`settings.py`，与阶段 0 的 `tool_output.py` 同一做法，保持 `settings.py` 是纯路径
解析器；(b) 新增 `atomic_json.atomic_write_text`（JSONL 不是 JSON），刻意重复那 6
行 fsync 纪律而不重构 `atomic_write_json`——为一个诊断功能改动全仓库唯一的耐久写
路径不值当，DATA-06/07 的回归测试就钉在那条路径上。

`test_trace_infrastructure.py` 52 条用例；全量 964 passed，唯一失败仍是
`68e9443` 遗留的 README 断言。收尾校验：不设 `FITHEALTH_TRACE` 时
`load_settings().enabled` 为 False、`start_turn` 产出 None、数据目录前后文件清单
完全相同；生产代码零调用点；`agent_snapshot.json` 无漂移。

---

### 阶段 2 — 接到 HTTP 边界（turn 生命周期）

ChatGPT 5.6Sol审查意见：路由层建立 turn 边界比全局中间件更精确，但示例中的 `with start_turn` 若内部包含异步调用，需确认上下文管理器支持异步异常、取消和响应序列化失败；建议采用 `asynccontextmanager` 或明确同步封装方式。

**目标**：让每个 `/chat`、`/upload/food`、`/logout` 请求都有一个 turn。

ChatGPT 5.6Sol审查意见：路由覆盖目标明确；应定义请求解析失败、健康检查、静态资源和重试请求是否有意排除，并保持文档与实际路由清单同步。

**改动**：`fithealth_agent/routes/chat.py:41-48`

ChatGPT 5.6Sol审查意见：需明确使用同步还是异步上下文管理器，并保证异常响应、取消和响应序列化失败时仍生成完整结束事件。

```python
@router.post("/chat")
async def chat(request: Request) -> JSONResponse:
    try:
        payload = await read_chat_request(request)
    except ContextInputError as exc:
        return context_error_response(exc)
    with start_turn("/chat", source=payload.get("source")):
        result = await run_chat_workflow(payload)
        trace_event("turn_end", "/chat",
                    source=result.body.get("source"),
                    status_code=result.status_code)
    return JSONResponse(result.body, status_code=result.status_code)
```

ChatGPT 5.6Sol审查意见：示例展示了正常路径，但异常发生在 `with` 内外的行为仍需明确；建议统一使用 `try/finally` 记录结束状态，并让响应序列化失败也可追踪。

**为什么放在路由层而不是中间件**：`runtime/middleware.py` 的 `maintenance_guard`
包住了**所有**路径，包括静态首页和 `/health/storage-status` 轮询。在那里开 turn
会给每次轮询都生成一个空 trace 文件。turn 应该只覆盖真正跑 agent 的三条路由。

ChatGPT 5.6Sol审查意见：建议把目标路由列成显式白名单，并为未来新增路由增加接入测试。

**为什么 `turn_end` 在 with 里而不是 `start_turn` 的 finally 里**：`finally`
拿不到 `ChatResult`。让 `start_turn` 的 finally 只负责"没有正常记 `turn_end`
就补一条 `status=aborted`"，正常路径由调用方记完整信息。

ChatGPT 5.6Sol审查意见：必须保证正常路径只写一次 `turn_end`，异常补写统一的 `aborted/error_type`，并验证重复记录的幂等性。

**验证**：扩展 `tests/test_chat_workflow_structure.py` 风格的结构断言——
`routes/chat.py` 源码中 `run_chat_workflow` 调用必须处在 `start_turn` 块内
（AST 断言，与 `tests/source_assertion_guard.py` 的既有做法一致）。

ChatGPT 5.6Sol审查意见：AST 断言能防止结构回退，但需配合成功、异常和取消请求的运行时集成测试。

#### 阶段 2 实施记录（2026-09-04，已完成）

**接入的三条路由**（真实路径，计划里写的 `/upload/food` 实际是 `/analyze_food`）：

| 路由 | 文件 | turn_start 带的信息 | turn_end 带的信息 |
| --- | --- | --- | --- |
| `/chat` | `routes/chat.py` | `source`、`message`(T)、`history_len` | `source`、`status_code`、`artifact_type` |
| `/analyze_food` | `routes/uploads.py` | `message`(T，即用户手写的 `context`) | `status_code` |
| `/logout` | `routes/logout.py` | `history_len` | `source`（业务 status）、`status_code` |

清单落在 `observability/http.py` 的 `TRACED_ROUTES`（纯数据 + 排除表），
`tests/test_trace_http_boundary.py` 按它逐条验证——白名单加了路由但没补用例时先红。

**按审查意见落实的 5 点**：

1. **同步 `with` 够用，不需要 `asynccontextmanager`**。sync 上下文管理器的
   `__exit__` 会收到 `with` 体内**任何**传播出来的异常，包括 `await` 点抛出的和
   `CancelledError`。已用真实请求验证三态：成功 → `ok`，`RuntimeError` → `error`，
   `asyncio.CancelledError` → `aborted`。
2. **响应序列化失败纳入回合**。`JSONResponse` 在 `__init__` 里就把 body 渲染成
   字节，所以它必须构造在 `with` 之内。计划草图把它放在 `with` 外面，那样一个
   不可序列化的 body 会静默逃出回合——已改到里面，并有专门用例钉住。
3. **`turn_end` 恰好一条**。计划草图里的 `trace_event("turn_end", ...)` 已被阶段 1
   的 `set_turn_result` 取代（暂存 + 由 `finally` 统一写出）。同时给
   `set_turn_result` 加了 `RESERVED_TURN_END_FIELDS` 保护：`/logout` 的业务
   `status`（saved/not_saved）会被**拒绝并告警**，改记进 `source`——否则生命周期
   三态被业务值覆盖，"这次到底是不是崩了"就再也读不出来。
4. **排除项写成表并逐条验证**：请求体解析失败（413/415/空/坏 JSON/缺 message）、
   静态首页与存储状态轮询、维护期 503、纯本地 CRUD 与 `/upload_*`。413 的理由值得
   记下来：它发生在**读 body 期间**，在那之前开 turn 等于让客户端用超大请求随意
   造 trace 文件。
5. **AST 断言 + 运行时集成测试都有**。AST 那条改用 `assertEqual` 比对
   "`with start_turn` 块内的调用名集合"，不把节点 unparse 回文本——ARCH-09 允许
   结构断言，禁止源码文本断言。

**与计划的偏差**：`request_id` 不从 HTTP 头读。`/analyze_food` 与 `/logout` 的签名
里没有 `Request`，为一个诊断字段给三条路由加参数会动到冻结的路由契约；本项目是
单用户本地部署，没有反向代理会注入上游 id。所以 `request_id` 由 `start_turn` 随机
生成，只用于把同一回合的事件与 index 行对上。

**验证**：`tests/test_trace_http_boundary.py` 15 条全绿；全量 979 passed。
关闭 trace 时三条路由跑一遍，`traces/` 目录**不存在**、零文件——阶段 1 的"关闭即
零副作用"在接线之后仍然成立。

---

### 阶段 3 — 确定性决策（本项目收益最大的一步）

ChatGPT 5.6Sol审查意见：集中在 `_chat_response` 记录出口能降低漏记概率，但不能替代关键 gate 的语义事件。建议为每个提前返回分支建立覆盖矩阵，明确“输入摘要、决策、原因、最终响应”四类字段。

**目标**：让"为什么给了这个回答"可回溯。

ChatGPT 5.6Sol审查意见：目标具有业务可验证性；建议定义最小可回溯字段集和“无法确定”状态，避免用缺失事件推断原因。

**改动 1**：一处改动覆盖全部 20 个分支。`chat()` 内所有返回都走
`_chat_response()`（`chat_workflow.py:500`），在它的 return 前插一条记录：

ChatGPT 5.6Sol审查意见：集中出口能降低漏记概率，但必须确认写入前已完成字段级脱敏，且 trace 失败不会改变响应结果。

```python
    def _chat_response(reply, *, artifact=None, source=None, status_code=200, ...):
        ...
        # 20 个提前返回分支共用这一个出口。逐分支插 trace 会漏，而且下次加
        # 分支的人不会记得补——这里记一次，新分支自动被覆盖。
        trace_event(
            "gate", "chat_response",
            source=source, status_code=status_code,
            artifact_type=(artifact or {}).get("type"),
            reply_len=len(final_reply),
            soreness_saved=bool(saved_soreness),
            memory_candidates=len(memory_candidates),
            extra_keys=sorted(extra),
        )
        return ChatResult(body=body, status_code=status_code)
```

**改动 2**：`extra_keys` 只给出键名不够——几个闸门的**输入**才是排障关键。在
以下 6 处补显式记录（都是既有变量，不新增计算）：

ChatGPT 5.6Sol审查意见：建议为每个记录点定义稳定字段字典和空值语义，避免不同分支产生同名异类型字段。

| 位置 | 记录内容 | 审查意见 |
| --- | --- | --- |
| `chat_workflow.py:615-624` 风险合并后 | `gate/health_risk`：`level`, `labels`, `painful_regions`, `blocks_plan` | ChatGPT 5.6Sol审查意见：建议使用稳定枚举和数量/哈希摘要，避免 `painful_regions` 直接暴露健康细节。 |
| `:600-608` 酸痛入库后 | `gate/soreness`：`saved_count`, `regions`, `levels`, `asked_regions` | ChatGPT 5.6Sol审查意见：区域与等级仍可能构成 PHI，应按 detail 级别处理并记录保存是否经用户确认。 |
| `:981-1000` plan_context 解析后 | `gate/plan_context`：`decision`, `effective_subject`, `scheduled_subject`, `blocking_reasons`, `clarification_required`, `active_safety_constraints`(计数) | ChatGPT 5.6Sol审查意见：决策字段适合排障；`blocking_reasons` 应采用规则 ID，原文只在受控 full 模式保留。 |
| `:1117-1139` 首次计划校验后 | `gate/plan_validation`：`violations`, `goal_alignment.passed`, `missing_subjects`, `expected_subject` | ChatGPT 5.6Sol审查意见：应记录校验器版本、目标集合摘要和失败数量，避免把违规文本写入日志。 |
| `:1140-1203` 自动修正全过程 | `gate/auto_correction`：`attempted`, `passed`, `first_violations`, `final_violations`, `corrected_is_complete_plan` | ChatGPT 5.6Sol审查意见：建议补 attempt 序号、修正前后摘要和停止原因，且明确修正失败不覆盖原始校验结论。 |
| `:343-379` / `:403-420` / `:463-476` 三个记忆候选 | `store_write/info_store`：`entry_id`, `namespace`, `key`, `user_confirmed=False`, `capture` | ChatGPT 5.6Sol审查意见：持久化事件应有幂等键、成功状态和用户确认来源；`key`、`capture` 默认不写原文。 |

**注意**：`active_safety_constraints` 与 `violations` 的**文本**是健康约束原文，
按 `meta` 级别只记条数与稳定的规则标识；`full` 级别才记原文。这条由
`redact.py` 统一裁决，调用点照常传完整值。

ChatGPT 5.6Sol审查意见：调用方不应先把原文拼进异常或普通日志；建议增加静态检查，限制健康约束文本只能经 observability API 输出。

**验证**：`tests/test_trace_decision_coverage.py`

ChatGPT 5.6Sol审查意见：典型路径覆盖了主要业务结论；还应覆盖所有 `source` 枚举及模型跳过、输入非法和内部异常路径。

- 用现有 `test_health_safety_gate.py` / `test_generated_plan_safety_rules.py` /
  `test_soreness_stage3.py` 的既有 fixture 跑三条典型路径（emergency 拦截、
  安全冲突 409、自动修正通过），断言 trace 里出现预期的 `gate` 序列；

ChatGPT 5.6Sol审查意见：三条典型路径覆盖主流程，但应增加每个提前返回 source 的参数化测试和顺序断言。
- **反向断言**：`chat_workflow.py` 源码中 `return ChatResult(` 只出现在
  `_chat_response` 内部（AST 断言）。这条保证"新加的分支自动被 trace 覆盖"
   这个前提不会被悄悄破坏。

ChatGPT 5.6Sol审查意见：结构断言有助于防止旁路，但应允许明确标记的错误响应出口，并配合运行时覆盖率而非只依赖 AST。

#### 阶段 3 实施记录（2026-09-05，已完成）

**记录点**：`_chat_response` 一处（覆盖全部 20 个分支）+ 8 处显式闸门：

| span | 位置 | 关键字段 |
| --- | --- | --- |
| `gate/chat_response` | `_chat_response` 唯一出口 | `source`、`status_code`、`artifact_type`、`reply_len`、`extra_keys` |
| `gate/soreness` | 入库成功与失败各一处 | `saved_count`、`regions`、`levels`、`prompted` |
| `gate/health_risk` | 风险合并之后（无条件） | `level`、`labels`、`painful_count`、`blocks_plan` |
| `gate/plan_context` | `scheduled_plan` 回填之后 | `decision`、`effective_subject`、`blocking_reasons`(TL)、`active_safety_constraints`(TL) |
| `gate/plan_validation` | 首次校验 + 目标对齐之后 | `violations`(TL)、`violation_count`、`goal_alignment_passed`、`alignment_stage` |
| `gate/auto_correction` | 修正收尾处 | `stop_reason`、`attempt`、`first_violations`/`final_violations`(TL) |
| `gate/context_budget` | `build_agent_input` 抛错处 | `code`、`total_len`、`limit` |
| `store_write/info_store` | 三个记忆候选各 2 处（写成 / 去重跳过） | `outcome`、`namespaces`、`keys`、`capture`、`user_confirmed` |

**顺带修掉一个真缺陷**（这正是"出口唯一"这条不变量的价值）：
`build_agent_input` 抛 `ContextInputError` 时原先 `return context_error_response(exc)`，
而该函数**在 `chat_workflow.py` 里根本没有导入**——`NameError` 被外层
`except Exception` 吞掉，用户拿到的是一句无用的 500"服务暂时不可用"，而不是
"整理后的上下文仍然过长，请缩短当前消息或训练计划"这条可操作提示。就算导入了也
还有第二个错：它返回 `JSONResponse` 而不是 `ChatResult`，路由层再包一层会因为
`body` 是 `bytes` 而抛 `TypeError`。现已改走唯一出口，新增 `source="context_budget"`。

**按审查意见落实的 4 点**：

1. **每个 gate 有自己的字段表**。`schema.py` 的 `_GATE_SPAN_FIELDS` 按 span 分表，
   `outcome` 是所有 gate 共有的结论字段；未注册的字段名照记、值一律丢弃。
2. **同名异类型**已消除：`store_write` 的 `namespace`/`key` 改成复数列表
   `namespaces`/`keys`（一次写入可能带多条事实），不再一个分支传 `str`、一个传 `list`。
3. **`stop_reason` 区分四种收尾**：`passed` / `still_violating` / `not_a_plan` /
   `call_failed`。前两种是模型能力问题，后两种是基础设施问题——混在一个
   `passed=False` 里，"要不要重试"就判断不了。`first_violations` 与 `final_violations`
   分开留着，修正失败**不覆盖**原始校验结论。
4. **AST + 运行时都有**。AST 三条（出口唯一、source 全集、出口里真有 `trace_event`），
   运行时按类别覆盖而非逐个 source——记录点是所有分支共用的一行，17 条近乎重复的
   用例不如"枚举层冻住全集 + 行为层覆盖各类别"。

**与审查意见的一处分歧**（明确说明而不是假装遵守）：审查建议把 `painful_regions`、
`regions`、`decision` 这类字段也做哈希或只记数量。这里**保留原样**，理由是它们的取值
是本项目源码里的**固定词表**（`REGION_ALIASES`、`CAUTION/URGENT`、
`follow_schedule/override_today/rest_today`），不是用户的自由叙述；哈希掉之后
"为什么不给我练腿"这个问题在 trace 里就答不出来了，而这正是阶段 3 的全部目的。
真正的自由文本（用户原话、违规描述、安全约束原文）仍然只有 `full` 级别才落盘。

**实测一次自动修正通过的回合**（`meta` 级别，7 条事件）：

```
1  turn_start   /chat            message_len=12 message_hmac12=61b89495d189 history_len=0
2  gate         health_risk      outcome=none painful_count=0 blocks_plan=false
3  gate         plan_context     outcome=override_today effective_subject=上肢训练
4  gate         plan_validation  outcome=failed violation_count=1 violations_hmac12=[08d7d53b6eb3]
5  gate         auto_correction  outcome=passed stop_reason=passed first=1 final=0
6  gate         chat_response    source=agent status_code=200 artifact_type=training_plan
7  turn_end     /chat            status=ok trace_errors=0
```

注意第 4 与第 5 条里**同一个违规摘要** `08d7d53b6eb3` 前后出现：不落原文也能判断
"修正改掉的正是那一条"。这是 HMAC 摘要设计的直接收益。

**验证**：`tests/test_trace_decision_coverage.py` 9 条全绿；全量 988 passed
（唯一失败仍是 HEAD 上就红的 README 措辞断言）。关闭 trace 时再测一次急症拦截，
`traces/` 目录不存在、零文件。

---

### 阶段 4 — 6 个模型触点（TRACE-01）

ChatGPT 5.6Sol审查意见：装饰器与异常分支双重记录的思路可行，但存在重复事件风险。应定义一次调用的唯一 call_id，并规定装饰器、调用点和重试之间如何合并，确保指标不会被重复计数。

**目标**：把静默降级变成可见事件。这一步顺带回答了 BUG-02 留下的问题——
"意图为空"到底是用户没这个意图，还是路由挂了。

ChatGPT 5.6Sol审查意见：目标直指故障可区分性；建议统一“跳过、成功空结果、失败回落”三种状态及其查询语义。

**新增**：`fithealth_agent/observability/model_trace.py`

ChatGPT 5.6Sol审查意见：单独模块便于统一口径；应明确装饰器对同步/异步函数、重试和流式响应的支持范围。

```python
def traced_model_call(span: str):
    """包住一次外部模型 HTTP 调用，记录延迟、状态码、token、回落原因。

    绝不改变返回值，也绝不改变异常传播——这 6 个函数的静默回落是**有意的**
    设计（本地数据不能基于猜测的意图去改），trace 只负责让回落变得可见。
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                trace_event("model_call", span, ok=False,
                            dur_ms=_ms(started), error_type=type(exc).__name__)
                raise
            trace_event("model_call", span, ok=True, dur_ms=_ms(started),
                        **_outcome_fields(span, result))
            return result
        return wrapper
    return decorator
```

ChatGPT 5.6Sol审查意见：装饰器示例保持返回值和异常语义是优点；实现时需同时支持同步/异步函数，并避免包装层改变调用栈、超时和取消行为。

装饰器只能看到返回值，看不到"HTTP 502 还是 JSON 解析失败"。所以每个模块的
`except` 分支里还要补一行——**这是本阶段真正的价值所在**：

```python
# chat_intent_router.py:279
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        # BUG-02：这里静默返回空意图是刻意的（不能基于猜测改本地数据），但
        # "路由挂了"和"用户没这个意图"在现场必须能区分开。
        trace_event("model_call", "chat_intent_router", ok=False,
                    fallback_reason=type(exc).__name__)
        return ChatIntent()
```

ChatGPT 5.6Sol审查意见：在回落分支显式记录原因很关键；建议记录规范化错误类别和 call_id，不要把异常消息或请求内容原样写入 trace。

逐点清单（6 处装饰 + 8 处 `except` 补记）：

ChatGPT 5.6Sol审查意见：清单完整性应由测试或静态检查自动校验，避免新增模型触点后再次出现覆盖缺口。

| 模块 | 函数 | 装饰 | except 补记行 | token 来源 | 审查意见 |
| --- | --- | --- | --- | --- | --- |
| `health_safety.py:135` | `classify_user_health_statement` | ✔ | 该函数的 `except` 分支 | 响应 `usage` | ChatGPT 5.6Sol审查意见：健康分类结果应记录状态和版本，不应记录症状原文或完整响应。 |
| `chat_intent_router.py:249` | `route_chat_intent` | ✔ | `:279` | 响应 `usage` | ChatGPT 5.6Sol审查意见：应区分空意图、跳过和失败回落，并用 call_id 去重。 |
| `information_router.py:313` | `route_information` | ✔ | `:247` 附近的 `except` | 响应 `usage` | ChatGPT 5.6Sol审查意见：长期记忆候选属于持久化前置步骤，建议记录保存决策和幂等标识。 |
| `plan_goal_validator.py:84` | `validate_plan_goal_alignment` | ✔ | `:148`，另加 `:134/:139` 两处**结构校验失败回落**（`parsed is None`、`matched∪missing≠targets`）——这两条现在完全静默 | 响应 `usage` | ChatGPT 5.6Sol审查意见：结构解析失败要与模型调用失败分开统计，并记录校验器版本。 |
| `muscle_map.py:407` | `query_muscles_with_lite_model` | ✔ | 其 `except` 分支 | 响应 `usage` | ChatGPT 5.6Sol审查意见：规则表回落应带规则版本和命中标志，避免误把本地结果当模型结果。 |
| `food_analysis.py:128` | `analyze_food_image` | ✔ | `:160` | 响应 `usage` | ChatGPT 5.6Sol审查意见：图片分析需要限制图像/OCR 载荷大小，并确保元数据和原图不进入默认 trace。 |

三个"没调模型"的短路也要记，否则会被误读成"模型返回了空"：
`chat_intent_router.py:255`（`not allow_external_models or not ROUTER_API_KEY`）、
`plan_goal_validator.py:104`、`muscle_map.py` 同类判断 → 记
`ok=None, skipped_reason="external_models_disabled" | "no_api_key"`。

ChatGPT 5.6Sol审查意见：短路事件应与真实模型调用使用同一 schema，并明确优先级（配置关闭、缺 key、输入不适用），这样统计才不会重复或误报。

**验证**：这 6 个模块都已有测试且都支持注入 `requester`
（`plan_goal_validator.py:93`、`health_safety.py:148`、`muscle_map.py:430` 都有
`requester=None` 参数）。在 `test_plan_goal_validator.py`、
`test_chat_intent_router.py`、`test_external_model_controls.py` 中各加一例：
注入一个抛 `RequestException` 的 requester，断言返回值**不变**（回落契约不能被
trace 改动）且 trace 里出现 `ok=False, fallback_reason="RequestException"`。

ChatGPT 5.6Sol审查意见：回落值不变的断言很关键；还应覆盖 HTTP 错误、解析错误和超时，并断言不会泄露请求体、密钥或完整异常堆栈。

**回滚**：装饰器改为恒等函数，一处生效。

ChatGPT 5.6Sol审查意见：回滚路径简单，但应保留失败行为测试，确保移除观测后业务回落契约仍稳定。

#### 阶段 4 实施记录（2026-09-05，已完成）

**放弃了计划里的装饰器写法**。审查意见指出"装饰器记一条 + 每个 except 再记一条"
会让一次调用产生两条事件、任何按 span 的计数翻倍——这是对的。改成**一次调用一个
记录器**：`model_call(span)` 上下文管理器持有那唯一一条事件，函数内部只往上标注
（`skipped` / `request` / `http` / `fallback` / `failed` / `succeeded`），退出时统一
写出。于是重复计数在**结构上**不可能发生，`call_id` 天然唯一，也不需要"装饰器与
调用点如何合并"的规则。

**触点是 7 个，不是 6 个**。静态完整性检查（`tests/test_trace_model_calls.py` 扫描
全包，找出任何往 `/chat/completions` 发请求的函数）在落地当天就抓到了计划漏掉的
`plan_classifier._level2_llm_check`——判断上传的 Markdown 是不是训练计划，走
`/upload_plan`。计划书当初是按函数名 grep（`def route_`/`def classify_`…）才漏的。
连带发现**阶段 2 的排除表把 `/upload_plan` 写成"不碰外部模型"是错的**，该路由已
移入 `TRACED_ROUTES`。

| span | 模块 | 三态标注点 |
| --- | --- | --- |
| `route_chat_intent` | `chat_intent_router.py` | 3 个 skip、1 个 failed、2 个 fallback（`ambiguous_tool_calls` / `invalid_arguments`）、2 个 succeeded（意图名 / `none`） |
| `classify_user_health_statement` | `health_safety.py` | `no_api_key` / `no_http_client` / `unparseable_json` / failed / `true|false` |
| `route_information` | `information_router.py` | `no_api_key`、`external_models_disabled`（在 `route_information` 里，与 `_level3_llm_decide` 互斥）、`invalid_arguments`、`evidence_rejected`、`save|no_save` |
| `validate_plan_goal_alignment` | `plan_goal_validator.py` | 2 个 skip + **两处原先完全静默的结构回落**：`unparseable_json`、`target_set_mismatch` |
| `validate_training_plan` | `plan_classifier.py` | `no_api_key` / `unparseable_json` / failed / `is_plan|not_a_plan` |
| `query_muscles_with_lite_model` | `muscle_map.py` | 3 个 skip、`no_valid_muscle_ids`（回落规则表）、`hits:N` |
| `analyze_food_image` | `food_analysis.py` | `no_api_key` / `no_base_url`（**记跳过后仍抛业务异常**）、failed、置信度 |

**按审查意见落实的 5 点**：

1. **三态语义统一**：`ok=None` 没调 / `False` 调了但回落 / `True` 结果被采用。
   跳过原因有**优先级**（`external_models_disabled` → `no_api_key` → `empty_input`），
   两个条件同时成立时只记优先级更高的那个，统计口径不会重复。
2. **结构解析失败与调用失败分开**：前者 `http_status=200` + `fallback_reason` 且
   **没有** `error_type`；后者两者都有。
3. **不泄露**：只记规范化异常类名，不记异常消息、请求体、响应体、密钥、图片内容。
   `endpoint_host` 只留主机名（URL 里可能带 query）。图片只记 `request_bytes`。
   有一条用例断言落盘全文里没有 key、没有用户原话、没有 `Bearer`、没有完整路径。
4. **首因优先**：`error_type` 已记过就不覆盖。调用点常把底层异常包成自己的业务异常
   再抛（`FoodAnalysisError from exc`），那层包装每次都一样；真正想知道的是
   `ConnectTimeout` 还是 `HTTPError`。这一条是测试逼出来的。
5. **回落契约不变**有专门用例：每个触点在回合外跑一遍、回合内跑一遍，断言返回值
   `assertEqual` 相同。

**支持范围**：只支持同步函数——7 个触点都是裸 `requests.post`，没有 async、没有
流式、没有框架重试。`attempt`/`retry_count` 字段留着但恒为 1/0；真加重试时应在
同一个 `model_call()` 块内递增，而不是开第二个记录器。

**验证**：`tests/test_trace_model_calls.py` 15 条全绿；全量 1003 passed
（唯一失败仍是 HEAD 上就红的 README 措辞断言）。

**这一阶段还暴露了两个生产缺陷**（属于行为问题，未在本阶段修改，见交付说明）：
`classify_user_health_statement` 不受"外部模型开关"管辖；它的 `max_tokens=40`
会截断响应，导致该分类器在当前配置下几乎必然解析失败。

#### 阶段 4 附带修复（2026-09-05，已获批准后实施）

上面那两个缺陷都是 trace 照出来的，也都已修复。**它们不是"trace 的功能"，而是
trace 的第一批产出**——这也是本方案第 0 节那句"投入不足，不是方向不对"的直接印证。

**缺陷 1（隐私，P0）：`classify_user_health_statement` 不受外部模型开关管辖。**

证据是同一个回合里的两条事件——`route_chat_intent` 正确跳过，健康分类却真的发了
请求并计了费：

```
2  model_call  classify_user_health_statement  {"ok": false, "endpoint_host": "api.deepseek.com",
      "http_status": 200, "total_tokens": 261, "error_type": "JSONDecodeError"}
4  model_call  route_chat_intent               {"ok": null, "skipped_reason": "external_models_disabled"}
```

其余 6 个触点都有这道闸门，只有它漏了，而 README 的"外部数据传输"一节明确承诺
开关控制哪些信息离开本机。修法：给函数加 `allow_external_models` 关键字参数
（默认 `True`，保持既有调用方兼容），并在 `chat_workflow.py` 的调用点显式传值。

关闭时返回 `None` 而不是 `False`：`None` 是"不确定"，调用方会继续跑确定性的本地风险
筛查；`False` 会**跳过**那道筛查，等于关掉联网模型顺带关掉本地安全网，与 AGENT-01
的意图相反。

**缺陷 2：`max_tokens=40` 截断响应。**`completion_tokens` 恰好等于 40（即撞上上限）
且内容 `json.loads` 失败——响应在写完 JSON 之前就断了，于是该分类器在当前模型下
几乎必然回落到 `None`，还照样计费。改为 200，与本仓库其余轻量调用一致。

**顺带给 schema 加了 `finish_reason`**（`ModelCall.response()` 从响应体里一次性抽出
`usage` 与 `finish_reason`）。这次是靠"completion_tokens 恰好等于 max_tokens"推断出
截断的；有了 `finish_reason="length"` 下次可以一眼定性，不必推理。

**回归测试**落在 `tests/test_external_model_controls.py`（"关掉开关后什么不该离开
本机"的既有归属地）：注入一个 `Mock` requester 断言**从未被调用**、AST 断言调用点
真的传了那个关键字参数（光有参数不够）、以及 `max_tokens >= 200`。

**修复后用同一次实测复验**：

```
classify_user_health_statement   ok=None  skipped_reason='external_models_disabled'  endpoint_host=None  total_tokens=None
route_chat_intent                ok=None  skipped_reason='external_models_disabled'  endpoint_host=None  total_tokens=None
```

全量 1006 passed。

---

### 阶段 5 — ReAct 内部（sink 适配，TRACE-02/04/05）

ChatGPT 5.6Sol审查意见：鸭子类型 sink 能避免 fork 框架，但必须锁定框架事件接口的最小契约，并处理未知事件、字段缺失和框架升级。主 agent 与修正 agent 共用 turn 时，还应记录 parent_span 或 attempt 序号。

**目标**：把框架事件引进同一个 turn，并补上可复现所需的字段。

ChatGPT 5.6Sol审查意见：目标与 TRACE-02/04/05 对应清楚；建议把 sink 的事件映射表和未知事件处理策略先固定下来。

**新增**：`fithealth_agent/observability/sink.py` 中的 `TurnTraceSink`——鸭子
类型实现框架用到的三个成员（`log_event(event, payload, step=None)`、
`finalize()`、`session_id`），把事件翻译成本项目的 `kind`/`span` 后写进当前 turn。

ChatGPT 5.6Sol审查意见：鸭子类型能避免修改第三方框架，但应锁定最小接口契约，并对未知事件、字段缺失和框架升级做兼容测试。

**改动**：`agent.py` 的 `create_fithealth_agent` 增加一个 `role` 参数，构造后装
sink：

ChatGPT 5.6Sol审查意见：`role` 能区分主 agent 与修正 agent；建议再记录 attempt 或 parent_span，避免同一 turn 内多次修正无法排序。

```python
def create_fithealth_agent(*, avoid_youtube_channels=None, role="agent"):
    ...
    agent = ReActAgent(..., config=AGENT_CONFIG, max_steps=15)
    # 框架的文件式 TraceLogger 已在 AGENT_CONFIG 里关掉（阶段 0）。这里装一个
    # 同接口的 sink，把 model_output / tool_call / tool_result 引进当前 turn，
    # 于是主 agent 与自动修正 agent 共享同一个 turn_id，用 role 区分。
    agent.trace_logger = TurnTraceSink(role=role)
    trace_event("react_init", role,
                model=llm.model if hasattr(llm, "model") else None,
                system_prompt_sha256_12=_sha12(runtime_system_prompt),
                system_prompt_len=len(runtime_system_prompt),
                tools=sorted(registry.list_tools()),
                max_steps=15)
    return agent
```

`chat_workflow.py:1154` 的自动修正 agent 传 `role="correction_agent"`。

ChatGPT 5.6Sol审查意见：角色标识足够区分两类 agent；若同一回合多次重试或并行修正，应再记录 attempt、parent_span 和触发原因。

**补齐 TRACE-05（可复现）**：上面的 `react_init` 事件记 system prompt 的哈希+
长度、工具清单、模型 id、`max_steps`。`meta` 级别不记 prompt 原文（里面有档案
与记忆），`full` 级别记全文。**同时给 `build_agent_input` 的产物记一条**
`agent_input`（哈希 + 长度 + 分段长度：档案段/计划上下文段/记忆段/历史段），
这样"上下文预算超了"或"记忆没进上下文"这类问题不必开 `full` 就能定位。

ChatGPT 5.6Sol审查意见：可复现字段基本齐全；建议增加代码版本、配置版本、schema 版本和随机性参数，内部 endpoint 只记录主机或哈希。

**补齐 TRACE-04（token/成本）**：`TurnTraceSink` 在翻译 `model_output` 时累加
`usage.total_tokens`；`turn_end` 汇总所有 `model_call` + `react_step` 的 token，
按 `FITHEALTH_TRACE_PRICE_JSON` 估算 `cost_estimate`，并标注 `cost_basis:
"estimated"`——框架给的 `cost` 是硬编码 0，绝不能当真。

ChatGPT 5.6Sol审查意见：成本需标注价格表版本、币种以及重试、缓存和多模型混用时的计费口径；缺少 usage 时应明确显示“未知”而非 0。

**关于 `sink` 的 `finalize()`**：实现为 no-op（落盘由 turn 负责）。这样即使
`agent.run` 从未被调用或中途抛异常，也不会有任何悬挂句柄——TRACE-07 在阶段 0
已被关闭框架 logger 消除，这里保证不重新引入。

ChatGPT 5.6Sol审查意见：no-op 设计可避免句柄问题，但仍需验证异常、取消和进程退出时 turn 层一定落盘或明确标记不完整。

**验证**：`tests/test_trace_react_sink.py`

ChatGPT 5.6Sol审查意见：测试应覆盖未知框架事件、并发工具结果、异常中断及 sink 自身失败，确保观测故障不影响 agent 主流程。

- 用一个假 agent（现有测试已有 `_Agent()` / `fake_agent` 模式，见
  `test_soreness_stage3.py:428`）驱动 sink，断言 `tool_call`/`tool_result` 落进
  当前 turn 且带 `role`；

ChatGPT 5.6Sol审查意见：该测试应同时验证参数/结果脱敏、事件序号和异常结果状态，不能只检查事件存在。
- 断言主 agent 与修正 agent 的事件在**同一个 turn_id** 下，`role` 不同；

ChatGPT 5.6Sol审查意见：还应验证并发请求之间 turn_id 不串线，并确认 parent/attempt 关系可排序。
- 断言 `sink.finalize()` 不写文件、不关句柄；

ChatGPT 5.6Sol审查意见：应检查 no-op 在成功、异常和未调用 agent 三种路径都成立，同时由外层 turn 负责最终落盘。
- 断言 `meta` 级别下 trace 不含 `SYSTEM_PROMPT` 的任意 30 字连续子串。

ChatGPT 5.6Sol审查意见：连续子串断言可防止明显泄露；建议再做字段级白名单和低熵内容反查测试。

#### 阶段 5 实施记录（2026-09-05，已完成）

**放弃了计划里"把 `TurnTraceSink` 放进 `sink.py`"的写法**：`sink` 被 `trace` import，
而 sink 要用 `trace_event`，放一起就是循环导入。而且职责也不同——`sink.py` 管磁盘，
这个翻译器一个字节都不落盘。改成按 `model_trace.py` 的同一形状单开
`observability/react_trace.py`（`react_trace` → `trace`，单向）。用量汇总同理单开
`observability/cost.py`（纯函数，`trace` → `cost`）。

**鸭子类型契约由静态检查兜住**（审查："必须锁定框架事件接口的最小契约，并处理未知
事件、字段缺失和框架升级"）。`tests/test_trace_react_sink.py` AST 扫描
`hello_agents/**/*.py`，取出所有 `self.trace_logger.log_event("…")` 的字面量与
`self.trace_logger.<member>` 的成员名，两条断言：每个事件都有归属，每个成员 sink 都
实现了。鸭子类型没有编译期检查，这是唯一的替代品。

| 框架事件 | 本项目 kind | 备注 |
| --- | --- | --- |
| `message_written` | `agent_input` | 只有长度与摘要；分段长度在 `gate/context_budget` |
| `model_output` | `react_step` | `cost: 0.0` 与 `usage` 原样都不进 payload |
| `error` | `react_step` + `stop_reason="llm_error"` | `message` 一个字不记 |
| `tool_call` | `tool_call` | 只记 `args_keys`，值按 T 处理 |
| `tool_result` | `tool_result` | **补上框架缺的 `status`**，见下 |
| `session_end` | `react_end` | 本 role 的 token 累计 |
| `session_start` | 刻意不记 | 构造后才装 sink，发不出来 |
| 其它（`hook_*`、将来新增） | `react_unmapped` | 只记事件名，payload 的值一律丢弃 |

**按审查意见落实的 7 点**：

1. **`finalize()` 严格空操作**。框架只在三条正常返回路径调它，异常路径一次都不调；
   空操作让两者行为完全一致，落盘只由 `start_turn` 的 finally 负责。TRACE-07 不会
   借这条路回来——有一条用例断言 sink 从未创建任何文件。
2. **顺手补上框架缺的工具成败**。框架只在内置工具分支写 `status`，用户工具分支连字段
   都没有（§1.3 已记下这个缺口）；成败其实在结果文本的前缀里（`_execute_tool_call`
   用 ❌ / ⚠️）。sink 把它还原成 `success` / `partial` / `error` 三态枚举，于是不必
   靠人去读 emoji。
3. **0 token 记成"不知道"而不是"免费"**（TRACE-04）。框架在 `response.usage` 为空时
   写 0，sink 遇到非正整数就**不写这个字段**并标 `usage_source="unknown"`；
   `turn_end` 另记 `usage_missing` 计数。翻译层还有一个统一的 `_record()` 专门丢掉
   值为 None 的字段——`redact` 会把 None 原样落成 `null`，那样"缺失"和"报了个空值"
   就混在同一个键上了。
4. **成本只报算得准的那部分**。框架只给 ReAct 每步的 `total_tokens`，没有输入/输出
   拆分，而价格表是分方向的。这类 token 不硬算（按输出价会高估几倍、按输入价会低估），
   而是计进 `cost_unpriced_tokens`：`cost_estimate` 是已定价部分的准确值，缺口有多大
   明写着。没有价格表时 `cost_basis="unknown"`，绝不写 `cost_estimate=0`。
   跳过的调用（`ok is None`）不计入 `model_calls`——它压根没发生。
   实测一次真实回合：`model_calls=3, usage_missing=1, total_tokens=13146,
   cost_estimate=0.000172, cost_basis=estimated, cost_unpriced_tokens=12406`。
5. **派生值调用方改不动**。用量六个字段加进 `RESERVED_TURN_END_FIELDS`：允许业务另报
   一份，就是允许两个数字对不上而没人知道哪个对。传同名字段会被拒绝并计入 `trace_errors`。
6. **提示词摘要摘的是静态那份**（审查："还需记录代码/配置/schema 版本及随机性参数"）。
   `create_fithealth_agent` 会往系统提示词尾部拼当前时刻，对整份摘要等于每秒换一个值，
   "这次用的是哪版 prompt"就永远答不出来。所以 `system_prompt_sha12` 摘 `SYSTEM_PROMPT`
   （跨进程稳定，**不用** HMAC——这是本仓库自己的源码，不是用户数据），拼接后的总长
   另记 `runtime_prompt_len`。随机性与超时参数记 `temperature` / `timeout_s`，工具契约
   记 `tools` / `tool_count` / `tool_schema_digest`。
7. **`endpoint_host` 只留主机名**：完整 `base_url` 可能带内网拓扑与 query。

**关闭时 sink 不装**（这一条计划里没写，但它决定了阶段 5 会不会破坏阶段 1 的验收）。
`attach_react_trace` 先看 `load_settings().enabled`，关闭时 `agent.trace_logger` 留在
`None`，于是框架的 `if self.trace_logger:` 短路，连 payload 字典都不构造。
`tests/baseline/agent_snapshot.json` 里 `trace_logger_type: null` 也继续为真；
快照沙箱另外显式清掉 `FITHEALTH_TRACE`，免得基线随开发机环境漂移。

**`agent_input` 与 `gate/context_budget` 的分工**（计划原本只说给 `build_agent_input`
的产物记一条 `agent_input`）。落地时发现这两件事必须分开：

* 上下文**超预算被拒**时 `agent.run` 从未被调用，框架一个事件都不发——分段长度只能在
  组装处记，否则恰恰在最需要的时候没有数据。所以 `build_agent_input` 记
  `gate/context_budget`，`outcome` 两种结局都记，带 8 段各自的长度。
* 框架**实际收到**的那串输入由 sink 记成 `agent_input`（每个 role 一条）。两条事件的
  `total_len` 同名，可以直接比——不相等就说明中间有谁改过上下文。

`chat()` 里 `except ContextInputError` 那处的 `trace_event` 随之删掉：同一次拒绝记两条
就是审查反复强调的重复计数。`agent_input` 的 `section_lens` 字段一并移走（阶段 1 注册过
但零调用点、从未落过盘，没有读方会被影响，故不升 `SCHEMA_VERSION`）。

**纠正掉一个框架缺陷**：LLM 调用失败时框架先发 `error` 再 `break`，**接着走"达到
最大步数"那条路**发出 `session_end{status: "timeout"}`（`react_agent.py:181-189` →
`:365-385`）。于是"真的用完 15 步"和"第 1 步就挂了"在框架口径里是同一个值，而这两件事
的处理人完全不同（一个查模型服务，一个调 `max_steps` 或提示词）。

本项目不 patch `hello_agents`（会破坏 `vendor/` 的 wheel 校验约束），所以在翻译层纠正：
sink 记住本轮是否出现过 `error`，翻译 `session_end` 时给出已纠正的结论。

| 现场 | 框架说 | trace 记 |
| --- | --- | --- |
| 正常收尾 | `success` | `status=success` `stop_reason=finished` |
| 真的用完步数 | `timeout` | `status=timeout` `stop_reason=max_steps` `max_steps_reached=true` |
| 第 N 步 LLM 挂了 | `timeout` | `status=llm_error` `stop_reason=llm_error` `error_type=…` `framework_status=timeout` |

两条纪律：**纠正必须留痕**——`framework_status` 只在与纠正结果不一致时出现（正常回合
不添噪声），所以"框架原话是什么"永远读得回来，不是被我们悄悄改掉的；
**`max_steps_reached` 由步数算而不是抄框架的 status**——`total_steps >= max_steps` 是独立
可验的事实，而那个 status 恰恰是这里不可信的那一项。`attach_react_trace` 因此要把
`agent.max_steps` 传给 sink；拿不到时（假 agent）才退化为看纠正后的 status。

实测三种收尾：

```
llm-crash    status=llm_error framework_status=timeout stop_reason=llm_error error_type=LLM_ERROR total_steps=1  max_steps_reached=false
step-limit   status=timeout                           stop_reason=max_steps                      total_steps=15 max_steps_reached=true
normal       status=success                           stop_reason=finished                       total_steps=1  max_steps_reached=false
```

**同一个缺陷的另一半也修了**（见下面"阶段 5 附带修复"）：框架把 LLM 异常**吞掉**
而不是重抛，`agent.run` 正常返回"抱歉，我无法在限定步数内完成这个任务。"，于是
`chat_workflow` 那条 `except HelloAgentsException → 503` 永远不会触发。

**`truncated` 字段恒不出现**：框架只在**异步** `arun` 路径调 truncator，本项目走同步
`run`（`run_in_threadpool(agent.run, …)`），工具结果一字不截。字段留在契约里备用，
眼下判断"观察是不是太大了"看 `result_bytes`。

**实测一次带 ReAct 的回合**（`meta` 级别，9 条事件）：

```
1 turn_start   /chat                source=chat message_len=11 history_len=0
2 react_init   agent                model=deepseek-chat endpoint_host=api.deepseek.com
                                    temperature=0.7 timeout_s=60 max_steps=15 tool_count=14
3 agent_input  agent                total_len=3120 text_hmac12=fbfa677253e5
4 react_step   agent                step=1 tool_calls=2 total_tokens=12406 usage_source=provider
5 tool_call    query_daily_records  role=agent args_keys=[date,limit]
6 tool_result  query_daily_records  role=agent status=success result_bytes=15
7 react_step   agent                step=2 tool_calls=1 total_tokens=3474
8 react_end    agent                status=success total_steps=2 total_tokens=15880 dur_ms=41230
9 turn_end     /chat                status=ok total_tokens=15880 model_calls=2 trace_errors=0
```

**验证**：`tests/test_trace_react_sink.py` 45 条全绿；全量 1051 passed
（唯一失败仍是 `68e9443` 遗留的 README 措辞断言）。收尾校验：关闭 trace 时连发三条
`/chat`，数据目录零新增文件、`traces/` 不存在、`trace_logger is None`；
`agent_snapshot.json` 无漂移。

#### 阶段 5 附带修复（2026-09-05，已获批准后实施）

上面那个框架缺陷的另一半：框架不只是把 status 记错了，它把 LLM 异常**吞掉**而不是
重抛（`react_agent.py:181-189` 只 `break`），于是 `agent.run` 正常返回一句"抱歉，我
无法在限定步数内完成这个任务。"。后果是**用户可见**的——模型服务故障（超时 / 502 /
缺 key / 余额不足）显示成 HTTP 200 加一句"任务太复杂"，而 `chat_workflow` 那条
`except HelloAgentsException → 503 "模型服务当前不可用"` 分支永远不会触发。用户既不
知道该重试还是该改问法，也看不出"本地汇总仍然可用"这条出路。

**修法**：不 patch 框架，也**不匹配那句中文文案**（它是框架的实现细节，随版本会变）。

1. `agent.py` 新增 `TrackedLLM(HelloAgentsLLM)`：`invoke_with_tools` 记下异常再原样
   抛出。只覆盖同步入口就够了——框架的 `ainvoke_with_tools` 只是把同步版丢进
   executor（`core/llm.py:269-282`），这条委派关系由一条用例钉住。
2. `swallowed_model_failure(agent)` 逐层 `getattr` 取那次异常（假 agent 也不能崩）。
3. `chat_workflow` 在 `agent.run` 返回后检查一次，一律包成 `HelloAgentsException`
   （原异常挂 `__cause__`，`logger.exception` 连着打）：对用户来说超时、502、缺 key
   都是同一件事，让两条路径汇到同一个出口，503 的文案与状态码才只有一处定义。
4. **修正循环同一处理**：原先它的 LLM 挂掉之后拿到那句套话，被判成"结果不是完整训练
   计划"，于是 `gate/auto_correction.stop_reason` 记成 `not_a_plan`（模型能力问题），
   而真相是 `call_failed`（基础设施问题）——"要不要重试"就判断反了。现在走既有的
   `except → corrected_answer = ""` 降级路径，用户看到的是"自动修正服务调用失败"。

**回归测试**落在新增的 `tests/test_model_failure_surfacing.py`（10 条）。其中一条刻意
**钉住框架当前的吞异常行为**（真实 agent + 打桩 adapter，断言 `agent.run` 返回那句
套话而不是抛）：框架哪天改成重抛，它会先红，届时这段绕行代码可以删掉，而不是留着
当谜题。另有一条让假 agent 在有故障的同时返回一句别的话，仍然 503——证明修复不依赖
文案匹配。

全量 1061 passed。


---

### 阶段 6 — 保留、清理、查看（TRACE-06/09）

ChatGPT 5.6Sol审查意见：隐私闭环、留存和查看工具放在最后合理，但 P0 的清理能力应在阶段 0 或阶段 1 就具备最小可用版本，避免中间阶段继续产生无法治理的 PHI。CLI 还应限制输入路径，防止误读或覆盖任意文件。

**改动 1（P0，隐私闭环）**：`routes/maintenance_ops.py:90-101` 的 `_reset_steps()`
增加第 11 步：

```python
        ("traces_removed", "Agent 执行轨迹", deps.trace_store.clear),
```

ChatGPT 5.6Sol审查意见：清理步骤应具备幂等性、权限错误可见性和删除后验证；P0 能力应尽早具备最小可用版本。

同时在 `runtime/deps.py` 的共享单例区加 `trace_store = TraceStore()`——按该模块
开头的约定（第 7-18 行），会被测试整体替换的东西必须走 `deps.X` 属性访问。

ChatGPT 5.6Sol审查意见：通过可替换依赖注入符合现有测试治理；应额外验证单例初始化顺序和多进程访问不会产生重复清理。

`_reset_steps()` 的每一步都是独立 try/except（DATA-14 的设计），trace 删除失败
不会影响其余 10 项，且会出现在响应的 `steps` 里。

ChatGPT 5.6Sol审查意见：独立 try/except 能提高恢复能力；建议同时记录失败原因类别、重试建议和最终是否仍有残留文件。

**关于备份**：trace **不**进 `LocalBackupService`。理由：备份的用途是恢复用户
数据，trace 是诊断产物，把它塞进备份只会让 1 GiB 上限更早触顶；但**恢复备份后
应清空 trace**，否则新数据与旧诊断记录混在一起会误导排障。在
`backup_service` 的 `on_restored` 回调列表里追加 `trace_store.clear`
（`deps.py:87-92` 已有 `on_restored=[info_store.revalidate]` 这个挂载点）。

ChatGPT 5.6Sol审查意见：不纳入备份可控制 PHI 扩散，但恢复后清空必须有失败告警、重试和可验证结果，并明确清理顺序。

**改动 2**：保留策略。`sink.py` 在每次落盘后执行 `prune()`：三条上限
（回合数 / 天数 / 总字节）同时判断，从最旧的日期目录开始删，并同步重写
`index.jsonl`。prune 失败只 `logger.warning`，不影响主流程。

ChatGPT 5.6Sol审查意见：三重上限合理；需定义并发 prune、索引重建、崩溃中断和单文件超过总上限时的处理规则。

**改动 3**：查看工具。`scripts/trace_report.py`：

ChatGPT 5.6Sol审查意见：CLI 有助于降低服务端暴露面；应限制输入路径、默认只读，并采用安全的输出文件创建策略。

```bash
/e/anaconda3/python scripts/trace_report.py --list --last 20
/e/anaconda3/python scripts/trace_report.py --turn t-20260904-101929-74ca3f --html out.html
/e/anaconda3/python scripts/trace_report.py --failed-plans --since 7d
```

HTML 渲染**必须** `html.escape` 所有 payload（TRACE-08 的教训）。

ChatGPT 5.6Sol审查意见：输出编码是必要条件但不是全部；还应限制输入路径、默认只读、避免覆盖原始 trace，并为危险 URL、属性和 Unicode 控制字符加测试。

**是否加 `/debug/traces` 路由**：建议**先不加**。理由：`main.py` 的 `_include_router`
依赖冻结的扁平路由顺序（`main.py:58-63` 的注释），`tests/route_snapshot.py`
会对路由快照做断言，加一条路由要动那份基线；而 CLI 已能满足排障需求。若后续
确实需要页面，再单开一个阶段，并且必须绑 `127.0.0.1` + 默认关闭。

ChatGPT 5.6Sol审查意见：暂不开放 HTTP 查看面能降低暴露面；若未来增加，应补认证、授权、审计和路径遍历防护，而不只是绑定本机。

**改动 4**：历史清理。`memory/traces/` 下 200 个旧格式文件由旧实现产生，格式不
兼容新 CLI。一次性删除即可（已 gitignore，无历史价值——真正有价值的那次睡眠
bug 分析已写进 `开发日志.md:291`）。

ChatGPT 5.6Sol审查意见：一次性删除旧格式前应确认没有审计或回归依赖，并保留清单、哈希和删除记录；“无历史价值”要有可追溯依据。

**验证**：`tests/test_trace_retention_and_reset.py`

- 三条保留上限各自单独生效，以及同时生效时的删除顺序；

ChatGPT 5.6Sol审查意见：应使用边界值和并发场景验证，尤其是恰好达到上限、单回合超大和日期跨界。
- `/data/reset` 之后 trace 目录为空，且响应体里有 `traces_removed`；

ChatGPT 5.6Sol审查意见：除目录为空，还应验证索引、临时文件和符号链接残留，并确认重复 reset 幂等。
- 恢复备份后 trace 被清空；

ChatGPT 5.6Sol审查意见：应覆盖恢复成功、恢复失败和回调异常，确保旧 trace 不会与新数据混淆。
- `prune` 遇到只读目录时不抛异常、主流程不受影响；

ChatGPT 5.6Sol审查意见：还应断言告警可检索且不会暴露路径中的敏感信息，并验证权限恢复后的下一次 prune。
- `trace_report.py` 对含 `</pre><script>alert(1)</script>` 的 payload 渲染出的
  HTML 里不含可执行的 `<script>`（锁住 TRACE-08 不复发）。

ChatGPT 5.6Sol审查意见：该 XSS 回归用例必要；建议扩展到属性、URL、Unicode 控制字符和双重编码，并检查输出文件不会加载外部资源。

#### 阶段 6 实施记录（2026-09-05，已完成）

**落点：`sink.py` 而不是新模块。** 保留策略与清理入口和写入共用同一份目录布局知识——
"哪个文件属于我们"这个判断只能有一处，否则清理迟早会漏掉一类产物。（`index.jsonl.lock`
就是最容易漏的那个：它由 `JsonFileLock` 建在 trace 目录里，与回合文件毫无相似之处。）
`TraceStore` 做成实例是为了和其余 11 个 store 一样能被 `mock.patch.object(deps, …)`
整体替换；构造刻意不碰文件系统——`deps` 在启动时就 import，在那里建目录会破坏"trace
关闭即零副作用"。

**清理闭环（TRACE-06）**：`/data/reset` 第 12 步 `traces_removed`，加上恢复备份后的
`on_restored` 回调。三条纪律：

1. **`clear()` 绝不抛。** 它同时挂在 reset 的一步和恢复回调上；后者抛异常会让一次
   **已经生效**的恢复被报成失败。逐文件 try/except，返回删除数，失败只告警。
2. **回调写成函数而不是绑定方法。** `on_restored=[…, trace_store.clear]` 会在 import
   时捕获实例，测试替换 `deps.trace_store` 之后回调还指向旧的——正是 `deps.py` 开头
   那条"打桩会静默失效"。改成 `_clear_traces_after_restore()`，调用时才解析模块全局量。
   顺序也进了契约：`import_backup` 按**位置**取 `callback_results[0]` 当记忆库重校验
   结果，所以 `info_store.revalidate` 必须留在第一位。
3. **只删认得出来的产物。** 日期目录、`turn-*.jsonl`、`index.jsonl`、`.lock`、`.tmp`
   残片、`.write-probe-*`；软链目录直接拒绝，`notes.md` 这类不认识的东西一个不动。
   `clear()` **不看 `FITHEALTH_TRACE` 开关**——关掉 trace 之前留下的产物同样要能删。

**保留策略（TRACE-09）**：三条上限同时判断，先按天数和回合数筛，再用**剩下的**文件算
字节（被前两条判掉的文件反正要走，算进字节账只会让字节规则多删无辜的）。两条刻意的
取舍：

* **永不删最新那个回合**。一个超大回合本身就超上限时，删光其余也救不回来，而把刚出
  问题的那次对话抹掉恰恰是最坏的结果——改为保留并置 `over_budget` 告警。
* **index 只在真删了东西时才重写**。它是 O(N) 原子写，而 prune 每个回合都跑。

两个实现细节值得记下来：`prune` 全程持 index 的 `JsonFileLock`（跨进程只有一个在跑，
也不会与 `append_index` 交错）；目录枚举用 `os.scandir` 并且**必须 `with`**——
`DirEntry.stat().st_size` 在 Windows 上由枚举一次带回，不额外发系统调用，而没关的目录
句柄会让随后的 `rmdir` 静默失败。调用顺序是 `write_turn → append_index → prune`：
反了的话本回合刚写的索引行会被自己的 prune 当成陈旧行清掉。

`today` 由调用方传（`_finalise` 给 `datetime.now(TZ).date()`）：日期目录按本地时区命名，
让 `sink` 自己取"今天"就等于让它自己猜时区。

**查看工具**：`scripts/trace_report.py`，三条子命令 + 三条硬约束（只读 / 输入不接受
路径 / 输出全部转义）。转义那条按审查意见扩到了四种上下文：`html.escape(quote=True)`
覆盖文本与属性，控制字符（C0、C1、行分隔符、BiDi 覆写）换成可见的 `\xNN` / `\uXXXX`
记号，报告不引用任何外部资源并带 `default-src 'none'` 的 CSP。输出侧：不许写进 trace
目录，不覆盖已有文件（除非 `--force`，且用 `"x"` 模式创建）。

**顺带补上一个既有的忽略缺口**：`.gitignore` 只有 `data/*.json` 这类 glob，拦不住
子目录，所以 `data/traces/` 与 **`data/recovery-points/`** 都没被忽略——后者是
`/data/reset` 前的完整数据快照 zip。两条都补上，并在 `test_agent_tool_schema_contract.py`
里加了一条**问 `git check-ignore` 实际结果**的断言（不读 `.gitignore` 文本）。

**实测一次真实回合序列**（`FITHEALTH_TRACE_MAX_TURNS=3`，连开 6 个回合）：

```
回合文件：3 个（最新的三个）
index 行数：3，且与盘上文件逐一对应
/data/reset 删除：5 个文件（3 个回合 + index + index.lock），目录清空
第二次 reset：traces_removed=0（幂等）
```

**验证**：`tests/test_trace_retention_and_reset.py` 43 条 + `test_reset_transactionality.py`
新增 1 条；全量 1106 passed（唯一失败仍是 `68e9443` 遗留的 README 措辞断言）。
收尾校验：关闭 trace 时连发三条 `/chat`，数据目录零新增文件、`traces/` 不存在。

---

## 第五部分：与既有测试治理的衔接

本仓库有 84 个测试文件和三层结构治理，新增模块必须同步登记，否则 CI 会红：

ChatGPT 5.6Sol审查意见：把可观测性变更纳入既有治理是正确的；建议在 CI 中增加“未登记模块/文件”硬失败检查，降低对人工清单的依赖。

| 治理文件 | 需要做的事 | 审查意见 |
| --- | --- | --- |
| `tests/module_map.py` | 新增 `OBSERVABILITY_DIR` 常量；把 `trace_event` / `start_turn` / `traced_model_call` 等登记进 `FUNCTION_HOME`（该文件开头明确要求"每加一个函数只改这一行"） | ChatGPT 5.6Sol审查意见：集中登记有助于架构治理；建议用自动检查发现新增公开函数，避免人工漏登。 |
| `tests/test_module_map.py` | 随 `module_map.py` 更新后应自动通过；若断言"全部函数必须登记"则必须补全 | ChatGPT 5.6Sol审查意见：应明确测试失败信息和允许的私有辅助函数范围，避免治理规则过严或过松。 |
| `tests/test_deps_stub_reachability.py:65` | `trace_store` 加入可替换依赖清单（与 `create_fithealth_agent` 等 10 个函数同列） | ChatGPT 5.6Sol审查意见：依赖可替换性是隔离测试的关键；还应验证替换不会泄露到全局单例或跨测试污染。 |
| `tests/test_domain_boundaries.py` | `observability/` 不得反向 import `workflows/` 或 `routes/`；`domain/` 允许 import `observability`（只依赖 `trace_event` 这一个纯函数） | ChatGPT 5.6Sol审查意见：边界规则合理；应同时检查运行时循环依赖和类型检查/插件导入带来的隐式反向依赖。 |
| `tests/route_snapshot.py` | 阶段 6 若真加 `/debug/traces` 才需要动；当前方案不动 | ChatGPT 5.6Sol审查意见：冻结路由快照可控变更面；若新增管理入口，必须同步评估认证、授权和快照更新流程。 |
| `ARCH-09-测试治理清单.md` | 新增测试文件登记进清单 | ChatGPT 5.6Sol审查意见：登记应与 CI 检查绑定，否则清单容易过期而失去约束力。 |
| `tests/__init__.py` | `FITHEALTH_TRACE_DIR` 指向 `TEST_DATA_DIR/traces`，并把 `FITHEALTH_TRACE` 默认设为 `off`——测试不该产生 trace 文件（当前 84 个文件都 stub 了 `create_fithealth_agent`，靠的是运气不是约束，见 TRACE-12） | ChatGPT 5.6Sol审查意见：测试默认关闭合理；还应有一个显式 on 的隔离集成套件，验证真实落盘而不污染仓库。 |

**跑测试用 `/e/anaconda3/python`**：PATH 上的默认 `python` 是 3.14 且没装
pytest，会假成功。

ChatGPT 5.6Sol审查意见：固定解释器可避免环境误判；建议在 CI 中锁定 Python/pytest 版本并让错误退出码显式传播。

```bash
/e/anaconda3/python -m pytest tests/ -q
```

ChatGPT 5.6Sol审查意见：全量测试命令应补充超时、并发和覆盖率/安全测试入口，并在文档中说明外部模型是否需要 mock。

---

## 第六部分：验收标准

阶段 0 单独验收：

ChatGPT 5.6Sol审查意见：阶段性验收有利于控制风险；建议把每项转成可重复执行的命令、预期输出和失败处置人。

- [ ] `create_fithealth_agent().trace_logger is None`

ChatGPT 5.6Sol审查意见：应同时断言没有创建文件和句柄，并覆盖构造异常路径。
- [ ] 工具 schema 恰好 14 个，不含 `Skill`/`Task`/`TodoWrite`/`DevLog`

ChatGPT 5.6Sol审查意见：名称数量不足以保证契约，建议比较完整 schema 快照、参数和描述。
- [ ] 跑一次完整对话后，`memory/traces`、`memory/sessions`、`memory/todos`、
      `memory/devlogs` 均无新增文件

ChatGPT 5.6Sol审查意见：应在干净临时目录和异常/取消路径重复验证，并检查隐藏临时文件与日志输出。
- [ ] 全量测试通过，且**回答质量无明显退化**（少了 4 个 schema 是纯减法，但
      需要人工看一次实际回复）

ChatGPT 5.6Sol审查意见： “无明显退化”过于主观，建议定义固定回归样本、评分维度和可接受阈值，并记录模型版本。

阶段 1-6 整体验收——用"能不能回答这 6 个问题"来判定，而不是看代码写了多少：

ChatGPT 5.6Sol审查意见：以用户问题验收很实用；每个问题还应对应可自动查询的事件断言和一条失败样本。

1. **"为什么它没给我生成训练计划？"**——一条 turn 的 trace 能给出确切的闸门：
   `plan_context.decision=rest_today` / `blocking_reasons` / `health_risk=urgent`
   / `plan_validation.violations` / `auto_correction.passed=false` 之一。

ChatGPT 5.6Sol审查意见：应同时证明该原因确实导致响应 source，而不是仅在 trace 中出现过；建议加入因果关联字段。
2. **"意图路由到底调了没有？"**——`model_call/chat_intent_router` 有
   `ok` 与 `fallback_reason` 或 `skipped_reason`，不再只能猜。

ChatGPT 5.6Sol审查意见：需区分未到达、主动跳过、调用失败和成功返回空意图，并验证每种状态都能检索。
3. **"这一轮花了多少 token / 多少钱？"**——`turn_end.total_tokens` 覆盖全部 7
   个触点，`cost_estimate` 带 `cost_basis: estimated` 标注。

ChatGPT 5.6Sol审查意见：验收应允许供应商 usage 缺失，并检查总计不重复、价格表版本和币种均可追溯。
4. **"这次回答用的是哪版 prompt 和哪些工具？"**——`react_init` 有 prompt 哈希、
   长度、工具清单、模型 id、`max_steps`。

ChatGPT 5.6Sol审查意见：还需记录代码/配置/schema 版本及随机性参数，才能在版本升级后真正复现。
5. **"自动修正到底改了什么？"**——同一个 `turn_id` 下 `role=agent` 与
   `role=correction_agent` 的事件连续可读，`first_violations` → `final_violations`。

ChatGPT 5.6Sol审查意见：建议增加修正前后摘要或差异哈希、attempt 序号和停止原因，避免只看到两个结果而不知改动边界。
6. **"用户点了『删除全部数据』，健康信息还在磁盘上吗？"**——不在。trace 是
   `_reset_steps()` 的第 11 步，且默认 `meta` 级别本来就不落原文。

ChatGPT 5.6Sol审查意见：不能只验证主目录为空；还应扫描临时文件、备份、索引、崩溃转储和符号链接，并记录删除验证结果。

量化门槛：

ChatGPT 5.6Sol审查意见：量化指标方向正确；建议补充测量环境、样本量、置信区间和基线提交，避免单次测量误判。

- [ ] trace 关闭时，`/chat` P50 延迟变化 < 1%（`trace_event` 退化为一次
      `ContextVar.get()`）

ChatGPT 5.6Sol审查意见：应比较冷启动与稳态、成功与异常请求，并同时监控 P95/P99 以发现尾延迟回归。
- [ ] trace 开启（`meta`）时，`/chat` 额外延迟 < 20 ms，单回合落盘 < 64 KiB

ChatGPT 5.6Sol审查意见：阈值应按硬件和并发量校准，并明确是否包含 prune、fsync 和索引写入时间。
- [ ] `meta` 级别下，全量测试断言 trace 输出不含用户原文哨兵串

ChatGPT 5.6Sol审查意见：哨兵串只能检测已知样本；应增加敏感字段白名单、随机样本和高熵/低熵文本测试。
- [ ] 7 个模型触点 100% 有 `model_call` 事件（含跳过与失败）

ChatGPT 5.6Sol审查意见：应定义覆盖率分母和重试去重规则，并验证异常发生在调用前、调用中和解析后的情况。
- [ ] `chat()` 的 `return ChatResult(` 只出现在 `_chat_response` 内（AST 断言）

ChatGPT 5.6Sol审查意见：AST 约束不能替代运行时验证；应允许明确登记的错误出口并配合端到端 trace 检查。

---

## 第七部分：风险与取舍

| 风险 | 评估 | 应对 | 审查意见 |
| --- | --- | --- | --- |
| 阶段 0 减掉 4 个工具改变模型行为 | 中。这 4 个工具本项目从未实现对应能力，模型调用它们只会拿到无意义结果；但 schema 变化会轻微改变模型的选择分布 | 单独提交、人工对照一次完整对话；出问题只需还原一个实参 | ChatGPT 5.6Sol审查意见：应使用固定回归集比较工具调用率、回答安全性和计划质量，而不只看一次人工对照。 |
| `ContextVar` 在 `run_in_threadpool` 下失效 | 低。已实测通过 | 阶段 1 的传播测试锁死这个前提；若将来换 ASGI 栈，该测试会先红 | ChatGPT 5.6Sol审查意见：测试还应覆盖任务取消、嵌套上下文、多个 worker 和不同 AnyIO/Starlette 版本。 |
| trace 拖慢请求 | 低。缓冲 + 一次原子写；单回合事件量级在几十条 | 量化门槛已列；`FITHEALTH_TRACE_STREAM` 仅排障时开 | ChatGPT 5.6Sol审查意见：除平均延迟外应监控尾延迟、内存峰值、磁盘写入和 prune 开销，并设置超限降级。 |
| 在 `_chat_response` 记录会漏掉 `context_error_response(exc)`（`chat_workflow.py:1047` 那条不走 `_chat_response`） | 确实会漏 | 该处单独补一条 `gate/context_budget`；阶段 3 的 AST 断言正是为了发现这类旁路 | ChatGPT 5.6Sol审查意见：应把所有响应出口列成机器可检查的白名单，并用异常端到端测试验证关联完整。 |
| `full` 级别被误留在生产 | 中。这是唯一会写健康原文的开关 | 启动时 `logger.warning`；`turn_end` 里写 `detail_level` 字段，事后可甄别 | ChatGPT 5.6Sol审查意见：警告不足以阻止误用；建议默认拒绝生产 full、要求显式授权并设置自动过期。 |
| trace 目录被 Docker 卷外的路径吃掉 | 低 | 默认挂 `settings.data_dir()` 下（阶段 1 修 TRACE-12） | ChatGPT 5.6Sol审查意见：应在启动和写入时校验解析后的绝对路径、挂载状态、权限和剩余空间。 |

**明确不做的事**：

ChatGPT 5.6Sol审查意见：明确范围有助于控制复杂度；每项“不做”都应记录触发条件和重新评估时间，避免临时限制变成永久盲区。

- 不 fork / patch `hello_agents`（破坏 `vendor/` 的 wheel 校验约束）；

ChatGPT 5.6Sol审查意见：不修改第三方框架能降低维护风险；建议以适配层契约测试覆盖框架升级的破坏性变化。
- 不接 OpenTelemetry / 外部 APM（本项目是本地单用户部署，健康数据不出机器，
  引入外部 exporter 与 `external_models_enabled` 的隐私承诺相冲突）；

ChatGPT 5.6Sol审查意见：本地化取舍符合隐私边界；仍可保留可导出的无敏感指标或离线诊断接口，为未来规模化留出迁移路径。
- 不在阶段 6 加 `/debug/traces` 路由（会动冻结的路由快照基线，收益不足）；

ChatGPT 5.6Sol审查意见：暂不开放 HTTP 入口合理；若排障需求增长，应先设计认证、授权和审计，再调整路由基线。
- 不把 trace 纳入备份（只在恢复后清空）。

ChatGPT 5.6Sol审查意见：排除备份可减少 PHI 扩散，但需明确用户是否有保留诊断记录的需求，并验证恢复后清理失败的告警路径。

---

## 附录 A：改动文件清单

| 阶段 | 新增 | 修改 | 审查意见 |
| --- | --- | --- | --- |
| 0 | `fithealth_agent/tool_output.py`、`tests/agent_snapshot.py`、`tests/baseline/agent_snapshot.json`、`tests/test_agent_tool_schema_contract.py`、`scripts/purge_legacy_traces.py` | `fithealth_agent/agent.py`、`fithealth_agent/routes/maintenance_ops.py`、`.gitignore`、`.dockerignore`、`tests/test_data_dir_and_docker.py`、`tests/test_reset_transactionality.py` | ChatGPT 5.6Sol审查意见：阶段 0 文件清单应明确历史数据清理是否另行审批，避免代码提交掩盖不可逆操作。<br>**已落地**：清理走 `scripts/purge_legacy_traces.py`，默认只盘点（逐文件 sha256），销毁需显式确认短语，与代码提交解耦。 |
| 1 | `observability/{__init__,config,schema,redact,sink,trace}.py`、`tests/test_trace_infrastructure.py` | `fithealth_agent/atomic_json.py`（加 `atomic_write_text`）、`tests/__init__.py`、`tests/module_map.py`、`tests/test_module_map.py`、`ARCH-09-测试治理清单.md` | ChatGPT 5.6Sol审查意见：基础设施新增文件较多，建议先定义公共 API 和模块边界，再允许辅助实现扩张。<br>**已落地**：拆成 5 个单向依赖的子模块（`trace`→`sink`→`config`，`redact`→`schema`），公开 API 只从包顶层导出；`settings.py` 未改动，`trace_dir()` 放在 `observability/config.py`。 |
| 2 | — | `routes/chat.py`、`routes/uploads.py`、`routes/logout.py` | ChatGPT 5.6Sol审查意见：三条路由需共享同一生命周期契约，并分别验证解析失败、取消和响应序列化失败。<br>**已落地**：新增 `observability/http.py`（接入清单+排除表）与 `tests/test_trace_http_boundary.py`；解析失败、取消、序列化失败各有用例。 |
| 3 | `tests/test_trace_decision_coverage.py` | `workflows/chat_workflow.py`、`observability/schema.py` | ChatGPT 5.6Sol审查意见：工作流改动应配套 source 枚举覆盖矩阵和异常旁路测试。<br>**已落地**：`EXPECTED_SOURCES` 冻住 17 个 source；旁路由 `test_all_returns_go_through_the_single_exit` 挡住，并借此修掉 `ContextInputError` 分支的 `NameError`。 |
| 4 | `observability/model_trace.py`、`tests/test_trace_model_calls.py` | `chat_intent_router.py`、`information_router.py`、`plan_goal_validator.py`、`health_safety.py`、`muscle_map.py`、`food_analysis.py`、`plan_classifier.py`（第 7 个触点）、`observability/{schema,http}.py`、`routes/uploads.py`、`tests/test_trace_http_boundary.py` | ChatGPT 5.6Sol审查意见：跨六个模块的装饰器接入应分批提交，便于定位重复计数和回落行为变化。<br>**已落地**：改用一次调用一个记录器，重复计数在结构上不可能发生；静态扫描抓到第 7 个触点。 |
| 5 | `tests/test_trace_react_sink.py`、`tests/test_model_failure_surfacing.py` | `agent.py`、`workflows/chat_workflow.py` | ChatGPT 5.6Sol审查意见：sink 适配需固定框架事件接口版本，并验证主/修正 agent 的父子关系。<br>**已落地**：新增 `observability/react_trace.py`（鸭子类型 sink，`sink.py` 放不了——会循环导入）与 `observability/cost.py`（用量汇总纯函数）；框架事件清单由 AST 扫描框架源码兜住；`role` 加在 `create_fithealth_agent` 上，连带改 `fithealth_agent/__init__.py`、`tests/{module_map,test_module_map,test_package_lazy_import,test_training_plan_artifact_routing,test_context_memory,agent_snapshot}.py`。附带修复框架吞掉 LLM 异常导致 503 永不触发的问题（`TrackedLLM`）。 |
| 6 | `scripts/trace_report.py`、`tests/test_trace_retention_and_reset.py` | `runtime/deps.py`、`routes/maintenance_ops.py`、`ARCH-09-测试治理清单.md` | ChatGPT 5.6Sol审查意见：清理、保留和查看最好分别验收，避免一个阶段失败时无法判断具体风险。<br>**已落地**：`prune` 与 `TraceStore` 都放在 `observability/sink.py`（与写入共用同一份"哪个文件属于我们"的判断，否则清理迟早漏一类产物）；另改 `observability/{trace,__init__}.py`、`.gitignore`、`tests/{module_map,test_deps_stub_reachability,test_reset_transactionality,test_agent_tool_schema_contract}.py`。三组验收分别独立：上限与索引、清理闭环与恢复回调、CLI 转义与只读。 |

## 附录 B：一次典型 turn 的 trace 预期形态

```
turn_start   /chat            source=chat msg_len=42 external_models_enabled=true
model_call   health_statement ok=true dur_ms=610 total_tokens=88
gate         soreness         saved_count=1 regions=[腿部] levels=[sore]
gate         health_risk      level=caution labels=[soreness] blocks_plan=false
model_call   chat_intent_router ok=true dur_ms=812 create_training_plan=true
gate         plan_context     decision=override_today effective_subject=胸推
react_init   agent            model=deepseek-chat tools=12 max_steps=15
react_step   agent            step=1 tool_calls=2 total_tokens=12406
tool_call    query_daily_records args_keys=[date,limit]
tool_result  query_daily_records status=success dur_ms=8 result_bytes=1932
react_step   agent            step=2 tool_calls=1 total_tokens=15880
model_call   plan_goal_validator ok=true passed=false missing=[胸推]
gate         plan_validation  violations=1 expected_subject=胸推
gate         auto_correction  attempted=true
react_init   correction_agent model=deepseek-chat
react_step   correction_agent step=1 tool_calls=1 total_tokens=9204
gate         auto_correction  passed=true final_violations=0
gate         chat_response    source=agent status_code=200 artifact_type=training_plan
turn_end     /chat            dur_ms=41230 total_tokens=38578 cost_estimate=0.0121
```

ChatGPT 5.6Sol审查意见：典型 trace 的事件顺序能覆盖路由、闸门、模型、工具、修正和结束汇总；落地时应明确哪些事件是必选、哪些可选，并用 schema_version、call_id 和完整性标志支持版本演进与异常中断。
