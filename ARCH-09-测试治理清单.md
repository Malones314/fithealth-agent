# ARCH-09 测试治理清单

日期：2026-08-26

## 已改为行为断言

| 测试 | 处理 |
|---|---|
| `test_durable_writes_and_profile_locking.py::StoreWritePathsAreDurableTest` | 覆盖 DailyRecord、Plan、Profile、ExternalSettings、InfoStore、HRStream、PendingWorkout 的真实写入，并在运行时记录 `os.fsync`；删除 `"atomic_write_json" or "os.fsync"` 假绿。 |
| `test_durable_writes_and_profile_locking.py::AtomicWriteDurabilityTest` | 保留落盘顺序、临时名唯一性、失败不破坏原文件的行为断言。 |
| `test_json_store_locking.py::test_store_instances_preserve_concurrent_writes` | 从串行两次写改为 6 个线程同步起跑，验证无死锁、无丢写。 |
| `test_extended_health_fields.py::BackfillSafetyBehaviorTest` | 真实调用回灌脚本 `main()`；验证恢复点先于删除，且重导失败时仍有恢复点；验证 dry-run 零写入。 |
| `test_extended_health_fields.py` 的强度、代谢、睡眠、心率用例 | 使用内存 FIT 消息与临时 SQLite，直接验证解析和聚合结果。 |
| `test_view_intent_stress_tz_and_trend_dedup.py::ViewIntentDoesNotSwallowQuestionsTest` | 走真实 `/chat`，验证 artifact、正文回答、酸痛回执同时存在；取代 `pending_view_*` 字符串断言。 |
| `test_session_intro_muscle_recovery.py::test_garmin_hours_are_request_scoped_and_not_persisted` | 注入非空恢复快照，明确断言 fixture 不为空，消除空字典/空循环假绿。 |
| `test_session_intro_muscle_recovery.py::test_stage_three_feeds_garmin_value_into_chat` | 走真实 `/chat` 并观测 `_current_recovery_snapshot(8.0)`，不再读取 `main.py`。 |
| `test_session_intro.py` | 使用真实 `resolve_confirmed_memory_facts`，有效期改为相对当天，确保过期过滤真实执行。 |
| `test_muscle_recovery.py::MuscleRecoveryTest` | 用户 `data/daily_records.json` 改为固定训练 fixture；用户编辑真实记录不再让测试漂移。 |
| `test_reset_transactionality.py::ResetEndpointTest` | 已显式替换并恢复 `workout_store` 及 `_PERSIST_PATH`，与进程级临时目录形成双层防护。 |
| `test_sleep_session_fields.py` | 保留真实 Garmin 真值测试；缺数据时整模块 `skipif`，不退化为空断言。 |

## 已改为 AST 断言

| 测试 | 处理 |
|---|---|
| `test_durable_writes_and_profile_locking.py::SourceInvariantTest` | 用 `ast.With`、调用节点与关键字参数验证锁边界和 merge 接线，不比较反编译字符串。 |
| `test_journal_backup_and_restore_safety.py::StructuralInvariantTest` | 用调用节点验证 writable gate、恢复锁和 reload 路径。 |
| `test_reset_transactionality.py::StructuralInvariantTest` | 用调用节点顺序验证“恢复点 → reset steps”，用 AST 验证逐项异常处理和清理顺序。 |
| `test_view_intent_stress_tz_and_trend_dedup.py::test_chat_has_no_direct_json_response_outside_its_builder` | 保留 AST 不变量：`chat` 内不得裸调 `JSONResponse`。 |
| `test_extended_health_fields.py::StructuralInvariantTest` | 仅保留无法由公开行为表达的逆向字段范围常量结构。 |

## 已删

