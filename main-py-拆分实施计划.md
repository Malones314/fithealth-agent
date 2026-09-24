# 实施计划：拆分 main.py 的路由与工作流

**目标**：把 5402 行的 `main.py` 拆成职责清晰的多个模块，降低单文件复杂度，同时**不改变任何对外行为**（HTTP 契约、响应字段、副作用顺序全部保持不变）。

**状态**：计划稿，未动代码。
**日期**：2026-08-27
**约束**：个人自用项目，不做认证/CORS/部署加固；重心在数据安全与可维护性。

---

## 0. 现状盘点

`main.py` 5402 行，是全仓最大文件（第二名 `health_store.py` 1900 行）。里面混了六类东西：

| 类别 | 大致行段 | 体量 |
|---|---|---|
| 依赖导入 + 全局 store 实例化 + 常量 | 1–190 | ~190 |
| 中间件与异常处理器（maintenance guard、两个 DegradedError handler） | 192–267 | ~75 |
| 纯函数工具层（计划校验、意图正则、档案合并、记忆格式化、恢复上下文、日期解析…） | 297–2000 | ~1700 |
| `/chat` 单个处理函数 | 2001–2739 | **~740** |
| `/logout` + 上传类端点（fit / plan / health） | 2740–3330 | ~590 |
| 数据 CRUD 端点（训练/营养/计划/记忆/健康/备份/重置…） | 3331–5402 | ~2070 |

三个真正的痛点：

1. **`/chat` 一个函数 740 行**，把意图路由、安全闸、记忆候选、计划上下文、Agent 调用、artifact 组装全塞在一起。
2. **~1700 行纯函数散落在端点之间**，没有归类，改一处要通读全文件。
3. **CRUD 端点 2070 行**按资源交错排列，没有边界。

### 全局单例清单（拆分时必须共享同一实例）

`profile_store`、`daily_record_store`、`info_store`、`external_model_settings_store`、`soreness_store`、`plan_store`、`plan_draft_cache`、`health_store`、`hr_stream_store`、`health_import_service`、`backup_service`、`app`、`logger`。

这些目前是 `main.py` 的模块级变量。多个新模块都要用到它们，如果各自 `import main` 会形成循环导入。

---

## 1. 两条硬约束（决定整个方案的形状）

### 约束 A：测试大量 `monkeypatch.setattr(main, "X", ...)`

统计到的打桩目标：`_current_recovery_snapshot`(10)、`soreness_store`(9)、`classify_user_health_statement`(7)、`route_information`(6)、`create_fithealth_agent`(6)、`route_chat_intent`(5)、`info_store`(4)、`build_current_week_reply`(4)、`validate_generated_training_plan`(2)、`looks_like_complete_training_plan`(2)、`inspect_fit_source`(2)、`analyze_food_image`(2)、`_immediate_memory_candidate`(2)、`_acute_injury_memory_candidate`(2)，以及 `parse_fit_file`、`extract_activity_fits`、`parse_soreness_reply`、`scheduled_weekly_entry_for_message` 各 1 处。

**关键陷阱**：Python 的 `from x import f` 会把 `f` 绑成新模块的局部名字。如果把 `chat` 搬到 `routes/chat.py`，而它内部用 `from fithealth_agent.chat_intent_router import route_chat_intent`，那么 `monkeypatch.setattr(main, "route_chat_intent", fake)` **会静默失效**——测试变绿但打桩没生效，这是最危险的一类回归。

**对策**：被打桩的符号，其**定义模块**与**使用模块**必须是同一个模块，且使用处走实际依赖模块的属性访问，而不是通过 `from x import f` 捕获局部别名。`deps.py` 只承载共享实例、配置常量和真正的外部依赖适配；仍定义在 `main.py` 的本地辅助函数不得在阶段 1 被提前“再导出”，必须随消费者一起迁移。

### 约束 B：约 15 个测试文件直接读取 `main.py` 源码

两种用法：

- **AST 抽函数执行**：`test_plan_date_context.py`、`test_multi_zip_activity_import.py`、`test_local_fallback_and_set_counts.py`、`test_context_memory.py`、`test_plan_draft_integrity.py` 等，用 `MAIN_SOURCE = (REPO_ROOT / "main.py").read_text()` 再 `ast` 抽单个函数在合成命名空间里执行。注释写明理由：避免 `import main` 触发整条 LLM 依赖链（ARCH-02）。
- **AST 结构不变量断言**：`test_durable_writes_and_profile_locking.py::SourceInvariantTest`、`test_journal_backup_and_restore_safety.py::StructuralInvariantTest`、`test_reset_transactionality.py::StructuralInvariantTest`、`test_view_intent_stress_tz_and_trend_dedup.py::test_chat_has_no_direct_json_response_outside_its_builder`。这些是 ARCH-09 治理后**刻意保留**的资产，不能删，只能改指向。

**对策**：第 0 阶段先做一层测试基础设施改造，把"从 main.py 读源码"抽象成"从指定模块读源码"，之后每个搬迁阶段只改一个常量。

---

## 2. 目标模块结构

```
main.py                      # 瘦身为 ~150 行：装配 + 门面再导出
fithealth_agent/
  runtime/
    __init__.py
    deps.py                  # 全局单例 + 可打桩的外部依赖引用
    middleware.py            # maintenance guard + 异常处理器
    responses.py             # JSONResponse 统一构造器、context_error_response
  domain/
    plan_validation.py       # validate_generated_training_plan 等计划校验族
    plan_context.py          # resolve_plan_context / format_plan_context / 日期解析族
    intent_rules.py          # is_*_query / navigation_only_message 等只读兜底正则
    profile_rules.py         # validate_profile_tool_updates / merge_* / preview / summary
    memory_view.py           # format_cross_session_memories / confirmed_* / 记忆候选族
    recovery_view.py         # _recovery_context_payload / _format_muscle_recovery_lines 等
    record_view.py           # _training_record_items / _nutrition_record_items / _record_overview
    segment_merge.py         # _merged_segment_hr / _merged_segment_numbers / _merge_saved_*
  workflows/
    chat_workflow.py         # /chat 的编排逻辑（拆成 6~8 个阶段函数）
    logout_workflow.py       # /logout 的记忆落盘编排
    upload_workflow.py       # fit / plan / health 三条上传的编排
  routes/
    chat.py  uploads.py  workout_state.py  records.py
    plans.py  memories.py  health.py  settings.py  maintenance_ops.py
```

