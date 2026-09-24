# FitHealthAgent 代码审查报告

审查日期：2026-09-24  
审查版本：`fe15b95`（`gitignore更新`）  
审查方式：静态阅读、既有专项测试、临时数据复现。初次审查未修改业务代码；随后按用户要求逐项修复，状态见下。

## 修复记录（2026-09-24）

R1–R5 均已实现修复，并补充回归测试。下文原始审查位置以 `fe15b95` 为准，保留当时的触发条件和证据；当前实现的行号可能已变化。

| 问题 | 当前处理 | 回归覆盖 |
| --- | --- | --- |
| R1 | 预检与恢复在线程中执行；待确认训练在维护窗口内重载；真实诊断接口维护期间返回 `check_deferred`，不等待存储锁 | 同事件循环并发请求、预检期间诊断、实际 JSON/数据库锁持有期间诊断、状态重载时机 |
| R2 | 重试改为重新确认的选择性重置；每次生成新恢复点，快照失败不删除，维护期间排空请求；准入检查与计数原子化 | 新确认、未知 keys、快照失败、快照包含新增数据、仅删除所选项目、请求自身不参与排空、前端取消确认 |
| R3 | 目标已有健康数据库时，拒绝不含 `health.db` 的 v2 备份，且拒绝发生在数据替换前 | 数据库、原始文件和档案均保持原样；无数据库目标仍可正常恢复 JSON 备份 |
| R4 | 显式重置使用严格 trace 清理，返回残留错误和已删除数量；恢复后的默认清理仍不抛异常 | 单文件删除失败、子目录扫描失败、部分删除数量、默认恢复回调兼容性 |
| R5 | 两种视图共用同一操作序号，状态写入前检查是否过期；当前请求失败清空旧编辑器 | 双向跨模式切换、同模式切日期、旧响应不发送编辑事件、当前加载失败不保留旧表单 |

R2 采用“每次重新确认并重新拍快照”的处理方式，不依赖旧重置操作或旧恢复点。界面明确告知所选项目期间新增的数据也将删除。R3 采用拒绝不完整恢复的保守策略，不推断缺少数据库就表示用户希望清空已有健康数据。

接口变化已同步到 [前端 HTTP API 契约](</Volumes/Lenovo K102/Malones/FitHealthAgent/docs/frontend-api-contract.md>)，维护路径快照仅增加 `/data/reset/retry` 的自身计数豁免。

验证使用新建的 Python 3.12 隔离环境和锁文件依赖。全量验证首次发现测试扫描器误读 AppleDouble 元数据；已核验文件签名并迁出到 [元数据备份目录](/Users/mac/FitHealthAgent-build-metadata-b4xwkqgm)，源码和实际依赖文件未被删除。全部数据测试使用临时目录。

最终测试结果见文末“修复后的验证”。

## 结论

初次审查发现 **5 项可操作问题：3 项 P1，2 项 P2**。以下保留原始发现，修复状态见上表。

| 编号 | 级别 | 问题 | 验证程度 |
| --- | --- | --- | --- |
| R1 | P1 | 异步备份导入同步等待请求排空，阻塞事件循环 | 实际维护组件 + asyncio 最小复现；未运行 HTTP 集成复现 |
| R2 | P1 | 重试重置绕过恢复点、确认和维护隔离 | 静态调用链确认 |
| R3 | P1 | 无数据库的 v2 备份恢复后留下旧库、清空原始文件 | 实际备份服务 + 临时 SQLite 复现 |
| R4 | P2 | trace 删除失败被吞掉，重置可能误报全部成功 | 实际清理组件故障注入 + 静态接口追踪 |
| R5 | P2 | 训练/营养视图切换后，旧请求仍可覆盖当前页面 | 抽取实际 TypeScript 函数执行并复现 |

P1 表示应优先解决的服务可用性或数据完整性问题；P2 表示需要修复的功能或状态反馈错误。报告不将代码风格偏好列为缺陷，也不声称完成了全仓库逐行审计。

## R1 · P1：备份导入在事件循环里同步等待排空

**位置：** [maintenance_ops.py:54](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/routes/maintenance_ops.py:54>)，关联 [maintenance.py:120](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/maintenance.py:120>)。

`import_backup()` 是异步路由，却直接调用同步的 `backup_service.restore(content)`。恢复方法进入 `MaintenanceGate.exclusive()` 后，通过 `threading.Condition.wait_for()` 等待在飞请求归零。