| 原测试组 | 原因 |
|---|---|
| `test_extended_health_fields.py::SourceInvariantTest` 中 SQL、模板字段、脚本文案和源码调用顺序断言 | 已有解析/SQLite/脚本行为测试；字符串存在不能证明逻辑生效。 |
| `test_checkin_fit_and_editor_fixes.py::SourceInvariantTest` | BUG-09/10/11 已有同文件端点、store、parser 行为覆盖；整组仅守实现拼写。 |
| `test_journal_backup_and_restore_safety.py::test_main_wires_*` | 仅检查五个字符串，不能证明 middleware、异常处理和 revalidate 行为；相应行为由同文件端点测试覆盖。 |
| `test_reset_transactionality.py` 的响应字段和前端源码拼接断言 | 真实 reset 端点测试已验证 200、partial、steps、recovery point；重复源码检查无增益。 |
| `test_independent_session_history.py` | 整个文件只比较 HTML/JavaScript 字符串，既不执行历史截断也不提交退出请求；删除。 |
| `test_training_record_selection_view.py` | 整个文件只用正则截取 JavaScript；当前实现格式变化已使其误红，且没有验证一次真实交互；删除。相关后端记录与营养端点仍由独立行为测试覆盖。 |
| `test_bug28_bug46.py`、`test_f2_features.py`、`test_fifth_batch.py` | 均为批次验收时留下的源码拼写清单，没有执行端点或前端行为；删除。对应后端行为由按领域组织的测试覆盖。 |
| `test_plan_save_endpoint_contract.py`、`test_workout_discard_button.py` | 只截取 JavaScript 并比较字符串；删除，避免格式调整造成误红。 |

## 保留并说明理由

| 类型 | 理由 |
|---|---|
| Dockerfile、Compose、requirements、README 契约 | 这些文件本身就是部署/依赖声明，文本或 YAML 结构是对外契约，不是 Python 实现细节。 |
| Prompt 文案契约 | 模型提示词本身就是运行时行为输入，关键语义的文本断言属于产品契约。 |
| 少量旧前端接线断言 | 浏览器行为基础设施尚未覆盖全部旧功能，暂列显式债务基线；只能减少，不能新增。完整名单在 `test_arch09_test_governance.py::LEGACY_SOURCE_ASSERTION_TESTS`。 |
| 真实 Garmin 睡眠数据 | 五天 CSV 真值无法用合成数据替代；通过 `FITHEALTH_REAL_DATA_DIR` 和模块级 `skipif` 明确管理。 |
| 固定历史日期 fixture | 日期作为解析/时区/周几输入，均显式传入被测函数，不依赖墙钟，不属于定时炸弹。 |

## 两处假绿结论

1. `atomic_write_json or os.fsync`：确属假绿。短路条件只要求任一字符串出现，注释、导入或未调用 helper 都能通过。现已由七条真实写入的 fsync 观测替代。
2. `pending_view_artifact` / `pending_reply_prefix`：历史版本中这两个名字只存在于测试，断言没有 skip、条件分支或异常吞噬；该测试若被实际收集必然失败。因此“全仓不存在但仍绿”的原因是当时运行集合未包含该测试文件（或查看的是另一工作树/旧报告），不是 `assertIn` 自身空转。现已由三个 `/chat` 行为用例替代，并保留单一出口 AST 守卫。

## 防回退

`test_arch09_test_governance.py` 通过 AST 和轻量数据流追踪识别：从 `.py/.html` 读取文本后再做 `assertIn`、`assertNotIn`、`assertRegex` 或等价布尔包含判断。遗留测试按“文件::测试名”列入固定基线；新增测试不在基线中，因此一旦使用源码文本断言会直接失败。基线存在已清理项时也会失败，迫使同步删除豁免。

### 2026-08-28 更新：main.py 拆分阶段 0 对守门器的两处补强

拆分把 `(REPO_ROOT / "main.py").read_text()` 收敛成了 `module_source(consumer_home(...))`（见 `tests/source_tools.py` 与 `tests/module_map.py`），测试文件里不再出现 `.read_text` 调用。若不同步更新，守门器会静默失去对那 25 个文件的覆盖，`LEGACY_SOURCE_ASSERTION_TESTS` 基线跟着一起假绿。因此：