分层规则（单向依赖，禁止反向）：

```
routes  →  workflows  →  domain  →  runtime.deps  →  fithealth_agent/*
```

- `domain/` 只放**纯函数**，不 import `deps`，不碰 FastAPI，输入输出都是普通数据；需要读取 store 的查询/编排函数放在 workflow 或 query service。这是最容易搬、最容易测的一层，优先动它。
- `workflows/` 允许用 `deps`，但不返回 `JSONResponse`，只返回数据字典。
- `routes/` 只做「解析请求 → 调 workflow → 包成 JSONResponse」，单个函数控制在 40 行内。

### `deps.py` 的具体形态（解决约束 A）

```python
# fithealth_agent/runtime/deps.py —— 示意，非最终代码
from fithealth_agent.storage import DailyRecordStore, UserProfileStore
...
profile_store = UserProfileStore()
daily_record_store = DailyRecordStore()
...
# 只再导出真正属于外部依赖层的函数；main.py 中尚未迁移的本地函数不能从这里导入
from fithealth_agent.chat_intent_router import route_chat_intent
from fithealth_agent.information_router import route_information
from fithealth_agent.health_safety import classify_user_health_statement
```

调用方一律 `from fithealth_agent.runtime import deps` 然后 `deps.route_chat_intent(...)`，属性访问在调用时才解析，打桩才生效。

而 `main.py` 保留门面：

```python
from fithealth_agent.runtime.deps import *          # 或显式再导出
from fithealth_agent.runtime import deps
route_chat_intent = deps.route_chat_intent          # 兼容旧打桩点
```

但这只兼容“读”，不兼容“写”——`monkeypatch.setattr(main, "route_chat_intent", fake)` 改的是 `main` 的属性，而 workflow 读的是 `deps` 的属性。测试应改为打 `deps`；对于随 workflow 搬迁的本地函数，则改打该 workflow 的依赖模块。**不能**通过 `from deps import soreness_store` 让消费者持有副本，否则替换 `deps.soreness_store` 不会生效。

> 决策点：也可以让 `main.py` 用 `__getattr__` + `__setattr__` 代理到 `deps`，测试一行不改。但模块级 `__setattr__` 无法拦截（模块不是普通对象，需要 `sys.modules` 替换成自定义类实例），属于黑魔法，会让后续排查困难。**建议不用，老老实实改 ~60 处打桩点。**

### 全局单例的实例化时机

`deps.py` 在 import 时实例化 store，与现状一致。注意 `tests/__init__.py` 在导入任何东西之前设置了 `FITHEALTH_DATA_DIR` 环境变量——`deps.py` 必须晚于它被导入。由于 store 的路径解析读的是环境变量，只要不在 `fithealth_agent/__init__.py` 里 eager import `deps`，这条就自然满足。**验证方式**：阶段 0 的 `test_package_lazy_import.py` 必须持续通过。

### 路由与共享状态完整性

拆分前后必须保持全部现有路由，不得只按资源表搬迁而遗漏独立端点。当前至少包括：`/analyze_food`、`/upload_health/activity`、`/data/profile/reset`，以及计划表中各资源路由。阶段 0 建立路由快照，阶段 7 用 `/openapi.json` 和 `app.routes` 同时比较 path、method、endpoint name、response class 与 operation id。

`_analysis_signing_key`、`_sign_analysis_confidence`、`_verified_analysis_confidence` 属于餐盘分析与营养保存共用的安全状态，必须由单一 runtime 模块持有；不能在 `routes/records.py` 或 `routes/uploads.py` 各自重新生成密钥。

---

## 3. 分阶段实施步骤

每个阶段都是一次可独立提交、可独立回滚的变更。**每阶段结束必须跑全量测试并且 0 失败**才进入下一阶段。

### 阶段 0：建立测试基础设施与基线（不动 main.py）— ✅ 已完成 2026-08-28

1. 记录基线：`pytest -q` 全量跑一次，当前基线为 **848 passed, 1 warning**（约 45 秒）；同时保存 JUnit XML，记录每个 node id 的 outcome。若已有失败用例，先登记，不在本次拆分中修。
2. 在 `tests/` 新建 `source_tools.py`，提供两个函数：
   - `load_function(module_path: Path, name: str, extra_consts=True) -> callable`：把现在散在多个测试文件里的 AST 抽函数逻辑收敛成一份（现有实现已经会自动收集"私有大写常量"，保留该行为）。
   - `module_tree(module_path: Path) -> ast.Module`：给结构不变量测试用。
3. 新建 `tests/module_map.py`，声明「哪个函数现在住在哪个文件」：
   ```python
   FUNCTION_HOME = {
       "requested_plan_date": REPO_ROOT / "main.py",
       "resolve_plan_context": REPO_ROOT / "main.py",
       ...
   }
   ```
   同时声明 `CONSUMER_HOME`（函数间调用顺序、缓存优先级和 AST 不变量所在的消费者模块）。单纯函数抽取使用 `FUNCTION_HOME`，跨函数结构断言使用 `CONSUMER_HOME`，不能只维护一张函数名表。
   把 `test_plan_date_context.py`、`test_multi_zip_activity_import.py`、`test_local_fallback_and_set_counts.py`、`test_context_memory.py`、`test_plan_draft_integrity.py`、`test_health_safety_gate.py`、`test_memory_fact_addressing.py`、`test_info_store_corruption_guard.py`、`test_calorie_trend_and_bmr_removal.py`、`test_fit_parser_sport_routing.py`、`test_agent_training_edit_tools.py` 等测试里的源码路径改成查表。
   > 之后每搬一个函数，只改 `module_map.py` 一行。这是整个计划里 ROI 最高的一步。
4. `test_arch09_test_governance.py:75` 里硬编码了 `"    source = Path('main.py').read_text()\n"` 这个字符串（用于检测"源码字符串断言"这种反模式），确认改造后该检测仍然生效，必要时同步更新其匹配规则。
5. 生成路由快照：记录 `app.routes` 的 path、methods、name、endpoint module，以及 `/openapi.json` 中的 operation id；后续每阶段都必须能与快照对比。
6. 验证：全量测试 node id 与基线一致，且 outcome 完全一致。

**风险**：低。**产出**：后续 8 个阶段的搬迁成本从 O(n×测试文件) 降到 O(1)。

---

### 阶段 1：抽出 `runtime/deps.py`，切换打桩点 — ✅ 已完成 2026-08-28