**触发条件：** 上传完成并开始恢复时，同一个服务进程里还有未结束的请求，例如聊天正在等待模型工作线程返回。该请求需要事件循环继续处理，才能返回并退出 `track_request()`；恢复却占住了事件循环等待它退出。

**影响：** 排空无法正常完成，默认等待约 15 秒后返回 409。在等待期间，其他请求也无法正常调度。即使没有其他在飞请求，解压、校验和恢复的同步磁盘操作仍会阻塞该进程的异步请求处理。

**复现证据：** 一个真实 `MaintenanceGate` 追踪的协程只需要等待 10ms；在同一事件循环直接进入 `exclusive(timeout=0.1)` 后，维护仍在约 105ms 时超时，且该协程尚未完成。这验证了同步排空与事件循环之间的等待关系，不等同于已经完成 FastAPI 端到端测试。

**建议：** 在读取上传后，将同步恢复放入 `run_in_threadpool` 等线程执行机制；确保磁盘恢复与必要的内存状态重载都在维护窗口内完成。备份预检中的同步解压与校验也应避免占住事件循环。

**回归验收：** 并发发起一个可控的慢请求和备份导入；慢请求应能正常结束，随后完成恢复。不能依赖超时结束维护；维护期间诊断接口应仍有响应。

## R2 · P1：重试清理绕过首次重置的数据保护

**位置：** [maintenance_ops.py:172](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/routes/maintenance_ops.py:172>)。

首次 `/data/reset` 会验证确认短语、进入维护模式并生成恢复点；但 `/data/reset/retry` 仅接收 `keys`，随后直接执行 `_reset_steps()` 中的清空函数。它既不验证对应的失败重置操作，也不确认恢复点是否存在，不排空其他请求。

**触发条件：** 无需先执行首次重置，直接提交 `{"keys":["records_removed","plans_removed"]}` 就会进入相应清空操作。即使通过正常 UI 重试，首次重置失败后新写入的数据也可能被整个 store 的 `clear()` 一并清除。

**影响：** 可以绕过“先有恢复点，再做批量删除”的保护；重试期间也可能与其他写请求交错。首次重置留下的恢复点不包含其后新增的数据，不能作为这些新增数据的恢复保障。

**证据边界：** 本项由完整路由分支和 `_reset_steps()` 调用链确认；没有对真实接口执行删除请求。

**建议：** 将重试绑定到服务端记录的重置操作 ID，只接受该操作失败的步骤，并验证恢复点。对重试期间的新数据明确选择阻止写入、拒绝过期重试，或生成新的受保护快照。重试必须复用维护隔离；若新增 `exclusive()`，也要同步调整中间件对该维护请求本身的在飞统计，避免等待自己。

**回归验收：** 没有有效重置操作时不能直接清空；不存在/已过期的恢复点不能继续删除；重试与并发写入之间不存在未经快照保护的数据丢失。

## R3 · P1：恢复缺少 health.db 的备份会形成新旧数据混合状态

**位置：** [backup_service.py:545](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/backup_service.py:545>)、[backup_service.py:574](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/backup_service.py:574>)。

备份格式允许 `health.db` 缺省。恢复 v2 备份时，没有成员的 `health-imports` 等目录会按“原目录为空”重建；但缺省顶层文件的清理只考虑 `OPTIONAL_JSON_FILES`，不包括 `health.db`。

**触发条件：** 目标目录已有健康数据库及原始导入文件，导入一份合法但不含 `health.db`、也不含原始文件的 v2 备份。

**影响：** 恢复正常返回，旧数据库仍然存在，旧原始文件却已经被清空。数据库引用与文件系统脱节，界面可能显示不属于恢复目标的旧健康数据，并且无法重新解析对应原始文件。恢复点提供了人工回退机会，但不能消除“成功恢复后数据不一致”的缺陷。

**复现证据：** 在两个临时目录中用真实 `LocalBackupService` 导出、恢复；目标 SQLite 放入一条引用 `old.zip` 的合成记录。结果如下：

```json
{
  "has_health_database": false,
  "old_db_rows": [["old.zip"]],
  "raw_files_after_restore": []
}
```

