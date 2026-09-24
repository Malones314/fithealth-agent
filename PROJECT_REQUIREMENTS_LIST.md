# FitHealthAgent — 项目需求清单

版本：2026-08-13

**摘要**
本文件列出 FitHealthAgent 当前功能概况、未实现需求、agent（智能体）相关功能缺口、上下文管理方案建议，以及是否以及如何在项目中集成 hello_agents 框架的 NoteTool。目标是形成可执行的需求 backlog，便于优先级排序与实现。

---

## 1. 当前功能概览
- 基于 FastAPI 的后端（main.py）
- 用户档案管理（UserProfileStore）：身高、每周体重、器械、目标等
- 训练计划上传与解析（/upload_plan）、FIT 文件上传与解析（/upload_fit）
- 对话入口 /chat：调用 create_fithealth_agent() 运行 agent
- 临时信息存储 InfoStore（短期记忆）与训练计划、训练记录存储
- 训练卡片与编辑接口（workout_state、workout_state/update）
- 登出流程 /logout: 调用信息路由 route_information 进行三级路由并可能保存摘要

## 2. 发现的问题与需求（功能性）
1. 长期记忆与检索：InfoStore 似乎为短期/临时记忆，缺乏长期、可检索的持久记忆（用户偏好、历史训练摘要、plan 版本历史）及基于向量的检索接口。
2. 上下文窗口管理：agent_input 使用最近 5 条对话，但没有分层摘要/压缩策略，token 控制与检索增强未实现。
3. 训练计划 QA 与验证不足：plan_classifier 存在基本计划鉴定，但缺少结构化解析、可编辑的计划模型与完整自动化验证规则集。
4. Agent 可解释性与审计：agent.run() 返回任意文本，缺少生成来源 attribution（哪些事实来自用户 profile、哪些由训练数据产生）及生成日志。
5. 错误处理与回退：多个 except: Exception 捕获但未细化错误类别或 graceful fallback（尤其 agent 调用与 parse_fit_file）。
6. 测试覆盖：缺少单元测试与集成测试，尤其针对 parse_* 函数、upload endpoints、route_information。
7. 并发与持久化：持久化存储如何在多实例/重启场景中表现不明确（daily_record_store、plan_store 的后端实现需核查）。
8. 安全与输入校验：文件上传、文本解析的安全边界（文件大小、编码、CSV/MD 注入）需要审计。

## 3. Agent 需求与能力缺口（智能体角度）
1. 明确 agent 角色与工具边界：create_fithealth_agent() 未展示实现细节。需要明确 agent 的工具集（NoteTool、PlanStore 接口、FIT 解析器、向量 DB、外部 API）与权限。
2. 文本到结构化计划转换器：agent 应能输出结构化 JSON plan（周/日/动作/组/负重/次数/备注），当前仅返回文本。
3. 规划可迭代工作流：支持 agent 生成初稿 → 人类/前端编辑 → agent 优化（适配器需要 plan_context 与 artifact 回传机制）。
4. 记忆层次：agent 需使用短期对话上下文 + 会话级摘要 + 用户长期偏好。缺少 embedding 存储与检索逻辑。
5. 错误与不确定性处理：当 agent 不确定（例如动作识别不准）应返回结构化建议与 uncertainty 标记，便于前端提示“是否确认”。
6. 严格的隐私/合规策略：训练数据、心率等生物敏感数据的保存、导出与删除策略。

## 4. 上下文管理思路（建议架构）
目标：在保证响应质量与成本（tokens）之间取得平衡，支持长期个性化与短期对话连贯性。

分层记忆（Memory Tiers）：
- Tier 0（即时上下文）：最近 5-10 条对话，用于即时响应（已实现部分）。
- Tier 1（会话摘要）：对会话进行增量摘要（每 1000-2000 tokens 摘要一次），保存在 InfoStore 或 NoteTool，保留 7 天。
- Tier 2（长期用户画像）：结构化 profile、偏好、长期训练目标、不可变信息，作为 agent 个性化输入。
- Tier 3（检索记忆 / 向量 DB）：历史训练记录、重大事件、曾生成的训练计划等，使用 embeddings 做相似度检索。

实现细节：
- 引入 embedding 服务（OpenAI/其他）和向量 DB（FAISS/SQLite+pgvector/Weaviate）以做相似检索。
- 每次 agent 生成训练计划或关键决策时，存一条带 metadata 的短摘要与 embedding。
- 对话入口在构建 agent_input 时：先检索 Tier2/Tier3 的 K 最近相关条目，按优先级拼接（并限制 token），再添加会话摘要与即时上下文。
- 自动化摘要：定时/事件驱动（logout、保存 plan、上传 FIT）触发 multi-level 摘要器，结果存 Tier1/Tier2。
- 在前端提供“上下文查看/清除”功能，支持用户自己管理记忆。

上下文期限与隐私：
- 敏感生理数据（心率、病史）应加密存储并允许用户随时删除。
- 默认保留 Tier1 7 天、Tier3 90 天（可配置）。