1. `SOURCE_HELPERS` 加入 `module_source`、`module_tree`、`function_body_source`。**`function_node` 刻意不加**——拿到 AST 节点做结构断言是本清单允许的，只有 `ast.unparse` 回文本再做字符串匹配才算源码文本断言，那一步由既有的 `unparse` 规则识别。
2. 断言参数改为既检查“被污染的名字”，也检查**内联的源码读取**（`assertIn(x, module_source(...))` / `assertIn(x, ast.unparse(node))`）。此前内联写法没有赋值语句可供污染传播，是一条绕过守门器的暗道。

补强后新暴露出三条**既有**债务（不是新增的），已按原样列入基线：`test_profile_update_confirmation.py` 的两条与 `test_saved_training_record_editing.py::test_endpoint_feeds_the_sidecar_stream_into_the_merge`，三者都是 `assertIn(..., ast.unparse(node))`。债务没有变多，是检测变准了。

`test_arch09_test_governance.py::test_guard_detects_a_source_assertion_routed_through_the_module_map` 专门钉住第 1 条：`SOURCE_HELPERS` 名单漏项时它先红。

### 2026-08-29 更新：module_map.py 是拆分后的测试寻址层

`tests/module_map.py` 不是兼容导入表，而是源码级测试的唯一寻址声明。四组数据
各自解决不同问题，不能合并：

1. `FUNCTION_HOME` 声明函数或常量的定义模块，供单函数 AST 提取与隔离执行使用。
2. `CONSUMER_HOME` 声明跨函数接线不变量所在的消费者；函数搬走后，调用顺序、
   缓存优先级和事务边界断言必须跟着消费者走。
3. `IMPLEMENTATION_MODULES` 是缺席断言的完整扫描范围，防止“只看 main.py”
   在符号迁移后静默变成永真。
4. `DEPS_CONSUMERS` 只列实际通过 `deps.X` 读取可替换依赖的模块，用于禁止同名
   影子绑定和验证桩可达性；`deps.py` 自身和不再读取 deps 的最终 `main.py`
   都不属于消费者。

`test_module_map.py` 验证路径、符号和 import 名称没有漂移；`test_main_facade.py`
额外限制入口文件不超过 200 行、只保留装配函数，并禁止生产包反向 `import main`。
新增源码级测试时应先判断它需要的是“定义位置”还是“消费者位置”，不得重新硬编码
`REPO_ROOT / "main.py"`。

### 2026-09-04 更新：agent-trace 阶段 0-1 的新增测试

三个新文件，**全部是行为断言**，一条源码文本断言都没有，因此不进
`LEGACY_SOURCE_ASSERTION_TESTS` 基线。

| 文件 | 断言方式 |
|---|---|
| `test_agent_tool_schema_contract.py` | 构造真实 agent，比对 `tests/baseline/agent_snapshot.json`（14 个工具的完整 schema + 整份 51 字段配置 dump + digest）。落盘不变量在正常、未运行、崩溃、中断四条路径上各验一次。忽略规则问 `git check-ignore` 的**实际结果**，不读 `.gitignore` 文本。 |
| `test_trace_infrastructure.py` | 全部对 `fithealth_agent.observability` 的行为断言：默认关闭零副作用、`turn_end` 恰好一条（ok/error/aborted 三态）、失败隔离与告警限流、ContextVar 跨 `run_in_threadpool` 传播、`meta` 级别的字段级反向脱敏断言（含 Unicode 与 emoji 哨兵串）。 |
| `test_reset_transactionality.py`（新增一条） | `test_reset_deletes_the_tool_output_overflow_files` 走真实 `/data/reset`，验证溢出文件被删而无关文件保留。 |

两处既有断言的**计数**随之更新，这是契约本身在变：`_reset_steps()` 从 10 步变 11
步（新增 `tool_output_removed`），失败一步时的成功数从 9 变 10。

### 2026-09-04 更新：agent-trace 阶段 2 的 HTTP 边界测试

