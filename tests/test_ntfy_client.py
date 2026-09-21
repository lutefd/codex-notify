from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ntfy_client import load_settings, publish


class FakeResponse:
    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class NtfyClientTests(unittest.TestCase):
    environment = {
        "NTFY_BASE_URL": "http://ntfy",
        "NTFY_TOPIC": "private_topic",
        "NTFY_TOKEN": "publisher_token",
        "NTFY_ALLOW_INSECURE_HTTP": "1",
    }

    def test_private_http_requires_explicit_opt_in(self) -> None:
        with self.assertRaises(ValueError):
            load_settings({key: value for key, value in self.environment.items() if key != "NTFY_ALLOW_INSECURE_HTTP"})

    def test_publish_is_fixed_and_authenticated(self) -> None:
        settings = load_settings(self.environment)
        observed: list[object] = []

        def opener(request: object, timeout: float) -> FakeResponse:
            observed.append((request, timeout))
            return FakeResponse()

        publish(settings, opener=opener)
        request, timeout = observed[0]
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.full_url, "http://ntfy/private_topic")
        self.assertEqual(request.data, b"Codex turn completed.")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer publisher_token")
        self.assertEqual(request.headers["Title"], "Codex")

    def test_claude_message_uses_fixed_source_text_and_safe_label(self) -> None:
        settings = load_settings(self.environment)
        observed: list[object] = []

        def opener(request: object, timeout: float) -> FakeResponse:
            observed.append((request, timeout))
            return FakeResponse()

        publish(settings, source="claude", label="Research", opener=opener)
        request, _timeout = observed[0]
        self.assertEqual(request.data, b"Claude turn completed. [Research]")
        self.assertEqual(request.headers["Title"], "Claude [Research]")

    def test_invalid_label_is_rejected_before_network(self) -> None:
        settings = load_settings(self.environment)
        opener = Mock()
        with self.assertRaises(ValueError):
            publish(settings, source="claude", label="private\ntext", opener=opener)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