## 5. 是否使用 hello_agents 的 NoteTool？（建议）
结论：推荐使用 NoteTool（或等价的笔记/记忆工具），作为 agent 与系统之间的轻量持久化接口，但需扩展以支持 embeddings 与元数据。

理由：
- NoteTool 提供简单的 write/read/note lifecycle，适合保存中间草稿、会话摘要、训练计划草案与用户确认记录。
- 与 InfoStore 整合可以实现更稳定的“会话级别”记忆写入点（例如 logout 时写入 note）。

如何使用（高层步骤）：
1. 引入 hello_agents.NoteTool 作为 agent 的工具之一（create_fithealth_agent 中加入）：
   - note_tool = NoteTool(storage=... , namespace=f"user:{user_id}")
   - agent.register_tool("note", note_tool)
2. 使用场景：
   - 在生成训练计划（looks_like_complete_training_plan 为 True）时，创建一个 note 保存 plan 文本、subject、生成 metadata（agent_version, timestamp）。
   - logout 时把 route_information 的 summary 写入 NoteTool（并在 InfoStore 中保索引）。
   - 当 agent 询问用户确认动作识别问题时，把用户确认结果写入 note（用于后续训练/改进）。
3. 增强 NoteTool：
   - 为每 note 生成 embedding 并把 embedding 存入向量 DB（或把 note 内容同时写入向ect DB），便于检索。
   - 保存 metadata 字段：{type: training_plan|summary|fit_upload, subject:, source:, agent_id:, expires_at:}
4. 回收与保留策略：实现 note TTL 与清理任务（与 InfoStore 保持一致）。

注意事项：
- NoteTool 不应成为唯一的长期存储；它可作为 agent 可用的“工作台”，长期检索与分析建议依赖向量 DB 与 PlanStore。
- 对敏感数据加密字段或避免在 note 中保存敏感原文（只保存摘要/哈希）。

## 6. 可交付的需求清单（优先级、验收标准、估时）
格式：- id — 标题 (优先级) — 验收标准 — 估时 (人日)

- memory-longterm — 引入长期记忆（向量 DB 与 embedding pipeline）(P0) — 能存储/检索最近 10 条相关记忆并被 /chat 调用成功 — 5d
- agent-structured-plan — 训练计划结构化输出（JSON schema）(P0) — agent 能输出并通过单元测试的 JSON plan，前端能渲染并编辑 — 4d
- context-summarizer — 分层摘要器（会话/日/周）(P1) — logout/upload_plan/upload_fit 触发摘要，摘要在 InfoStore/note 写入并可检索 — 3d
- note-integration — 集成 hello_agents.NoteTool 并扩展 embedding 写入 (P1) — NoteTool 写入后在向量 DB 可检索，metadata 完整 — 2d
- error-handling — 精细化异常处理与监控 (P1) — 关键路径不再使用 broad except，错误日志结构化发到 monitoring — 2d
- tests-core — 为 parse_*、upload endpoints、plan validator 添加单元测试 (P0) — CI 通过且覆盖率提高 — 5d
- privacy-controls — 数据删除/导出/加密策略 (P0) — 用户能删除个人数据，且敏感字段加密 — 3d
- agent-audit — 生成响应时包含 provenance metadata (P2) — response 带上来源字段或 confidence 标签 — 3d
- plan-validator-enhanced — 结构化 plan 验证规则 (P1) — 能检测重复/冲突训练天、总量异常、恢复期警告 — 4d
- docs-and-runbooks — 运行/部署文档与记忆管理手册 (P2) — README 更新并有记忆生命周期说明 — 2d

## 7. 接入 NoteTool 的示例代码片段（伪代码）

```aiignore
# 在 create_fithealth_agent() 内部注册 NoteTool
# note: 伪代码示意
"""
from hello_agents import NoteTool
note_tool = NoteTool(storage=NoteStorage(dirname="notes"), namespace=f"user:{user_id}")
agent.add_tool("note", note_tool)
"""

# 在生成训练计划后写入 note
"""
if looks_like_complete_training_plan(answer):
    note_id = note_tool.write(
        title=f"plan:{subject}",
        content=answer,
        metadata={"type":"training_plan","subject":subject,"agent":"fithealth-v1"}
    )
    # 同步 embedding
    embed = embedding_service.embed_text(answer)
    vector_db.upsert(id=note_id, vector=embed, metadata={...})
"""
```


## 8. 风险与里程碑建议
- 风险：向量 DB 成本与隐私合规、agent 输出非结构化导致前端解析失败、FIT 解析器对不同设备兼容性问题。
- 里程碑：
  1) 完成长期记忆 PoC（向量 DB + 简单检索）
  2) agent 输出结构化 plan + 前端编辑循环
  3) NoteTool 集成与 logout 自动化摘要
  4) 完整测试与隐私合规审计

---

## 9. 附录：首周开发计划（建议）
Day 1-2: 建设 embedding pipeline、向量 DB PoC；实现基本检索 API
Day 3-5: agent 输出 JSON plan schema 设计与实现；单元测试
Day 6-7: NoteTool 集成与 logout/ upload_plan 写入；实现 TTL 清理

---

如需，将此文件拆成多个更小的 task tickets（带 issue 模板/PR checklist）。

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
