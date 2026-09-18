from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = REPO_ROOT / "fithealth_agent" / "chat_intent_router.py"
    spec = importlib.util.spec_from_file_location("chat_intent_router_for_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load chat_intent_router")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ChatIntentRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.router = load_module()

    def test_routes_training_data_view_through_tool_call(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {
                "choices": [{"message": {"tool_calls": [{
                    "function": {
                        "name": "view_training_records",
                        "arguments": '{"date":"2026-08-13"}',
                    }
                }]}}]
            }
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("查看 2026-08-13 的训练数据")
        self.assertTrue(intent.view_training_records)
        self.assertEqual(intent.training_records_date, "2026-08-13")
        self.assertFalse(intent.create_training_plan)

    def test_routes_nutrition_data_view_through_tool_call(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {
                "choices": [{"message": {"tool_calls": [{
                    "function": {
                        "name": "view_nutrition_records",
                        "arguments": '{"date":"2026-08-23"}',
                    }
                }]}}]
            }
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("查看 2026-08-23 的营养数据")
        self.assertTrue(intent.view_nutrition_records)
        self.assertEqual(intent.nutrition_records_date, "2026-08-23")
        self.assertFalse(intent.view_training_records)

    def test_rejects_invalid_training_record_date(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {"name": "view_training_records", "arguments": '{"date":"next Tuesday"}'},
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("查看下周二训练记录")
        self.assertTrue(intent.view_training_records)
        self.assertIsNone(intent.training_records_date)

    def test_plan_tool_requires_structured_subject_and_title(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {
                    "name": "create_training_plan",
                    "arguments": '{"subject":"胸部训练","title":"周一胸部力量训练"}',
                },
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("帮我制定周一胸部训练计划")
        self.assertTrue(intent.create_training_plan)
        self.assertEqual(intent.training_plan_subject, "胸部训练")
        self.assertEqual(intent.training_plan_title, "周一胸部力量训练")

    def test_save_existing_plan_is_not_routed_as_new_plan(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {
                    "name": "save_existing_training_plan",
                    "arguments": '{"date":"2026-08-18","subject":"背部训练"}',
                },
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("今天是8-18，为我保存今天的练背计划")
        self.assertTrue(intent.save_existing_training_plan)
        self.assertFalse(intent.create_training_plan)
        self.assertEqual(intent.saved_plan_date, "2026-08-18")
        self.assertEqual(intent.saved_plan_subject, "背部训练")

    def test_plan_tool_without_title_is_rejected(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {"name": "create_training_plan", "arguments": '{"subject":"胸部训练"}'},
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("帮我制定胸部训练计划")
        self.assertEqual(intent, self.router.ChatIntent())

    def test_profile_question_without_tool_call_never_updates_profile(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {
                "choices": [{"message": {"tool_calls": []}}]
            }
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("我的可用器械有哪些？")
        self.assertEqual(intent.profile_updates, {})

    def test_personal_information_query_uses_read_only_profile_tool(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {"name": "view_profile", "arguments": "{}"},
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("查看我的个人信息和训练偏好")
        self.assertTrue(intent.view_profile)
        self.assertEqual(intent.profile_updates, {})

    def test_explicit_profile_declaration_uses_update_tool(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {
                "choices": [{"message": {"tool_calls": [{
                    "function": {
                        "name": "update_profile",
                        "arguments": '{"equipment_change":{"mode":"replace","items":["哑铃","哑铃凳","跳绳"]}}',
                    }
                }]}}]
            }
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent(
                "我的可用器械有哑铃、哑铃凳、跳绳"
            )
        self.assertEqual(
            intent.profile_updates["equipment_change"],
            {"mode": "replace", "items": ["哑铃", "哑铃凳", "跳绳"]},
        )

    def test_multiple_supported_tool_calls_are_rejected(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post"
        ) as post:
            post.return_value.json.return_value = {
                "choices": [{"message": {"tool_calls": [
                    {"function": {
                        "name": "update_profile",
                        "arguments": '{"equipment":["哑铃","跳绳"]}',
                    }},
                    {"function": {
                        "name": "view_training_records",
                        "arguments": '{}',
                    }},
                ]}}]
            }
            post.return_value.raise_for_status.return_value = None

            intent = self.router.route_chat_intent("我的器械有哑铃和跳绳，查看训练记录")

        self.assertEqual(intent, self.router.ChatIntent())
        self.assertFalse(post.call_args.kwargs["json"].get("parallel_tool_calls", True))

    def test_failure_or_disabled_router_does_not_guess_action(self) -> None:
        self.assertEqual(
            self.router.route_chat_intent("查看训练数据", allow_external_models=False),
            self.router.ChatIntent(),
        )
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(
            self.router.requests, "post", side_effect=self.router.requests.RequestException
        ):
            self.assertEqual(
                self.router.route_chat_intent("我的可用器械有哪些？"),
                self.router.ChatIntent(),
            )


    def test_create_plan_returns_temporary_constraints_and_clarification(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(self.router.requests, "post") as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {"name": "create_training_plan", "arguments": '{"subject":"背部训练","title":"今日计划","schedule_decision":"override","temporary_health_constraints":["今天腰部不适"],"excluded_subjects":["背部训练"],"needs_clarification":false}'},
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("今天腰部不适，不练背")
        self.assertEqual(intent.temporary_health_constraints, ["今天腰部不适"])
        self.assertEqual(intent.excluded_subjects, ["背部训练"])
        self.assertFalse(intent.needs_clarification)

    def test_create_plan_schema_allows_every_consumed_plan_field(self) -> None:
        create_plan = next(
            item["function"] for item in self.router.TOOLS
            if item["function"]["name"] == "create_training_plan"
        )
        properties = create_plan["parameters"]["properties"]
        self.assertTrue(
            {
                "schedule_decision", "temporary_health_constraints",
                "excluded_subjects", "needs_clarification",
            }.issubset(properties),
        )
        self.assertFalse(create_plan["parameters"]["additionalProperties"])

    def test_non_array_plan_constraint_fields_are_discarded(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(self.router.requests, "post") as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {"name": "create_training_plan", "arguments": '{"subject":"腿部训练","title":"今日计划","temporary_health_constraints":"膝盖疼","excluded_subjects":"背部训练"}'},
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("帮我安排今天的腿部训练")
        self.assertTrue(intent.create_training_plan)
        self.assertEqual(intent.temporary_health_constraints, [])
        self.assertEqual(intent.excluded_subjects, [])

    def test_compound_plan_targets_are_preserved(self) -> None:
        with patch.object(self.router, "ROUTER_API_KEY", "test-key"), patch.object(self.router.requests, "post") as post:
            post.return_value.json.return_value = {"choices": [{"message": {"tool_calls": [{
                "function": {"name": "create_training_plan", "arguments": '{"subject":"有氧和核心训练","title":"今日有氧核心计划","schedule_decision":"override","requested_subjects":["有氧","核心"]}'},
            }]}}]}
            post.return_value.raise_for_status.return_value = None
            intent = self.router.route_chat_intent("今天做有氧和核心训练，给我生成对应的计划")
        self.assertEqual(intent.requested_subjects, ["有氧", "核心"])
        self.assertEqual(intent.schedule_decision, "override")
if __name__ == "__main__":
    unittest.main()