**建议：** 明确定义“备份未含数据库”的含义。如果表示空状态，应在独占和回滚保护下清理旧库及边车，再初始化新库；如果不允许覆盖已有数据库，应在任何替换之前拒绝该恢复。不能保留旧库的同时无条件替换其依赖目录。

**回归验收：** 用不含数据库的合法 v2 备份恢复到有数据的实例，结果应是明确拒绝或一致的空数据库状态；不得残留引用已删除原始文件的旧记录。

## R4 · P2：trace 删除失败没有进入重置的部分失败反馈

**位置：** [sink.py:461](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/observability/sink.py:461>)，关联 [maintenance_ops.py:109](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/routes/maintenance_ops.py:109>) 与 [maintenance_ops.py:192](</Volumes/Lenovo K102/Malones/FitHealthAgent/fithealth_agent/routes/maintenance_ops.py:192>)。

`TraceStore.clear()` 和 `_unlink_matching()` 将文件删除失败写入日志，仍返回删除数量；重置编排则把“没有异常”解释成步骤成功。因此无法区分“原本无文件”和“有文件但全部删除失败”。

**触发条件：** trace 文件因权限、文件占用或 I/O 故障不能删除，其他重置步骤正常。

**影响：** 重置响应可能返回 `deleted: true`、`partial: false`，而健康信息相关的 trace 仍在磁盘。用户既看不到残留提示，也无法通过失败步骤列表重试该项。

**复现证据：** 在临时 trace 目录中创建合成文件，对该文件的 `Path.unlink()` 注入 `PermissionError`；实际 `TraceStore.clear()` 返回 `0`，没有异常，文件仍存在。

**建议：** 保留恢复后回调“不影响已完成恢复”的语义，但让清理返回删除数量和失败列表，或提供用于显式重置的严格模式。重置接口据此报告 `partial`；备份恢复可报告清理警告，而不是把已经完成的数据恢复改报失败。

**回归验收：** 单个 trace 删除失败时，重置响应必须指出残留和可重试步骤；已成功删除的数量仍应准确。

## R5 · P2：跨视图请求没有共同的过期判定

**位置：** [controller.ts:114](</Volumes/Lenovo K102/Malones/FitHealthAgent/frontend/src/domains/data-management/controller.ts:114>)。

`viewer()` 使用两个不同的操作键：`viewer-training` 和 `viewer-nutrition`。切换模式时，新的营养请求不会令旧训练请求失效；两种请求又共同写入 `state.viewerItems`。异步返回后没有重新核对当前模式和日期。

**触发条件：** 训练列表请求较慢，用户切换到营养视图；营养结果先返回，训练结果后返回。

**影响：** 当前状态标记为营养视图，但列表内容被旧训练记录替换；同时还会触发旧训练记录的 `workout:edit-saved` 事件。

**复现证据：** 使用 Node 24 内置 TypeScript 类型剥离能力，从实际源码提取 `run()`、`viewer()` 和 `createOperationRegistry()`，替换网络与视图依赖并控制响应顺序。最终输出：

```json
{
  "finalState": {
    "viewerMode": "nutrition",
    "viewerDay": "2026-09-24",
    "viewerItems": [{"id": "training-old"}]
  },
  "lastEvent": {
    "name": "workout:edit-saved",
    "payload": {"recordId": "training-old", "showNotice": false}
  }
}
```

这属于函数级复现，未运行真实浏览器测试。另外，同模式的过期调用得到 `undefined` 后仍会执行清空和渲染，也应在修复时一并处理。

**建议：** 对同一个 viewer 使用统一的请求序号/操作键，并将“仍为最新调用”的检查覆盖到状态写入、渲染和事件发送。取消或过期应直接退出，不应当作空列表渲染。

**回归验收：** 覆盖训练→营养、营养→训练、同模式快速切日期，以及旧响应晚于新响应返回的顺序；最终内容必须与当前模式、日期一致，过期请求不得发送编辑事件。

## 初次审查覆盖与边界

重点阅读了应用入口、维护中间件、备份导入/导出、重置及重试、JSON 存储和锁、trace 清理、记录与计划路由，以及前端 API 客户端、请求取消和数据管理控制器。参考了备份、维护、trace 相关测试。

初次审查未完成：完整后端/前端测试、真实浏览器交互、Docker 构建、外部模型集成、依赖漏洞扫描，以及所有健康算法和 FIT 解析分支的逐行审计。修复阶段已补充的验证见文末；本报告不是全项目安全或正确性保证。

