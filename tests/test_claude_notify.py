from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import claude_notify
from status_labels import set_label


class FakeResponse:
    status = 204

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status


class ClaudeNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.labels_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.labels_dir.cleanup)
        self.environment = {
            "CODEX_NOTIFY_URL": "https://notify.example.test/v1/codex/turn-complete",
            "CODEX_NOTIFY_TOKEN": "relay_test_value_that_never_leaves_the_process",
            "CODEX_NOTIFY_LABELS_FILE": str(Path(self.labels_dir.name) / "labels.json"),
        }
        self.event = json.dumps(
            {
                "hook_event_name": "Stop",
                "stop_hook_active": False,
                "session_id": "session-123",
                "cwd": "/private/worktree",
                "transcript_path": "/private/transcript.jsonl",
                "last_assistant_message": "private response",
            }
        )

    def test_stop_publishes_claude_source_and_local_label_only(self) -> None:
        set_label("claude", "session-123", "Research", environ=self.environment)
        requests: list[object] = []

        def opener(request: object, timeout: float) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse()

        result = claude_notify.main(
            ["claude-notify"],
            environ=self.environment,
            stdin=io.StringIO(self.event),
            opener=opener,
        )

        self.assertEqual(result, 0)
        request, timeout = requests[0]
        self.assertEqual(timeout, 10.0)
        self.assertEqual(
            request.data,
            b'{"type":"agent-turn-complete","source":"claude","label":"Research"}',
        )
        self.assertNotIn("session-123", request.data.decode())
        self.assertNotIn("private", request.data.decode())

    def test_active_stop_hook_is_ignored_without_configuration_or_network(self) -> None:
        event = json.dumps(
            {
                "hook_event_name": "Stop",
                "stop_hook_active": True,
                "session_id": "session-123",
                "last_assistant_message": "private response",
            }
        )
        opener = Mock()
        result = claude_notify.main(
            ["claude-notify"],
            environ={},
            stdin=io.StringIO(event),
            opener=opener,
        )
        self.assertEqual(result, 0)
        opener.assert_not_called()

    def test_non_stop_hook_is_ignored(self) -> None:
        opener = Mock()
        result = claude_notify.main(
            ["claude-notify"],
            environ={},
            stdin=io.StringIO(json.dumps({"hook_event_name": "Notification", "session_id": "private"})),
            opener=opener,
        )
        self.assertEqual(result, 0)
        opener.assert_not_called()

    def test_stop_without_session_id_still_sends_unlabeled_fixed_event(self) -> None:
        requests: list[object] = []

        def opener(request: object, timeout: float) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse()

        result = claude_notify.main(
            ["claude-notify"],
            environ=self.environment,
            stdin=io.StringIO(json.dumps({"hook_event_name": "Stop", "stop_hook_active": False})),
            opener=opener,
        )
        self.assertEqual(result, 0)
        request, _timeout = requests[0]
        self.assertEqual(request.data, b'{"type":"agent-turn-complete","source":"claude"}')

    def test_delivery_error_never_blocks_or_echoes_hook_content(self) -> None:
        error = io.StringIO()

        def opener(_request: object, timeout: float) -> FakeResponse:
            response_error = HTTPError(
                "https://notify.example.test/v1/codex/turn-complete",
                403,
                "private response",
                {},
                None,
            )
            response_error.close()
            raise response_error

        result = claude_notify.main(
            ["claude-notify"],
            environ=self.environment,
            stdin=io.StringIO(self.event),
            stderr=error,
            opener=opener,
        )
        self.assertEqual(result, 0)
        self.assertEqual(error.getvalue().strip(), "claude-notify: notification was not delivered")
        self.assertNotIn("private", error.getvalue())


if __name__ == "__main__":
    unittest.main()