`test_trace_http_boundary.py` 用一个**只挂三个 router 的最小 app**（不 import
`main`，避开"谁先 import 谁定下数据目录"那个坑）验证四组不变量：

| 断言 | 方式 |
|---|---|
| 接入清单与实现一致 | `observability/http.py` 的 `TRACED_ROUTES` 逐条发真实请求；同模块的 `/upload_fit`、`/upload_plan`、`/upload_health` 作对照组，必须零回合。白名单加了路由但没补发送方式时，`test_every_whitelisted_route_has_a_case_here` 先红。 |
| `turn_end` 恰好一条且三态可分 | 成功 → `ok`；`RuntimeError` → `error`；`asyncio.CancelledError` → `aborted`。都是真实请求，不是直接调 `start_turn`。 |
| 响应序列化失败在回合内 | 让 workflow 返回含 `object()` 的 body，`JSONResponse.__init__` 渲染时抛 `TypeError`，断言它记成 `turn_end.status="error"`。这条钉住"`JSONResponse` 必须构造在 `with` 之内"。 |
| 排除路径确实不开回合 | 413（超大 body）、415、空 body、坏 JSON、缺 `message` 五种请求各验一次。 |

`RouteStructureTest` 是**纯 AST 结构断言**：提取 `with start_turn(...)` 块内的调用名
集合，用 `assertEqual` 比对（不 unparse 回文本，也不用 `assertIn`/`assertTrue`，
因此不触发守门器）。它钉住三件事：workflow 调用与 `JSONResponse` 构造在回合内、
`context_error_response` 刻意在回合外、三个 `/upload_*` 端点完全没有回合块。

`observability/http.py` 与 `TRACED_ROUTES` 已登记进 `tests/module_map.py`。

### 2026-09-05 更新：agent-trace 阶段 3 的闸门覆盖测试

`test_trace_decision_coverage.py` 分两层，**没有一条源码文本断言**：

| 层 | 断言 | 方式 |
|---|---|---|
| 枚举层 | `chat()` 里所有 `return` 都走 `_chat_response` 这唯一出口 | AST：遍历 `ast.Return`，比对调用名；`_chat_response` 自己那条 `return ChatResult(...)` 排除在外。用 `assertEqual` 比对**越界行号列表**，不 unparse 回文本。 |
| 枚举层 | 17 个 `source` 的全集冻住 | AST 抽取 `source=` 字面量与 `pending_view_source` 的赋值常量，与 `EXPECTED_SOURCES` 用 `assertEqual` 比。新增分支时必须显式过一遍。 |
| 枚举层 | 唯一出口里真的有 `trace_event("gate", "chat_response")` | AST：否则上面两条都是空转。 |
| 行为层 | 三条典型路径的**闸门顺序** | 真实 `/chat`：急症拦截 → `[soreness, health_risk, chat_response]`；安全冲突 409 → `[…, plan_context, chat_response]`；自动修正通过 → `[health_risk, plan_context, plan_validation, auto_correction, chat_response]`。顺序本身就是结论，所以断整个列表而不是"包含"。 |
| 行为层 | 脱敏在闸门上真的生效 | 断言 `blocking_reasons` / `active_safety_constraints` 的**原文不在** payload 里，只有 `_count` 与 `_hmac12`。 |
| 行为层 | trace 故障不改变响应 | 让 `TurnTrace.add` 只对 `kind="gate"` 抛异常，比对同一请求的响应体逐字段相同（剥掉酸痛记录的随机 `id` 与时间戳），并断言 `turn_end.trace_errors > 0`——降级必须可见。 |

**这一层抓到过一个真缺陷**：`chat_workflow.py` 的 `ContextInputError` 分支原先
`return context_error_response(exc)` 绕过唯一出口，而那个函数**在本模块根本没有
导入**——`NameError` 被外层 `except Exception` 吞掉，用户拿到一句无用的 500 而不是
"上下文过长，请缩短消息或训练计划"。该分支现已改走 `_chat_response`，
`test_all_returns_go_through_the_single_exit` 钉住它不再复发。