1. 新建 `fithealth_agent/runtime/{__init__,deps,upload_io}.py`，把 store 实例、`logger`、体积常量和 `_analysis_signing_key` 的单一持有点搬过去，连同设计注释一起搬。
2. `deps.py` 只再导出 `route_chat_intent`、`route_information`、`classify_user_health_statement`、`create_fithealth_agent` 等外部依赖；**不导出仍位于 main.py 的本地函数**。
3. 所有消费者改为 `from fithealth_agent.runtime import deps` 后使用 `deps.X`；禁止对可替换 store 使用 `from deps import store`。`main.py` 的兼容再导出只用于旧测试和外部调用，不作为内部调用路径。
4. `read_upload_with_limit` 搬到 `runtime/upload_io.py`，供餐盘分析、三条上传链路和备份端点共同使用，避免 upload workflow 反向承载通用 HTTP 工具。
5. 按符号实际消费者修改测试桩：外部依赖打 `deps.X`，已迁移本地函数打对应 domain/workflow 模块；不要把所有 `main.X` 机械替换成 `deps.X`。
6. **专项验证**：对每个热点桩做“故意抛异常”的可达性测试；额外验证替换 `deps.soreness_store`、`deps.info_store` 后所有读写方都命中新实例。
7. 验证：全量测试 node id/outcome 与基线一致，并比较路由快照。

**风险**：中（假绿风险集中在这一步，步骤 5 是专门的解药）。

---

### 阶段 2：抽出 `runtime/middleware.py` 与 `runtime/responses.py`

— ✅ 已完成 2026-08-28：维护中间件、两个降级异常处理器和 `context_error_response`
已迁入 runtime；通过显式 `register(app)` 接线，维护/备份/路由快照专项及全量测试。
阶段 2 后全量为 **870 passed**，848 条阶段 1 基线 outcome 逐条未变，新增 22 条为
阶段 1–2 的专项与治理验证。

1. 搬 `maintenance_guard` 中间件、`HealthStoreDegradedError` / `MemoryStoreDegradedError` 两个异常处理器、`MAINTENANCE_ALLOWED_PATHS` / `MAINTENANCE_UNTRACKED_PATHS`、`context_error_response`。
2. 改成 `def register(app) -> None` 的注册函数形式，由 `main.py` 显式调用，避免 import 副作用。
3. `test_journal_backup_and_restore_safety.py::StructuralInvariantTest` 用 AST 验证 middleware 接线，同步更新 `module_map.py` 指向。
4. 验证：`test_journal_backup_and_restore_safety.py`、`test_reset_transactionality.py`、`test_arch08_data23_streaming_and_locking.py` 重点跑，再跑全量。

**风险**：低。

---

### 阶段 3：搬 `domain/` 纯函数（分 4 个子步，各自独立提交）

这是体量最大的一层，但不能把访问 store 的函数伪装成纯函数。按主题分批，**每批搬完立刻更新 `module_map.py` 并跑全量**。

拆分原则：

- `domain/` 只接收普通数据并返回普通数据；不 import `deps`、FastAPI 或 store。
- `_current_recovery_snapshot`、`_training_record_items`、`_nutrition_record_items` 等读 store 的函数改放到对应 workflow/query service；其内部的筛选、格式化和聚合算法再下沉到 domain。
- `build_session_intro` 拆成“workflow 收集状态 + domain 渲染文本”两段，保持原有输出顺序。

- **3a 计划族** → `domain/plan_validation.py` + `domain/plan_context.py`

  — ✅ 已完成 2026-08-28：计划校验、计划上下文、日期解析和显示纯函数已迁入
  `domain/`；`resolve_plan_context` / `daily_schedule_constraint` 对尚未迁移的 3b/3c
  规则使用显式回调，`main.py` 保留门面以维持现有调用和打桩可达性。
  `_plan_muscle_resolver` 因读取 `deps.external_model_settings_store`，确认留在运行时
  消费者而不伪装成 domain 纯函数。`confirmed_weekly_schedule_entry` 作为日期解析的
  直接纯依赖随 3a 提前迁移。当前 `plan_*` domain 暂时复用 `info_store.py` 中两个
  无 I/O 的纯规则（事实归并、周计划按日期解析）；阶段 3c 迁移 memory domain 时
  应将其归位并消除该模块依赖。全量 **872 passed**，848 条基线 outcome 未变；
  24 个迁移符号 AST 与拆分前逐个一致，另有两条 domain 边界守门。
  涉及：`validate_generated_training_plan`、`plan_validation_fact_usage`、`constraint_regions`、`_plan_muscle_resolver`、`looks_like_complete_training_plan`、`most_recent_complete_training_plan`、`extract_plan_subject`、`is_generic_training_subject`、`plan_card_title`、`infer_training_subject`、`infer_plan_title`、`_exercise_set_counts`、`resolve_plan_context`、`format_plan_context`、`daily_schedule_constraint`、`extract_iso_dates`、`requested_plan_date`、`scheduled_plan_for_message`、`scheduled_weekly_entry_for_message`，以及它们依赖的模块级正则常量（`_SET_SUMMARY_LABEL`、`_SET_COUNT_PATTERN`、`_PLAN_DATE_INTENT`、`_PLAN_DATE_VETO_*`、`_ISO_DATE_PATTERN`、`_GENERIC_TRAINING_SUBJECTS`）。
  相关测试：`test_plan_date_context.py`、`test_plan_classifier.py`、`test_generated_plan_safety_rules.py`、`test_local_fallback_and_set_counts.py`、`test_training_plan_artifact_routing.py`。
  > 注意：`resolve_plan_context` 就是难点文档里说的"七级优先级阶梯"，逻辑密度最高，单独一次提交，diff 必须是纯位移（可用 `git diff --color-moved` 或搬完后对函数体做一次 `difflib` 逐行比对来自证）。

