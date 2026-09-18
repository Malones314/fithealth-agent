# 前端断言迁移台账

> 生成基线：2026-09-10  
> 关闭日期：2026-09-14

阶段 12 已删除旧模板、`LEGACY_INDEX` 和临时断言扫描器。本文件只保留迁移历史；
所有条目均已转为行为或契约测试，当前不存在旧模板耦合。

## 1. 原子迁移规则

每个领域迁移提交必须同时完成四件事：迁移实现、增加行为/契约测试、删除 legacy 对应实现、更新 `LEGACY_SOURCE_ASSERTION_TESTS`。不得先把断言改指向新的 `.ts` 源码而继续保留字符串断言；这只是在转移债务。

Python 测试定位前端文件必须使用 `tests/frontend_map.py`。新增源码文本断言不允许加入 ARCH-09 豁免。台账状态只允许：`pending`、`migrated`、`retained-with-reason`。

## 2. 当前台账

| 测试 | 领域 | 目标测试 | 状态 | 迁移验收 |
| --- | --- | --- | --- | --- |
| `test_agent_training_edit_tools.py::test_sidebar_recovery_controls_use_the_pending_workout_endpoint` | workout | Vitest controller | migrated | 撤销、恢复按钮调用 typed update API |
| `test_calorie_trend_and_bmr_removal.py::test_the_frontend_no_longer_falls_back_to_an_estimate` | health | Vitest view | migrated | 缺失实测值时不渲染 BMR 估算 |
| `test_calorie_trend_and_bmr_removal.py::test_the_frontend_offers_the_new_metric` | health | Playwright DOM | migrated | 指标选项包含 total_calories |
| `test_calorie_trend_and_bmr_removal.py::test_the_frontend_does_not_average_a_cumulative_curve` | health | Vitest controller | migrated | cumulative 分支展示末值而非平均值 |
| `test_calorie_trend_and_bmr_removal.py::test_the_frontend_explains_the_prorated_resting_part` | health | Vitest view | migrated | 累计热量展示静息均摊说明 |
| `test_data_management_collapsible.py::test_requested_sections_are_collapsible_and_external_models_are_last` | data-management | Playwright DOM | migrated | 六个 section 可折叠且外部模型位于末尾 |
| `test_memory_fact_addressing.py::test_frontend_prefers_fact_id` | data-management | Vitest controller | migrated | 请求优先使用 fact_id，不拼列表下标 |
| `test_multi_zip_activity_import.py::test_picker_is_opened_from_the_rich_activities_list` | uploads | Vitest/Playwright | migrated | 多活动响应通过真实上传入口打开选择器 |
| `test_multi_zip_activity_import.py::test_picker_resolves_each_activity_to_its_own_zip_file` | uploads | Vitest controller | migrated | 活动按 zip/name 解析源文件 |
| `test_multi_zip_activity_import.py::test_upload_sends_the_matching_zip_not_a_single_remembered_one` | uploads | Vitest controller | migrated | API 使用被选活动对应文件 |
| `test_multi_zip_activity_import.py::test_remaining_activities_are_keyed_by_zip_and_name` | uploads | Vitest state | migrated | 同名活动不会跨 ZIP 一并移除 |
| `test_multi_zip_activity_import.py::test_multi_zip_picker_labels_show_the_source` | uploads | Vitest view | migrated | 选项同时显示 ZIP 与活动名 |
| `test_multi_zip_activity_import.py::test_the_old_give_up_notice_is_gone` | uploads | Vitest/Playwright | migrated | 多 ZIP 可继续处理且无全局兼容出口 |
| `test_plan_draft_integrity.py::test_frontend_passes_draft_id_back` | chat | Vitest controller | migrated | 保存请求原样携带 artifact.draft_id |
| `test_profile_update_confirmation.py::test_confirmation_card_renders_equipment_diff` | chat | Vitest view | migrated | 器械新增/删除差异正确显示 |
| `test_quarantined_workout_recovery.py::test_startup_does_not_block_with_a_confirm_dialog` | workout | Vitest controller | migrated | 启动只显示非阻塞通知，不调用 ask |
| `test_quarantined_workout_recovery.py::test_data_panel_offers_dismiss_and_delete` | data-management | Vitest controller/view | migrated | 列表支持 dismiss/delete 和 include_dismissed |
| `test_saved_training_record_editing.py::test_frontend_sends_revision_and_handles_conflicts` | workout | Vitest controller | migrated | PATCH 携带 revision，409 保留草稿并进入冲突状态 |
| `test_weekly_schedule_structure.py::test_pending_memory_card_displays_skipped_schedule_keys` | data-management | Vitest view | migrated | pending memory 卡展示 invalid_days |

自动扫描当前报告 0 个文件、0 个测试函数。阶段 9 已清理 workout/uploads 的 9 条、
阶段 10 已清理 health 的 4 条，阶段 11 已清理最后 6 条模板耦合断言。

## 3. 领域清理顺序

| 阶段 3 切片 | 预计清理条目 |
| --- | ---: |
| session | 1 |
| workout | 2 |
| uploads | 6 |
| chat | 3 |
| health/checkin | 4 |
| data-management | 3 |

领域提交完成后运行扫描器和 `tests/test_arch09_test_governance.py`；出现 `unexpected` 或 `stale` 均视为提交不完整。