`store_write` 事件的 `namespace`/`key` 刻意用**复数列表**（`namespaces`/`keys`）：
一次写入可能带多条事实，不同分支一个传 `str` 一个传 `list` 就是"同名异类型"。

### 2026-09-05 更新：agent-trace 阶段 4 的模型触点测试

`test_trace_model_calls.py` 的核心是**静态完整性**——这是审查意见"清单完整性应由
测试或静态检查自动校验"的落地：

| 断言 | 方式 |
|---|---|
| 触点清单冻结 | AST 扫描全包，找出**函数体里出现 `/chat/completions` 字面量**的函数（f-string 的常量片段也算），与 `EXPECTED_TOUCHPOINTS` 用 `assertEqual` 比。判据用端点而不是 `requests.post`，因为 `youtube_search.py` 也发 HTTP 但那不是模型调用。 |
| 每个触点都在 `model_call` 块内 | AST：函数内的调用名集合必须含 `model_call`。 |
| span 双向对齐 schema | 源码里出现的 span 必须都在 `MODEL_CALL_SPANS` 里（打错字会让事件带 `_unknown_span`，按 span 分组的统计就会漏掉它），反向也要成立（schema 里注册了但没有调用点＝清单过期）。 |
| 一次调用恰好一条事件 | 7 个触点的失败路径逐个跑，断言 `len(calls) == 1` 且 `call_id` 唯一。这条直接钉住审查意见的头号关切：装饰器 + `except` 双记录会让任何按 span 的计数翻倍。 |
| `ok` 三态 + 跳过优先级 | 关联网模型→`None/external_models_disabled`；缺 key→`None/no_api_key`；两者同时成立时只记优先级更高的那个。 |
| 结构解析失败 ≠ 调用失败 | HTTP 200 但内容不合约定 → `ok=False, fallback_reason="unparseable_json", http_status=200` 且**没有** `error_type`。 |
| 回落契约不变 | 每个触点跑两遍——一遍在回合外、一遍在回合内——断言返回值 `assertEqual` 相同。`analyze_food_image` 是抛异常而非回落，单独断言异常类型不变。 |
| 不泄露 | 断言落盘全文里没有 API key、没有用户原话、没有异常消息、没有 `/chat/completions` 路径、没有 `Bearer`；同时断言 `endpoint_host` **有**主机名（分得清打到哪个供应商，且不含路径与参数）。 |

**这个静态检查在落地当天就抓到了计划漏掉的第 7 个触点**：
`plan_classifier._level2_llm_check`（判断上传的 Markdown 是不是训练计划，走
`/upload_plan`）。计划书按函数名 grep（`def route_`/`def classify_`…）所以漏了它。
连带发现阶段 2 的排除表把 `/upload_plan` 写成"不碰外部模型"是错的，该路由已移入
`TRACED_ROUTES`，`test_trace_http_boundary.py` 的对照组同步缩到两条。

`test_external_model_controls.py` 新增 `HealthStatementGateTest` 三条，为 trace 照出来
的两个生产缺陷补回归：

| 断言 | 方式 |
|---|---|
| 关掉开关后不发请求 | 注入 `Mock` requester，断言 `assert_not_called()`；并断言返回 `None`（"不确定"）而不是 `False`——`False` 会让调用方跳过确定性的本地风险筛查。 |
| 调用点真的传了开关 | AST：光有参数不够，`chat_workflow` 不传就等于没有闸门。用 `assertEqual` 比对那次调用的关键字参数列表。 |
| `max_tokens` 留得下完整 JSON | 注入一个记录请求体后抛异常的 requester，断言 `max_tokens >= 200`。原值 40 会截断响应，使该分类器几乎必然解析失败。 |

### 2026-09-05 更新：agent-trace 阶段 5 的 ReAct sink 测试

`test_trace_react_sink.py` 45 条，分五层。第一层是**对第三方框架的静态契约检查**——
本仓库此前没有这一类，值得单独说明：