- **3b 意图与档案族** → `domain/intent_rules.py` + `domain/profile_rules.py`

  — ✅ 已完成 2026-08-28：本地意图识别、安全绕过规则、档案字段校验/合并/
  展示已迁入 domain，`main.py` 通过按名导入保留现有访问路径。12 个符号 AST 与
  拆分前完全一致；`profile_summary` / `onboarding_reply` 仅将
  `UserProfileStore.DEFAULT_EQUIPMENT` 替换为 `profile_rules.DEFAULT_EQUIPMENT`，
  替换后 AST 等价。`storage.UserProfileStore.DEFAULT_EQUIPMENT` 同样引用该唯一常量，
  避免存储默认值与展示默认值漂移。档案确认端点仍在 store 文件锁内执行 merge，
  现有并发与唯一写路径守门持续通过。全量 **872 passed**，848 条基线 outcome 未变。
  涉及：`is_training_related`、`is_training_record_query`、`is_profile_query`、`navigation_only_message`、`current_instruction_override`、`_is_safety_bypass_request` 及其正则、`validate_profile_tool_updates`、`merge_profile_updates_with_existing`、`equipment_change_preview`、`field_label`、`profile_summary`、`onboarding_reply`。
  相关测试：`test_profile_tool_validation.py`、`test_profile_update_confirmation.py`、`test_durable_writes_and_profile_locking.py`（含 AST 不变量：禁止 get→merge→update 的丢更新写法，搬迁后 AST 要指向新文件）、`test_health_safety_gate.py`、`test_view_intent_stress_tz_and_trend_dedup.py`。

- **3c 记忆与恢复族** → `domain/memory_view.py` + `domain/recovery_view.py`

  — ✅ 已完成 2026-08-28：记忆展示、临时约束解析、fact 寻址、恢复区域解析与
  恢复展示已迁入 domain。17 个符号 AST 与拆分前完全一致；5 个记忆展示函数仅将
  `resolve_confirmed_memory_facts` / `parse_weekly_schedule` 改为显式纯依赖参数，
  函数体 AST 等价，plan domain 对 `info_store` 的临时直接依赖由 main 门面统一承接。
  `build_session_intro` 已拆为 main 收集 store 状态 + `recovery_view.render_session_intro`
  纯渲染两段，既有输出行为不变。`_current_recovery_snapshot`、
  `_immediate_memory_candidate`、`_date_range_memory_candidate`、
  `_acute_injury_memory_candidate` 因直接读写 `deps`，确认留在运行时消费者，等待阶段 4
  chat workflow，而不伪装成 domain。全量 **872 passed**，848 条基线 outcome 未变；
  domain 边界、三处热点桩可达性和路由快照持续通过。
  涉及：`format_cross_session_memories`、`_MEMORY_FACT_LABELS`、`youtube_channels_to_avoid`、`confirmed_memory_profile`、`confirmed_weekly_schedule`、`confirmed_weekly_schedule_entry`、`_immediate_memory_candidate`、`temporary_constraint_for_message`、`_date_range_memory_candidate`、`_memory_confirmation_decision`、`_acute_injury_memory_candidate`、`_fact_locator`、`active_temporary_health_facts`、`_DURABLE_MEMORY_CUE`、`_TEMPORARY_CONSTRAINT_CUE`、`_DATE_RANGE_CUE`、`_SESSION_CONSTRAINT_CUE`；恢复侧 `explicitly_requested_recovery_regions`、`_subject_recovery_regions`、`_recovery_context_payload`、`_current_recovery_snapshot`、`_recovery_checkin_items`、`_format_muscle_recovery_lines`、`parse_garmin_recovery_hours`、`build_session_intro`、`_REGION_*` 正则。
  > `_current_recovery_snapshot`、`_immediate_memory_candidate`、`_acute_injury_memory_candidate` 是打桩热点，必须明确其最终消费者模块；不得为了兼容旧测试把它们塞进 `deps` 形成反向 import。
  相关测试：`test_context_memory.py`、`test_f3_memory_features.py`、`test_immediate_memory_capture.py`、`test_memory_validity.py`、`test_memory_fact_addressing.py`、`test_session_intro*.py`、`test_muscle_recovery.py`、`test_structured_memory_youtube.py`。

- **3d 记录视图与段合并族** → `domain/record_view.py` + `domain/segment_merge.py`

  — ✅ 已完成 2026-08-28：记录名称、记录概览、日期提取和训练/营养记录投影已迁入
  `domain/record_view.py`；投影函数改为显式接收调用方预先读取的记录列表，domain 不再
  直接访问 `daily_record_store`。`main.py` 仅保留 `_training_record_items` 与
  `_nutrition_record_items` 两个薄门面负责读取 store。已保存训练组的更新校验、心率
  聚合、数值字段聚合与合并算法已迁入 `domain/segment_merge.py`，端点仍负责读取旁挂
  心率流并传入纯算法。两个新模块已加入 `IMPLEMENTATION_MODULES` 与 domain 边界扫描；
  `workout_store.merge_sets` 和 saved-record merge 两处增加交叉注释，本阶段按计划不合并。
  专项 **96 passed**；全量 **884 passed**，路由/OpenAPI 快照与既有基线持续通过。
  涉及：`_training_record_name`、`_record_overview`、`_training_record_items`、`_requested_record_date`、`_nutrition_record_items`、`_nutrition_record_date`、`_active_saved_segments`、`_validate_saved_training_updates`、`_weighted_average`、`_segment_numbers`、`_merged_segment_hr`、`_merged_segment_numbers`、`_merge_saved_training_segments`、`_ADDITIVE_SEGMENT_FIELDS`/`_PEAK_SEGMENT_FIELDS`/`_WEIGHTED_SEGMENT_FIELDS`。
  相关测试：`test_saved_training_record_editing.py`、`test_training_record_queries.py`、`test_calorie_trend_and_bmr_removal.py`。
  > `segment_merge.py` 与 `workout_store.merge_sets` 是同一套语义的两份实现（一份对 pending，一份对已保存）。**本次不合并它们**，只是搬走并在两处加交叉注释，合并留作独立任务。

**验证增强**：真正纯函数可用 `inspect.getsource`/AST 做逐字或结构比对；含 store 访问的函数必须改为显式输入输出，并补充行为测试。建议写一次性 `scripts/verify_pure_move.py`，同时拒绝 domain 中出现 `deps`、`FastAPI`、`JSONResponse`、store 实例名等依赖。

---

### 阶段 4：拆 `/chat`（740 行 → 编排 + 阶段函数）

