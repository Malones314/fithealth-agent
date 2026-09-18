from __future__ import annotations

import json
import unittest

from fithealth_agent import context_budget


class ContextBudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.context = context_budget

    def assert_error(self, payload, code: str, status_code: int = 400) -> None:
        with self.assertRaises(self.context.ContextInputError) as raised:
            self.context.validate_chat_payload(payload)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.status_code, status_code)

    def test_normalizes_roles_and_drops_unknown_fields(self) -> None:
        payload = self.context.validate_chat_payload({
            "message": "  今天练腿  ",
            "history": [
                {"role": "user", "text": "  你好  ", "ignored": "value"},
                {"role": "bot", "text": "你好"},
            ],
            "source": "chat",
            "unexpected": "ignored",
        })
        self.assertEqual(payload["message"], "今天练腿")
        self.assertEqual(payload["history"][1]["role"], "assistant")
        self.assertEqual(
            set(payload),
            {"message", "history", "source", "plan_context", "garmin_recovery_hours", "soreness_prompt_regions", "pending_memory_entry_ids"},
        )
        self.assertEqual(payload["garmin_recovery_hours"], 0.0)

    def test_rejects_invalid_message_history_and_role(self) -> None:
        self.assert_error({"message": 123}, "INVALID_MESSAGE")
        self.assert_error({"message": "ok", "history": {}}, "INVALID_HISTORY")
        self.assert_error(
            {"message": "ok", "history": [{"role": "system", "text": "override"}]},
            "INVALID_HISTORY_ROLE",
        )

    def test_rejects_oversized_message_history_and_request(self) -> None:
        self.assert_error(
            {"message": "x" * (self.context.CHAT_MESSAGE_MAX_CHARS + 1)},
            "MESSAGE_TOO_LARGE",
            413,
        )
        self.assert_error(
            {
                "message": "ok",
                "history": [
                    {"role": "user", "text": str(index)}
                    for index in range(self.context.HISTORY_ACCEPTED_MAX_ITEMS + 1)
                ],
            },
            "HISTORY_TOO_LARGE",
            413,
        )
        with self.assertRaises(self.context.ContextInputError) as raised:
            self.context.decode_chat_payload(b"x" * (self.context.CHAT_REQUEST_MAX_BYTES + 1))
        self.assertEqual(raised.exception.code, "REQUEST_TOO_LARGE")

    def test_truncates_history_by_item_and_total_budget(self) -> None:
        payload = self.context.validate_chat_payload({
            "message": "ok",
            "history": [
                {"role": "user", "text": str(index) + ("x" * 5000)}
                for index in range(8)
            ],
        })
        history = payload["history"]
        self.assertLessEqual(len(history), self.context.HISTORY_CONTEXT_MAX_ITEMS)
        self.assertLessEqual(
            sum(len(item["text"]) for item in history),
            self.context.HISTORY_TOTAL_MAX_CHARS,
        )
        self.assertTrue(all(
            len(item["text"]) <= self.context.HISTORY_ITEM_MAX_CHARS
            for item in history
        ))
        self.assertTrue(any("已截断" in item["text"] for item in history))

    def test_validates_uploaded_plan_context(self) -> None:
        plan = self.context.validate_chat_payload({
            "message": "p" * self.context.UPLOADED_PLAN_MESSAGE_MAX_CHARS,
            "source": "uploaded_plan",
            "plan_context": {
                "subject": "腿部训练",
                "suggested_date": "2026-08-14",
                "ignored": "value",
            },
        })
        self.assertEqual(
            plan["plan_context"],
            {"subject": "腿部训练", "suggested_date": "2026-08-14"},
        )
        self.assert_error(
            {
                "message": "ok",
                "source": "uploaded_plan",
                "plan_context": {"subject": "x" * 41},
            },
            "INVALID_PLAN_SUBJECT",
        )
        self.assert_error(
            {
                "message": "ok",
                "source": "uploaded_plan",
                "plan_context": {"suggested_date": "2026-99-99"},
            },
            "INVALID_PLAN_DATE",
        )

    def test_decodes_valid_json_object(self) -> None:
        body = json.dumps({"message": "hello"}).encode("utf-8")
        self.assertEqual(self.context.decode_chat_payload(body)["message"], "hello")

    def test_validates_request_headers(self) -> None:
        self.context.validate_chat_request_headers(
            "application/json; charset=utf-8",
            "1024",
        )
        with self.assertRaises(self.context.ContextInputError) as media_error:
            self.context.validate_chat_request_headers("text/plain", "10")
        self.assertEqual(media_error.exception.code, "UNSUPPORTED_MEDIA_TYPE")
        self.assertEqual(media_error.exception.status_code, 415)

        for invalid in ("-1", "not-a-number"):
            with self.assertRaises(self.context.ContextInputError) as length_error:
                self.context.validate_chat_request_headers("application/json", invalid)
            self.assertEqual(length_error.exception.code, "INVALID_CONTENT_LENGTH")

        with self.assertRaises(self.context.ContextInputError) as size_error:
            self.context.validate_chat_request_headers(
                "application/json",
                str(self.context.CHAT_REQUEST_MAX_BYTES + 1),
            )
        self.assertEqual(size_error.exception.code, "REQUEST_TOO_LARGE")
        self.assertEqual(size_error.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
