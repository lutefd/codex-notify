from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import codex_notify


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status


class NotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event = json.dumps(
            {
                "type": codex_notify.SUPPORTED_EVENT,
                "thread-id": "private-thread-id",
                "cwd": "/private/worktree",
                "input-messages": ["private prompt"],
                "last-assistant-message": "private response",
            }
        )
        self.environment = {
            "CODEX_NOTIFY_URL": "https://notify.example.test/v1/codex/turn-complete",
            "CODEX_NOTIFY_TOKEN": "relay_test_value_that_never_leaves_the_process",
        }

    def test_event_parser_discards_all_content_except_supported_type(self) -> None:
        self.assertEqual(codex_notify.parse_event(self.event), codex_notify.SUPPORTED_EVENT)
        self.assertIsNone(codex_notify.parse_event('{"type":"other"}'))

    def test_unsupported_event_does_not_require_configuration_or_network(self) -> None:
        opener = unittest.mock.Mock()
        result = codex_notify.main(
            ["codex-notify", '{"type":"approval-requested","cwd":"private"}'],
            environ={},
            opener=opener,
        )
        self.assertEqual(result, 0)
        opener.assert_not_called()

    def test_publish_uses_fixed_schema_and_bearer_auth(self) -> None:
        requests: list[object] = []

        def opener(request: object, timeout: float) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse()

        result = codex_notify.main(
            ["codex-notify", self.event],
            environ=self.environment,
            opener=opener,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(requests), 1)
        request, timeout = requests[0]
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.full_url, "https://notify.example.test/v1/codex/turn-complete")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b'{"type":"agent-turn-complete"}')
        self.assertEqual(request.headers["Authorization"], f"Bearer {self.environment['CODEX_NOTIFY_TOKEN']}")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(request.headers["User-agent"], "codex-notify/1.0")
        self.assertNotIn("private", request.data.decode())

    def test_token_file_is_supported_without_printing_token(self) -> None:
        with patch.object(codex_notify.Path, "read_text", return_value="relay_file_value"):
            environment = {
                "CODEX_NOTIFY_URL": self.environment["CODEX_NOTIFY_URL"],
                "CODEX_NOTIFY_TOKEN_FILE": "/private/token-file",
            }
            settings = codex_notify.load_settings(environment)
        self.assertEqual(settings.token, "relay_file_value")

    def test_http_requires_explicit_opt_in(self) -> None:
        environment = dict(self.environment, CODEX_NOTIFY_URL="http://127.0.0.1:8080")
        with self.assertRaises(codex_notify.ConfigurationError):
            codex_notify.load_settings(environment)
        settings = codex_notify.load_settings(
            dict(environment, CODEX_NOTIFY_ALLOW_INSECURE_HTTP="1")
        )
        self.assertTrue(settings.allow_insecure_http)

    def test_sensitive_fields_never_appear_in_error(self) -> None:
        error = io.StringIO()
        result = codex_notify.main(
            ["codex-notify", self.event],
            environ={},
            stderr=error,
        )
        self.assertEqual(result, 1)
        self.assertNotIn("private", error.getvalue())
        self.assertNotIn("thread-id", error.getvalue())

    def test_http_error_is_reduced_to_status_only(self) -> None:
        error = io.StringIO()

        def opener(_request: object, timeout: float) -> FakeResponse:
            response_error = HTTPError(
                "https://notify.example.test/v1/codex/turn-complete",
                403,
                "secret body",
                {},
                None,
            )
            response_error.close()
            raise response_error

        result = codex_notify.main(
            ["codex-notify", self.event],
            environ=self.environment,
            stderr=error,
            opener=opener,
        )
        self.assertEqual(result, 1)
        self.assertEqual(error.getvalue().strip(), "codex-notify: relay rejected the notification (HTTP 403)")
        self.assertNotIn("secret body", error.getvalue())


if __name__ == "__main__":
    unittest.main()