— ✅ 已完成 2026-08-28：`/chat` 的请求解析与 HTTP 响应适配迁入
`routes/chat.py`，其余聊天编排、运行时辅助函数与 Agent 前后处理迁入
`workflows/chat_workflow.py`。workflow 使用 `ChatTurn` 承载请求、档案、记忆、
计划上下文、artifact 与调试状态，使用 `ChatResult` 返回普通响应体和状态码；定义了
请求上下文、健康安全、意图路由、本地导航、计划上下文和生成后处理六个显式阶段边界。
route 层只执行 `read_chat_request → workflow → JSONResponse`，workflow 不 import
FastAPI、也不构造 `JSONResponse`。聊天相关可替换依赖继续统一通过 `deps.X` 访问，
`DEPS_CONSUMERS` 已加入 workflow；旧测试桩同步迁移到真实消费者，假绿守门持续通过。
`main.py` 由 3649 行降至 2537 行；workflow 1274 行，route 48 行。全量
**888 passed**，848 条基线 outcome 逐条未变，新增 40 条；路由/OpenAPI 快照无差异；
`import main` 冷启动 892/978/998 ms。

这是全计划最难的一步，单独一个阶段，**不与任何其他改动混在一个提交里**。

1. 先只读不改：把 `chat` 函数体按现有的空行/注释边界切成阶段，画出阶段间传递的变量。预期是这几段：
   1. 请求解析与上下文预算（`read_chat_request` / `ContextInputError`）
   2. 健康安全闸（`classify_user_health_statement` → 高风险直接短路返回）
   3. 只读导航兜底（`navigation_only_message` / `is_*_query` → artifact）
   4. 聊天意图路由（`route_chat_intent` → 档案更新预览分支）
   5. 酸痛反馈分支（`_is_only_soreness_feedback` / `parse_soreness_reply`）
   6. 计划上下文解析与 Agent 输入组装（`resolve_plan_context` / `build_agent_input`）
   7. Agent 调用与异常兜底（`HelloAgentsException`）
   8. 生成后处理（计划校验、`plan_draft_cache` 写入、记忆候选、artifact 组装、响应构造）
2. 定义一个 `ChatTurn` dataclass 承载阶段间状态（message、history、profile、memories、plan_context、artifacts、reply、debug…），避免十几个参数来回传。
3. 每个阶段抽成 `chat_workflow.py` 里的 `def stage_xxx(turn: ChatTurn) -> ChatTurn | ChatResult`，短路分支返回 `ChatResult`，继续则返回 `turn`。
4. `routes/chat.py` 只剩：解析 → 依次跑阶段 → `JSONResponse`。
5. **保留现有 AST 不变量**：`test_view_intent_stress_tz_and_trend_dedup.py::test_chat_has_no_direct_json_response_outside_its_builder` 断言 `chat` 内不得裸调 `JSONResponse`。搬迁后这条要扩展成对整个 `workflows/chat_workflow.py` 生效——workflow 层本来就不该产出 `JSONResponse`，正好顺势加强。
6. **行为等价验证**：拆分前先补一组“金标准”端到端用例——对 6~8 类典型输入打桩 Agent 后记录完整响应 JSON，落成 fixture。对时间戳、UUID、draft_id、随机签名等动态字段统一注入 fake clock/id factory，或在比较前按白名单规范化；不得直接逐字节比较未冻结的动态值。
7. 验证：全量测试 = 基线，金标准规范化后的 JSON 完全一致。

**风险**：高。**回滚点**：阶段 3 完成的提交。

---

### 阶段 5：拆 `/logout` 与三条上传链路

— ✅ 已完成 2026-08-29：`/logout` 与餐盘分析、FIT、训练计划、健康数据及健康 ZIP
活动上传链路已迁入 `workflows/logout_workflow.py` 与 `workflows/upload_workflow.py`，
HTTP 参数绑定与 `JSONResponse` 适配分别位于 `routes/logout.py`、`routes/uploads.py`。
workflow 使用
`WorkflowResult(body, status_code)`，不 import FastAPI 或构造 HTTP 响应；共享上传体积
限制继续使用 `runtime/upload_io.py`，临时 FIT 文件均在 `finally` 中清理。`select_autoload_activity`
与 `_health_import_message` 同步迁移，活动收集、自动载入和多 ZIP 来源信息行为保持不变。
注册位置经过调整，`/analyze_food`、`/upload_fit`、`/upload_plan`、`/upload_health` 与
`/upload_health/activity` 的原路由顺序、operation id、响应类和状态码均未变化。
`DEPS_CONSUMERS`、`FUNCTION_HOME`、`CONSUMER_HOME` 与专项结构守门已更新；打桩点
`analyze_food_image`、`inspect_fit_source`、`parse_fit_file`、`extract_activity_fits`、
`route_information` 均从真实 workflow 消费者可达。全量 **891 passed**，848 条基线
outcome 逐条未变，新增 43 条；路由/OpenAPI 快照无差异。

1. `/logout`（~100 行）→ `workflows/logout_workflow.py`。它调 `route_information` 并处理 `pipeline_stage`，注意 docstring 里写的 `level1/level2/level3` 已与 `information_router` 现状不符（白名单层已删），**这次顺手把 docstring 改对，但不改返回值**（返回值是前端契约）。
2. `/analyze_food`、`/upload_fit`（~240 行）、`/upload_plan`（~70 行）、`/upload_health`（~130 行）以及 `/upload_health/activity` → `workflows/upload_workflow.py` + `routes/uploads.py`。三者共享的体积校验、临时文件处理统一使用阶段 1 的 `runtime/upload_io.py`，不要在 workflow 中再复制 `_read_upload`。
3. `_health_import_message`、`select_autoload_activity` 一并搬到 upload workflow。
4. 相关测试：`test_health_import.py`、`test_multi_zip_activity_import.py`、`test_plan_draft_integrity.py`、`test_checkin_fit_and_editor_fixes.py`、`test_fit_parser_sport_routing.py`、`test_seventh_batch.py`、`test_remaining_partials.py`。仓库当前不存在 `test_plan_save_endpoint_contract.py`，不得将其列为验证文件。注意 `inspect_fit_source`、`extract_activity_fits`、`parse_fit_file`、`analyze_food_image` 是打桩点。

**风险**：中。

---

### 阶段 6：按资源拆 CRUD 路由（~2070 行）

— ✅ 已完成 2026-08-29：剩余 CRUD 端点按资源迁入
`routes/workout_state.py`、`records.py`、`plans.py`、`memories.py`、`health.py`、
`settings.py` 与 `maintenance_ops.py`。由于原注册顺序跨资源交错，每个资源模块按需
暴露多个 `APIRouter` 片段，`main.py` 依照拆分前顺序逐段 `app.include_router(...)`；
FastAPI 0.141 将 included router 作为惰性节点保存在 `app.routes`，装配后再原位展开
无 prefix 的片段，以继续满足阶段 0 冻结的扁平 `app.routes` 契约。73 条路由、69 个
OpenAPI operation、operation id、响应类、状态码与维护白名单均未变化。

