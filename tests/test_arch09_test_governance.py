from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests.source_assertion_guard import source_text_assertions


TESTS_DIR = Path(__file__).resolve().parent

# Existing debt is explicit and can only shrink. A newly added test function is
# absent from this set, so source-text assertions in new tests fail this guard.
LEGACY_SOURCE_ASSERTION_TESTS = {
    "test_upload_workflow_structure.py::test_logout_route_is_thin_adapter",
    "test_upload_workflow_structure.py::test_route_adapters_construct_http_responses",
    "test_upload_workflow_structure.py::test_workflow_has_no_fastapi_or_json_response_imports",
    # 阶段 4 新增的三条是架构结构约束：检查 class/function/import/call AST，
    # 不读取或断言源码文本内容。
    "test_chat_workflow_structure.py::test_named_workflow_stage_boundaries_exist",
    "test_chat_workflow_structure.py::test_workflow_has_explicit_state_and_result_types",
    "test_chat_workflow_structure.py::test_workflow_never_constructs_http_responses",
    "test_agent_write_validation.py::test_prompt_no_longer_contradicts_the_tool_layer",
    "test_calorie_trend_and_bmr_removal.py::test_the_overview_endpoint_no_longer_injects_an_estimate",
    "test_calorie_trend_and_bmr_removal.py::test_the_profile_summary_no_longer_reports_an_estimate",
    "test_data_dir_and_docker.py::test_data_dir_env_matches_the_mount_point",
    "test_data_dir_and_docker.py::test_data_volume_does_not_depend_on_the_source_mount",
    "test_data_dir_and_docker.py::test_data_volume_is_declared",
    "test_data_dir_and_docker.py::test_dev_override_keeps_the_data_volume",
    "test_data_dir_and_docker.py::test_dockerignore_keeps_personal_data_out_of_the_image",
    "test_data_dir_and_docker.py::test_does_not_build_from_its_own_output_image",
    "test_data_dir_and_docker.py::test_sets_the_data_dir_env",
    "test_data_dir_and_docker.py::test_vendor_directory_exists_so_the_copy_never_breaks_the_build",
    "test_durable_writes_and_profile_locking.py::test_public_profile_methods_hold_the_file_lock",
    "test_health_safety_gate.py::test_prompt_contains_risk_grading",
    "test_info_store_corruption_guard.py::test_main_surfaces_the_degraded_state",
    "test_journal_backup_and_restore_safety.py::test_middleware_is_registered_explicitly_without_import_side_effects",
    "test_journal_backup_and_restore_safety.py::test_restore_reloads_pending_workout_instead_of_clearing_it",
    "test_local_fallback_and_set_counts.py::test_all_three_helpers_are_called",
    "test_local_fallback_and_set_counts.py::test_offline_reply_names_the_local_commands",
    "test_local_fallback_and_set_counts.py::test_old_naive_regexes_are_gone",
    "test_plan_date_context.py::test_both_paths_go_through_the_shared_extractor",
    "test_plan_date_context.py::test_chat_uses_all_confirmed_memories_for_weekly_schedule",
    "test_plan_date_context.py::test_scheduled_plan_uses_the_gated_helper",
    "test_plan_date_context.py::test_scheduled_subject_overrides_router_summary",
    "test_plan_date_context.py::test_the_old_strict_regex_is_gone",
    "test_plan_draft_integrity.py::test_generated_plans_are_cached_after_validation",
    "test_plan_draft_integrity.py::test_local_plan_save_artifact_carries_the_draft_id",
    "test_plan_draft_integrity.py::test_save_branch_passes_the_intent_clues_to_the_cache",
    "test_plan_draft_integrity.py::test_save_branch_prefers_the_cache_over_history",
    "test_plan_draft_integrity.py::test_save_endpoint_delegates_to_the_pure_resolver",
    "test_plan_memo_memory_safety.py::test_plan_memos_are_not_automatically_promoted_to_memories",
    # 以下三条是**既有**债务，不是新增的。main.py 拆分（阶段 0）给守门器补上了
    # "内联源码读取"的识别（`assertIn(x, ast.unparse(node))` 这种写法此前没有
    # 赋值语句可供污染传播，一直是一条暗道）。债务没有变多，是检测变准了。
    "test_profile_update_confirmation.py::test_chat_only_returns_profile_update_candidate",
    "test_profile_update_confirmation.py::test_confirm_endpoint_is_the_only_profile_write_path",
    "test_saved_training_record_editing.py::test_endpoint_feeds_the_sidecar_stream_into_the_merge",
    "test_quarantined_workout_recovery.py::test_list_and_replay_are_exposed",
    "test_runtime_environment.py::test_container_and_docs_use_the_declared_default",
    "test_runtime_environment.py::test_lockfile_pins_every_direct_dependency",
    "test_runtime_environment.py::test_runtime_and_development_dependencies_are_explicit",
    "test_saved_training_record_editing.py::test_merge_no_longer_hardcodes_none_heart_rate",
    "test_sixth_batch_stage4.py::test_intro_explains_effect_and_correction",
    "test_training_plan_artifact_routing.py::test_chat_plan_artifact_requires_a_complete_plan_reply",
    "test_view_intent_stress_tz_and_trend_dedup.py::test_every_raw_sample_reader_shares_one_dedup_definition",
}