| 层 | 断言 | 方式 |
|---|---|---|
| 框架契约 | 框架发的每一类事件都有归属 | AST 扫 `hello_agents/**/*.py`，取所有 `self.trace_logger.log_event("…")` 的字面量，与 `TRANSLATED_EVENTS ∪ IGNORED_EVENTS ∪ KNOWN_UNMAPPED` 用 `assertEqual` 比。只认 `self.trace_logger`（`simple_agent.py` 用的是自己 new 的局部实例，我们的 sink 装不上去）。框架升级新增一类事件 → 先红。 |
| 框架契约 | sink 实现了框架会读的每个成员 | 同一次扫描收集 `self.trace_logger.<member>`，断言它们都在 `dir(TurnTraceSink)` 里。鸭子类型没有编译期检查，这是唯一的替代。 |
| 框架契约 | 每个翻译函数真的产出它声明的 kind | 按事件逐个跑最小合法 payload，断言落出来的 kind 与 `TRANSLATED_EVENTS` 一致。声明与实现挨着写也会漂移。 |
| 框架纠错 | LLM 故障不被记成超步数 | 四条：故障→`status=llm_error` 且 `framework_status=timeout`（纠正留痕）；真超步数→`status=timeout` 且不留 `framework_status`（纠正不能反过来误伤）；`max_steps_reached` 按 `(total_steps, max_steps)` 三组边界值算而不是抄框架 status；拿不到 `max_steps` 时退化为看纠正后的 status。 |
| 翻译 | 每个事件的字段逐项比对 | 全部**从落盘文件读回来**，不看内存中间态。含"框架硬编码的 `cost: 0.0` 不进 payload""0 token 记成 unknown 而不是 0""异常 message 一个字都不落"。 |
| 翻译 | 补上框架缺的 `status` | 框架只在内置工具分支写 `status`，用户工具分支连字段都没有，成败只在结果文本的 ❌/⚠️ 前缀里。三种前缀各一条用例。 |
| 共享回合 | 两个循环一个 turn_id、一个文件 | `read_turn` 自带"恰好一个文件"的断言；再按 seq 断言 role 序列是 `agent×7 + correction_agent×7`。 |
| 可复现 | `react_init` 的字段集合 | 用集合差断言"少了哪些字段就重放不了"，而不是逐个 `assertIn`。摘要摘的是**静态**提示词（拼上时间锚点的那份每秒一变），并断言两个 agent 摘出同一个值。 |
| 用量 | `summarise_usage` 的六条口径 | 纯函数直接测：跳过的调用不计入分母、缺用量记 `usage_missing`、无价格表 `cost_basis="unknown"`、无法定价的 token 计入 `cost_unpriced_tokens`。另有一条走真实回合，断言调用方**改不动**这些派生值（`set_turn_result` 传同名字段被拒且 `trace_errors > 0`）。 |
| 端到端 | 真实 `/chat` 一条链读下来 | 假 agent 发的是框架同步 `run` 的真实事件序列，并经 `attach_react_trace` 装真实 sink。断言 `sum(section_lens) == total_len`、`agent_input.total_len == context_budget.total_len`、自动修正路径下两个 role 的 `react_init`/`react_end` 各一条。 |
| 隔离 | sink 自己坏掉不打断主循环 | `__str__` 就抛的对象、`None` payload、非 dict payload、什么属性都没有的裸 agent，四种情况各一条；断言业务不受影响且 `trace_errors` 计数可见。 |

**一处既有断言随契约更新**：`test_trace_decision_coverage.py` 的自动修正路径闸门序列
从 5 项变 6 项——`gate/context_budget` 从阶段 5 起**两种结局都记**（accepted /
rejected）并带各段长度。只在被拒时记等于只在最坏情况下有数据，而"记忆有没有进上下文"
恰恰要在正常回合里回答。

**两处测试改动是契约本身在变**，不是迁就实现：`test_package_lazy_import.py` 的假工厂
要接受 `role`（公开工厂的签名变了），`test_training_plan_artifact_routing.py` 里
`correction_agent` 那条 `assertEqual` 要带上 `role='correction_agent'`（少传这个实参
就等于修正循环的事件混进主循环）。