共迁移 69 个函数；忽略 `app` → `router` decorator 差异后，函数体 AST 全部一致。
`main.py` 从约 1915 行降至 624 行，7 个资源路由文件共 1466 行。`FUNCTION_HOME`、
`CONSUMER_HOME`、`IMPLEMENTATION_MODULES` 与 `DEPS_CONSUMERS` 已更新；会话恢复快照
和 reset 直调测试的桩点同步迁到真实消费者，没有保留会假绿的 `main.X` 别名。
全量 **891 passed**，848 条基线 outcome 逐条未变，新增 43 条；路由、OpenAPI、
维护/备份专项 **78 passed**；`import main` 冷启动 974/1014/1217 ms。

纯位移，按资源建文件，每个文件一次提交：

| 新文件 | 端点 |
|---|---|
| `routes/workout_state.py` | `/workout_state` 及 `quarantined/*`、`/workout_state/update`、`/data/pending-workout` |
| `routes/records.py` | `/data/training-records*`、`/data/nutrition-records*`、`/data/records*`、`/data/overview`、`/data/checkins*` |
| `routes/plans.py` | `/plans*` |
| `routes/memories.py` | `/data/memories*`（含 facts 的 confirm/reject/edit/rollback/forget）、`/data/soreness*` |
| `routes/health.py` | `/health/*`（daily、overview、range、trend、sleep、imports、storage-status）、`/data/hr-streams/*` |
| `routes/settings.py` | `/settings/external-models`、`/profile/*`（含 `/data/profile/reset`）、`/session/intro`、`EXTERNAL_MODEL_DISCLOSURE` |
| `routes/maintenance_ops.py` | `/data/backup/*`、`/data/reset*`、`/data/recovery-points*`、`_reset_steps`、`_run_reset_steps` |

每个文件用 `APIRouter`，`main.py` 里 `app.include_router(...)`。

**注意点**：
- `/health/storage-status` 和 `/` 在 `MAINTENANCE_ALLOWED_PATHS` 白名单里，`/data/backup/import` 和 `/data/reset` 在 `MAINTENANCE_UNTRACKED_PATHS` 里。**路径字符串一个字符都不能变**，否则维护期排空会死等。搬完专门跑 `test_reset_transactionality.py` 和 `test_journal_backup_and_restore_safety.py`。
- `test_reset_transactionality.py::StructuralInvariantTest` 用 AST 验证"恢复点 → reset steps"的调用顺序，同步更新指向。
- 路由注册顺序若影响路径匹配（如 `/workout_state/quarantined/{name}/preview` vs 其他），include 顺序要保持与原文件一致。

**风险**：中。除纯位移外，还涉及 APIRouter 注册、依赖引用和端点顺序，必须比较路由与 OpenAPI 快照。

---

### 阶段 7：收尾与门面定型

— ✅ 已完成 2026-08-29：`main.py` 已收敛为 132 行的 FastAPI 装配门面，只保留
环境加载、输出编码、middleware、首页静态资源、分段 router 注册、兼容导出和
`uvicorn.run`。阶段 5 的 chat/logout/uploads 适配器也统一改用 `APIRouter`；
`/analyze_food`、三条上传路由与 `/upload_health/activity` 仍在原始位置注册。
兼容块仅导出仓库现有调用仍使用的稳定 helper、`MAINTENANCE`、`workout_store`
和异常类型，不导出任何 `deps` 可替换依赖，避免旧打桩路径重新假绿。

新增 `test_main_facade.py`，守住 `main.py <= 200`、门面函数白名单以及生产包
不得 `import main`；`MAIN` 已从 `DEPS_CONSUMERS` 移除。README、开发日志和
ARCH-09 的 module map 机制已同步更新。全量 **893 passed**，848 条基线 outcome
逐条未变，新增 45 条；73 条路由与 69 个 OpenAPI operation 契约不变；
`import main` 冷启动 936/988/1114 ms。

1. `main.py` 应只剩：环境加载、`app = FastAPI(...)`、`register_middleware(app)`、`include_router` 若干、首页静态资源、`uvicorn.run`，以及一段带注释的**兼容再导出块**（说明为什么保留、什么时候可以删）。目标 ≤ 200 行，但必须覆盖 `/analyze_food`、`/upload_health/activity`、`/data/profile/reset` 等独立端点。
2. 全仓 grep 确认没有残留的 `import main` 循环依赖；`test_package_lazy_import.py` 必须通过。
3. 更新 `README.md` 的目录说明与 `开发日志.md`；在 `ARCH-09-测试治理清单.md` 追加一节说明 `module_map.py` 机制。
4. 用 `python -c "import main"` 计时，对比拆分前后的冷启动耗时，确认没有因为分层引入额外的 eager import；同时执行路由/OpenAPI 快照比较。

---

## 4. 全程通用的验证清单

每个阶段提交前逐条过：

- [ ] `pytest -q` 全量结果与基线**逐个用例**一致；保存并比较 JUnit XML 中的 node id、outcome 和跳过原因，不以简单排序终端文本作为唯一依据。
- [ ] `git diff --stat` 确认净增行数接近 0（纯位移阶段），若明显增加说明混入了改写。
- [ ] 被搬函数做一次源码逐行比对（阶段 3 的 `verify_pure_move.py`）。
- [ ] 涉及打桩点的阶段，做一次"桩可达性验证"（故意让桩抛异常，确认测试变红）。
- [ ] 手工冒烟：启动服务，跑一遍餐盘分析、健康 ZIP/CSV 导入、上传 FIT → 编辑合并 → 确认保存 → 聊天提问 → 退出保存记忆 → 备份导出/恢复的完整链路。
- [ ] 路由与 OpenAPI 快照无差异；随机 `draft_id`、时间戳、签名 token 通过注入或规范化后再比对。
- [ ] 单独提交，提交信息注明阶段号，保证可 `git revert` 单个阶段。

---