部署边界已核对：Compose 默认绑定回环地址，README 明示 `python main.py` 会监听全部网卡，并要求可信网络或认证代理。本轮没有把这一已声明的部署条件直接计为新的代码缺陷。

## 初次审查验证记录（修复前）

环境：Python `3.13.3`、Node `v24.16.0`。项目声明 Python `>=3.11,<3.13`，当前解释器超出支持范围；以下结果仅代表本机专项验证，不能替代支持版本上的 CI。

| 验证 | 结果 |
| --- | --- |
| `python3 -m unittest tests.test_backup_service` | 5 个测试通过 |
| `python3 -m unittest tests.test_backup_service tests.test_journal_backup_and_restore_safety` | 报告运行 43 个测试，3 个错误；缺少 `fastapi` 和 `fitparse`，不能宣称该组全通过 |
| `TraceStoreClearTest`、`RestoreIsolationTest`、`MaintenanceGateTest` 联合执行 | 报告 11 个测试，1 个模块导入错误；trace 测试依赖的 `fitfile` 缺失，其余 10 个通过 |
| 维护排空最小复现 | 复现事件循环阻塞导致排空超时 |
| 缺少数据库的备份恢复复现 | 复现旧数据库与已清空原始目录并存 |
| trace 删除故障注入 | 复现残留文件且清理无异常返回 |
| 前端 viewer 函数级响应乱序复现 | 复现旧训练数据覆盖营养视图 |

所有动态复现使用临时目录、合成数据或替代网络依赖；没有调用真实模型或对项目真实健康数据执行重置、恢复。

## 原建议修复顺序

1. 修复 R1、R2，统一维护操作的线程调度、请求排空和快照保护。
2. 修复 R3，明确备份中可选数据库的恢复语义，并加入一致性测试。
3. 修复 R4，让显式删除能准确报告残留。
4. 修复 R5，补充跨模式、跨日期的响应乱序测试。
5. 在 Python 3.12 和已安装项目依赖的环境中运行专项及完整测试，再进行实际浏览器与容器验证。

## 修复后的验证

验证日期：2026-09-24。环境为 Python `3.12.13`、Node `v24.16.0`；Python 业务依赖按 `requirements.lock` 校验哈希安装，前端依赖使用 `npm ci` 安装。以下结果针对当前工作区中的 R1–R5 修复。

| 验证 | 最终结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **1114 通过、15 跳过、0 失败**；1 条来自 Starlette/httpx 的弃用警告 |
| 后端备份、重置、维护、trace 专项回归 | **141 通过**；已包含在全量结果中 |
| 前端 `npm test` | **24 个测试文件、180 个测试通过** |
| 前端 `npm run lint`、`npm run typecheck` | 通过；最后一次 E2E fixture 调整后，该文件 ESLint 再次通过 |
| 前端 `npm run build` | 通过 |
| 前端 `npm run check:bundle` | 通过；gzip 合计 **67,150 B / 74,000 B**，JS 和 CSS 各自也未超预算 |
| `check:boundaries`、`check:governance`、`check:architecture`、`check:components`、`check:styles` | 全部通过 |
| `npm run test:architecture-rules` | **2 个测试通过** |
| Playwright 新增跨模式响应乱序回归 | **3 个测试通过**：桌面 1440、平板 768、手机 390；无用例捕获的脚本或控制台异常 |
| 本次修改的 TypeScript / E2E 文件格式检查 | Prettier 检查通过 |
| `git diff --check` | 通过 |

浏览器回归使用真实 Chromium 和本地应用页面，将训练请求延迟到营养列表渲染完成后再放行，确认页面仍显示最新营养记录。单元测试另覆盖相反切换方向、同模式切日期，以及不响应取消信号的旧请求。首轮浏览器用例的模拟接口漏匹配无日期查询，修正 fixture 后三个视口均通过。

macOS 在外置磁盘上生成的 AppleDouble 文件曾被测试扫描器和构建体积脚本误读；核验签名并迁出这些附属文件后重新执行相关检查，上表记录的是清理后的最终结果。

本次未执行完整 Playwright/视觉基线套件、Docker 镜像构建与容器冒烟、真实外部模型调用或依赖漏洞扫描。没有更改依赖版本或更新视觉基线；数据重置和恢复测试均针对临时数据。修复代码及本报告保留在工作区，尚未提交 Git。