`tests/agent_snapshot.py` 的沙箱另外**显式关掉 `FITHEALTH_TRACE`**：`trace_logger_type`
现在随环境而变，不关的话谁的 shell 里开着 trace，重新生成的基线就会带上
`"TurnTraceSink"`——而那不是契约的一部分。

新模块 `observability/react_trace.py`、`observability/cost.py` 与
`fithealth_agent/agent.py` 已登记进 `tests/module_map.py`（`IMPLEMENTATION_MODULES`
+ `FUNCTION_HOME` 8 个符号），`tests/test_module_map.py` 的导入名表同步。

### 2026-09-05 更新：阶段 5 附带修复的回归测试

同一个框架缺陷的另一半——框架把 LLM 异常**吞掉**而不重抛，于是模型服务故障显示成
200 加一句"任务太复杂"——修在 `agent.TrackedLLM` + `chat_workflow` 的两处检查上。
新增 `test_model_failure_surfacing.py` 10 条，分三层：

| 层 | 断言 | 方式 |
|---|---|---|
| 记录器 | 记下并**原样抛出**，成功调用不留痕 | 打桩 `llm._adapter.invoke_with_tools`，断言 `last_failure is exc` 且异常照旧传播。另有一条断言工厂真的用了 `TrackedLLM`——少了它，有人换回 `HelloAgentsLLM` 时 503 会静默失效。 |
| 记录器 | 只覆盖同步入口就够了 | 框架的 `ainvoke_with_tools` 只是把同步版丢进 executor（`core/llm.py:269-282`）。用 `asyncio.run` 走一遍异步入口，断言同样被记下——**钉住这条委派关系**，框架改成独立实现时先红。 |
| 框架行为 | 钉住我们绕开的那个行为本身 | 用**真实** agent + 打桩 adapter 跑 `agent.run`，断言它返回框架那句"抱歉，我无法在限定步数内完成这个任务。"而不是抛异常。这条的价值在将来：框架哪天改成重抛，它会红，届时绕行代码可以删掉而不是留着当谜题。 |
| 端到端 | 故障 → 503、正常 → 200、修正循环故障 → `call_failed` | 真实 `/chat`。修正循环那条断言 `plan_validation.violations` 末尾是"自动修正服务调用失败"而不是"结果不是完整训练计划"——前者查模型服务，后者改提示词，处理人不同。 |
| 端到端 | **不匹配框架的中文文案** | 让假 agent 在有故障的同时返回一句别的话，仍然 503。文案是框架的实现细节，随版本会变，绑上去就是给自己埋雷。 |

`TrackedLLM` 与 `swallowed_model_failure` 一并登记进 `FUNCTION_HOME`：`chat_workflow`
按名字取后者，改名的症状是故障重新静默成 200。

### 2026-09-05 更新：agent-trace 阶段 6 的保留、清理与查看

`test_trace_retention_and_reset.py` 43 条，五组。全部对着**盘上的真实文件**断言，
不看内存态——保留策略与清理的失效方式就是"以为删了其实没删"。