## 5. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| `from X import f` 导致打桩静默失效 | **高**，测试假绿，问题延后暴露 | `deps` 属性访问 + 每阶段桩可达性验证 |
| 读 `main.py` 源码的 15 个测试文件集体失效 | 中，一次性大面积红 | 阶段 0 先建 `module_map.py` |
| `/chat` 拆分改变分支短路顺序 | **高**，行为漂移且难以察觉 | 阶段 4 的金标准 JSON fixture |
| 维护白名单路径字符串被改 | 中，维护期排空死等 | 常量集中在 `middleware.py`，专项测试 |
| store 实例化时机早于 `FITHEALTH_DATA_DIR` 设置 | **高**，测试污染真实 `data/` 目录 | 不在 `__init__.py` eager import `deps`；`test_package_lazy_import.py` 守门 |
| 阶段间混合提交导致无法二分定位 | 中 | 一阶段一提交，硬性要求 |

---

## 6. 明确不在本次范围内

- 不合并 `segment_merge.py` 与 `workout_store.merge_sets` 的重复语义（独立任务）。
- 不修 `information_router` 里 `_level3_llm_decide` / `pipeline_stage="level3"` 的历史命名残留（会动前端契约，独立任务）。
- 不拆 `health_store.py`(1900) / `info_store.py`(1363) / `workout_store.py`(1315)（下一轮）。
- 不改任何 HTTP 路径、请求体、响应字段。
- 不加认证、CORS、限流。

---

## 7. 工作量估计

| 阶段 | 内容 | 预估 |
|---|---|---|
| 0 | 测试基础设施 + 基线 | 0.5 天 |
| 1 | `deps.py` + 60 处打桩切换 | 1 天 |
| 2 | middleware / responses | 0.5 天 |
| 3a–3d | domain 纯函数四批 | 2 天 |
| 4 | `/chat` 拆分 | 1.5 天 |
| 5 | logout + uploads | 1 天 |
| 6 | CRUD 路由七个文件 | 1.5 天 |
| 7 | 收尾文档 | 0.5 天 |

合计约 8.5 天。**推荐节奏**：0→1→2 先做完（基础设施就位），验证一轮再推进 3，阶段 4 单独找一段完整时间做。

---

## 8. 基线（阶段 0 填写）

阶段 0 **已完成**（2026-08-28）。产出与实测值：

| 项目 | 值 | 存放位置 |
|---|---|---|
| 全量测试（阶段 0 之前） | **848 passed, 1 warning**，47.0 秒 | `tests/baseline/test_outcomes.txt`（848 行 `id<TAB>outcome`） |
| 全量测试（阶段 0 之后） | **858 passed, 1 warning**，46.6 秒 | 848 条 outcome 逐条未变，新增 10 条为基础设施自身 |
| `import main` 冷启动 | 1017.8 / 910.1 / 921.4 ms（三次） | 阶段 7 对比用 |
| 路由数 | 73 条（含 FastAPI 自带 4 条），OpenAPI 69 个 operation | `tests/baseline/route_snapshot.json` |
| 维护期白名单 | allowed `/`、`/health/storage-status`；untracked `/data/backup/import`、`/data/reset` | 同上，已进入快照比对 |

失败 0，跳过 0。基线比对命令：

```
python -m pytest -q --junitxml=.baseline/junit.xml
python scripts/test_baseline.py .baseline/junit.xml
```

比对规则：基线里每个用例都必须还在且 outcome 不变；新增用例单独列出但不算失败。
路由契约由 `tests/test_route_snapshot.py` 每次跑测试自动比对，不需要额外命令。

### 阶段 0 新增/改动的文件

| 文件 | 作用 |
|---|---|
| `tests/module_map.py` | `FUNCTION_HOME`（符号 → 定义模块）、`CONSUMER_HOME`（接线点 → 模块）、`IMPLEMENTATION_MODULES`（缺席断言的扫描范围）。**每搬走一个符号只改这里一行。** |
| `tests/source_tools.py` | `module_source` / `module_tree` / `function_node` / `function_body_source` / `load_symbols` / `load_symbol`。`load_symbols` 只收符号名、自己查表，跨模块共享一个全局字典，所以阶段 3 把计划族和意图族拆到两个 domain 文件后它们仍能互相调用。 |
| `tests/test_module_map.py` | 守门：声明的符号必须真的在声明的模块里；`IMPLEMENTATION_MODULES` 必须覆盖两张表提到的每个文件。搬迁忘了更新映射时**它第一个报错**，并直接指出是哪个符号。 |
| `tests/route_snapshot.py` + `tests/test_route_snapshot.py` | 按注册顺序比对 path / methods / name / response class / status code / operation id + 两份维护白名单。`endpoint` 一栏记函数当前住哪个模块，属参考信息，不参与比对。 |
| `scripts/test_baseline.py` | JUnit XML → 可 diff 的 `id<TAB>outcome` 文本；`--write` 记录，无参数比对。 |
| `tests/source_assertion_guard.py` | 补两条规则，详见 `ARCH-09-测试治理清单.md` 末节。 |
| 25 个测试文件 | 源码路径全部改成查表；**断言本身一字未改**（阶段 0 只换"去哪里找"，不换"断言什么"）。 |

### 阶段 1 结果（2026-08-28）

| 项目 | 值 |
|---|---|
| 全量测试 | **869 passed**，46.9 秒；848 条基线 outcome 逐条未变，新增 11 条为阶段 1 的专项验证 |
| 路由契约 | 73 条路由 / 69 个 operation，与快照一致 |
| `import main` 冷启动 | 902.9 / 888.9 / 901.0 ms（阶段 0 为 1017.8 / 910.1 / 921.4，无回归） |
| main.py | 5402 → 5341 行（190 增 / 251 删，净 −61；新建 runtime 三个文件共 166 行） |

改写规模：main.py 内 **162 处** `X` → `deps.X`（AST 定位，见 `scripts/stage1_deps_rewrite.py`），
9 个测试文件内 **134 处** 打桩点改到 `deps`（见 `scripts/stage1_test_stub_rewrite.py`）。
两个脚本都用 AST / 正则做机械改写，格式零变动；**阶段 4–6 搬 workflow 和 routes 时还会再用**，
所以先留着而不是删掉。

#### 相对原计划的一处偏离：main.py 不保留兼容再导出

原计划第 3 条写"`main.py` 的兼容再导出只用于旧测试和外部调用"。实施时**没有**为这 21 个符号
保留 `main.X` 别名，理由是它会亲手造出这次拆分最想避免的那种失效：