class SourceAssertionGovernanceTest(unittest.TestCase):
    def test_guard_detects_a_source_text_assertion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "test_bad.py"
            sample.write_text(
                "from pathlib import Path\n"
                "def test_bad():\n"
                "    source = Path('main.py').read_text()\n"
                "    assert 'sentinel' in source\n",
                encoding="utf-8",
            )
            self.assertEqual(source_text_assertions(sample), [("test_bad", 4)])

    def test_guard_detects_a_source_assertion_routed_through_the_module_map(self) -> None:
        """main.py 拆分（阶段 0）后的新写法也必须被抓到。

        `module_source(consumer_home("chat"))` 里没有 `.read_text` 调用，守门器
        只能靠 SOURCE_HELPERS 认出它。这条用例就是钉住那份名单——漏了它，
        改造过的那批文件会集体从基线里消失而没人发现。
        """
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "test_bad_via_map.py"
            sample.write_text(
                "from tests.module_map import consumer_home\n"
                "from tests.source_tools import module_source\n"
                "def test_bad():\n"
                "    source = module_source(consumer_home('chat'))\n"
                "    assert 'sentinel' in source\n",
                encoding="utf-8",
            )
            self.assertEqual(source_text_assertions(sample), [("test_bad", 5)])

    def test_test_process_never_targets_the_repository_data_directory(self) -> None:
        configured = Path(os.environ["FITHEALTH_DATA_DIR"]).resolve()
        repository_data = TESTS_DIR.parent / "data"
        self.assertNotEqual(configured, repository_data.resolve())
        self.assertTrue(configured.name.startswith("fithealth-tests-"), configured)

    def test_new_tests_do_not_assert_against_python_or_html_source_text(self) -> None:
        offenders: set[str] = set()
        for path in TESTS_DIR.glob("test_*.py"):
            if path.name == Path(__file__).name:
                continue
            for test_name, _line in source_text_assertions(path):
                offenders.add(f"{path.name}::{test_name}")
        unexpected = sorted(offenders - LEGACY_SOURCE_ASSERTION_TESTS)
        stale = sorted(LEGACY_SOURCE_ASSERTION_TESTS - offenders)
        self.assertEqual(
            unexpected,
            [],
            "新增了源码文本断言；请改为行为断言，只有结构约束才能用 AST：\n"
            + "\n".join(unexpected),
        )
        self.assertEqual(
            stale,
            [],
            "这些遗留豁免已经不再需要，请从基线删除：\n" + "\n".join(stale),
        )


if __name__ == "__main__":
    unittest.main()