| 组 | 断言 | 方式 |
|---|---|---|
| 三条上限 | 各自生效 + 边界值 + 组合 | 回合数 / 天数 / 总字节各一条；另有"**恰好**等于上限时一个都不删"（差一个 off-by-one 就会每回合删掉最旧那个）；组合那条造 4 个回合让三条规则互相重叠，断言重叠不导致重复删或漏删。 |
| 三条上限 | 永不删最新那个回合 | 单个回合本身就超字节上限时保留它并置 `over_budget`，同时断言那条告警能被检索到。把刚出问题的那次对话抹掉是最坏的结果。 |
| index | 与盘上文件一致 | 只保留"回合文件还在"的行，`file: null` 的陈旧行一起丢；**没删东西时不重写**（O(N) 原子写，prune 每回合都跑）；半行 JSON 跳过而不是整份索引作废。 |
| 失败降级 | 清理失败不影响主流程 | 锁超时 / 只读目录注入 `PermissionError`（Windows 上 chmod 对目录无效，所以直接打桩），断言不抛、不删、有告警，**且权限恢复后的下一次 prune 照常工作**；另有"一个文件删不掉不影响其余"。 |
| 清理闭环 | 删净、幂等、不误伤 | 断言 `index.jsonl.lock` 与 `.tmp` 残片真的被造出来又被删掉（这两类最容易漏）；`notes.md` 与非日期目录必须留着；软链目录直接拒绝；`clear` 在 trace **关闭**时同样有效——否则"删除全部数据"会漏一整个目录。 |
| 恢复备份 | 顺序与"绝不抛" | `on_restored` 的第一个必须仍是 `info_store.revalidate`（`import_backup` 按**位置**取 `callback_results[0]`）；回调写成函数而不是绑定方法，所以替换 `deps.trace_store` 有效；真实 restore 之后 trace 为空；把 `_unlink_matching` 打成抛异常，断言恢复**照样成功**且回调返回 0。 |
| CLI | 转义 | 判据是"渲染出的文本里没有能改变结构的字符"——一条同时踩标签闭合、属性闭合、危险 URL、控制字符的 payload，断言 `<`、`>`、两种引号一个都不许原样出现。这比逐个 payload 断言强。另有双重编码（`&lt;` → `&amp;lt;`）、BiDi 覆写与 C0 换成可见记号、`\t`/`\n` 保留。不可见字符在用例里用 `chr()` 拼，不写字面量：写进源码就看不见，别的编辑器还会"顺手修好"它们。 |
| CLI | 自包含 + 只读 | 报告里不许出现 `<script`/`src=`/`href=`/`url(`/`@import`/`<iframe`，且带 `default-src 'none'`；三条子命令跑完，trace 目录的文件名、大小、mtime 快照必须逐字节相同。 |
| CLI | 输入输出边界 | `--turn` 只收回合 id（`../../etc/passwd`、`index.jsonl`、绝对路径全部拒绝）；报告不覆盖已有文件（除非 `--force`）、不许写进 trace 目录；`--since` 只认 `Nd`/`Nh`。 |

**`/data/reset` 的端到端断言留在 `test_reset_transactionality.py`**（那里已有真实端点、
临时数据目录与恢复点 fixture，`test_reset_deletes_the_tool_output_overflow_files` 就是
同型的先例）：新增 `test_reset_deletes_the_agent_traces` 验证响应体里有 `traces_removed`、
文件真的没了、**第二次 reset 幂等**。三处既有计数随契约更新：步骤数 11 → 12，
失败一步时的成功数 10 → 11，最后一项断言改成"最后两项是两类诊断产物"。

`trace_store` 进 `test_deps_stub_reachability.py` 的可替换依赖清单（22 个符号）。
`.gitignore` 补 `data/traces/` 与 `data/recovery-points/`，并在
`test_agent_tool_schema_contract.py` 里加一条**问 git 实际结果**的断言——后者是既有
缺口：恢复点是完整数据快照 zip，而 `data/*.json` 那类 glob 拦不住子目录里的 `.zip`。






新模块已登记进 `tests/module_map.py`：`tool_output.py`、`agent.py` 与 `observability/`
下的 9 个模块进 `IMPLEMENTATION_MODULES`，可观测性相关的 40 个符号进 `FUNCTION_HOME`。
理由与拆分同一条：各阶段的调用点按名字接线，改名或换模块的症状是
静默失效。`tests/test_module_map.py` 的 `expected` 导入名表必须同步。

`tests/__init__.py` 另外显式设 `FITHEALTH_TRACE=off` 与
`FITHEALTH_TRACE_DIR=<临时数据目录>/traces`：`off` 本来就是默认值，写出来是防
开发机环境变量——谁的 shell 里开着 trace，整套测试就会一边跑一边往盘上写健康
数据，而且断言全绿。