```python
# 如果 main.py 里留了 `soreness_store = deps.soreness_store`
patch.object(main, "soreness_store", fake)   # 执行成功，main.soreness_store 变了
# ……但被测代码读的是 deps.soreness_store，桩完全没生效，测试照样绿
```

现在这么写会抛 `AttributeError`，响亮地失败。仅对**不会被替换**的三个符号
（`logger`、`_sign_analysis_confidence`、`_verified_analysis_confidence`）保留按名字导入，
它们没有"副本指向旧对象"的问题，且 `test_checkin_fit_and_editor_fixes.py` 仍在用
`main._sign_analysis_confidence`。

同理，上传体积上限和 `read_upload_with_limit` 也按名字导入而不是走 `upload_io.X`——
常量和纯函数不会被整体替换。**判据是"会不会被整体替换"，不是"在不在 runtime 层"。**

#### 假绿是真实存在的，不是假想风险

`tests/test_deps_stub_reachability.py` 写完后做了一次负向对照：故意把 main.py 改回
`from ...chat_intent_router import route_chat_intent` + 裸调用，然后跑测试。结果——

- 新守门器的**三层检查全部变红**，并直接指出是 `route_chat_intent` 有影子绑定；
- 而 `test_f3_memory_features.py`（内含 8 处 `route_chat_intent` / `create_fithealth_agent`
  打桩）**全部通过**：那 8 个桩静默变成了空操作。

其余文件只红了 3 条看起来"行为不对"的用例，指向的是症状而不是原因。这就是这个守门器的价值。

#### 阶段 1 新增/改动的文件

| 文件 | 作用 |
|---|---|
| `fithealth_agent/runtime/__init__.py` | 只有说明，**刻意不 eager import deps**（否则 store 会在 `FITHEALTH_DATA_DIR` 设值前实例化，测试直接写进真实 `data/`） |
| `fithealth_agent/runtime/deps.py` | 11 个 store/service 实例 + 10 个被打桩的外部依赖 + `logger` + 餐盘分析签名密钥与两个辅助函数 |
| `fithealth_agent/runtime/upload_io.py` | `read_upload_with_limit` 与 7 个上传体积上限（备份端点也用它，所以放 runtime 而不是 upload workflow） |
| `tests/test_deps_stub_reachability.py` | 阶段 1 的专项验收：静态穷举 21 个符号 + 4 条"抛异常桩"动态可达性 + 2 个 store 的整体替换验证 |
| `tests/module_map.py` | 新增 `DEPS`/`UPLOAD_IO`、`DEPS_CONSUMERS`、`module_import_name()`；`IMPLEMENTATION_MODULES` 扩到 3 个 |

> `DEPS_CONSUMERS` 与 `IMPLEMENTATION_MODULES` 必须分开：`deps.py` 自己**必须**持有
> `route_chat_intent` 这些名字（那正是它的职责），消费者**必须不**持有。阶段 4–6 每新建一个
> workflow/route 文件，都要把它加进 `DEPS_CONSUMERS`。

### 阶段 0 实施中发现、需要后续阶段注意的三点

1. `TRAINING_SUBJECT_RULES` 在 `fithealth_agent/muscle_map.py`，不在 main.py。`test_plan_draft_integrity.py` 和 `test_training_plan_artifact_routing.py` 原先尝试从 main.py 抽它/注入桩，两处都是**死代码**——`infer_training_subject` 与 `looks_like_complete_training_plan` 各自在函数体内 `from ... import TRAINING_SUBJECT_RULES`，局部导入会覆盖注入的桩。已删除这两处死代码并加注释。
2. `profile_summary` 没有 docstring，而 `test_calorie_trend_and_bmr_removal.py` 里的断言用的是 `node.body[1:]`——它实际跳过了**第一条真实语句**。阶段 0 保持原样（只换路径），但阶段 3b 搬 `profile_summary` 时应顺手改成按 docstring 判断。
3. 缺席断言（"某函数/字符串必须已彻底消失"）只看一个文件的话，函数搬到新模块后会静默变成永真。已改为遍历 `IMPLEMENTATION_MODULES` 的有两处：`test_plan_memo_memory_safety.py`、`test_calorie_trend_and_bmr_removal.py::test_the_estimator_function_is_gone`。**每个阶段新建模块后必须把它追加进 `IMPLEMENTATION_MODULES`**，`test_module_map.py` 会强制这一点。


---

## 9. chatGPT-sol审核：

### 审核结论

原计划方向正确，但不能按原稿直接实施。主要风险集中在路由遗漏、`deps.py` 循环依赖与打桩失效、把依赖 store 的函数错误归入纯 domain、动态响应 fixture 不稳定，以及验证清单不能证明 HTTP 契约未变化。本次修订已将这些问题纳入阶段约束和验收条件。

### 已纳入的整改

1. 补齐独立端点：`/analyze_food`、`/upload_health/activity`、`/data/profile/reset`，并将上传读取工具与餐盘分析签名状态列入共享 runtime 设计。
2. 调整 `deps.py` 边界：只承载共享实例和外部依赖；本地函数按消费者迁移，禁止通过 `from deps import store` 形成不可打桩的副本。
3. 将 `FUNCTION_HOME` 扩展为 `FUNCTION_HOME + CONSUMER_HOME`，分别服务函数抽取和跨函数 AST 不变量。
4. 将“纯 domain”改为显式输入输出；store 访问留在 workflow/query service，首页恢复摘要拆成收集与渲染两段。
5. 金标准测试引入时间、UUID、签名等动态字段的注入或白名单规范化，不再直接比较未冻结响应的字节。
6. 阶段 5 删除不存在的 `test_plan_save_endpoint_contract.py`，换用仓库实际存在的计划草稿与功能测试。
7. 阶段 6 风险上调为“中”，新增 `app.routes` 与 `/openapi.json` 快照比较。
8. 阶段 0/8 写入已验证基线：`848 passed, 1 warning`。

### 实施前必须满足的门槛

- 阶段 0 先生成测试 node id/outcome、路由和 OpenAPI 基线。
- 阶段 1 不得引入 `deps -> main` 反向导入；所有可替换实例必须通过模块属性访问。
- 阶段 3 的 domain 模块不得依赖 FastAPI、`JSONResponse`、`deps` 或 store 实例。
- 阶段 4 的 `/chat` 金标准必须冻结动态字段后再比较。
- 阶段 7 必须证明全部旧路由仍存在，HTTP 方法、响应字段、状态码、operation id 和维护白名单均未改变。
