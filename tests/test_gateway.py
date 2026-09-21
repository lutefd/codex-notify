from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_notify import SUPPORTED_EVENT
from codex_notify_gateway import (
    GatewayHandler,
    GatewayRequestError,
    GatewayServer,
    GatewaySettings,
    authorize,
    process_event,
    validate_event_payload,
)
from ntfy_client import NtfySettings


class GatewayTests(unittest.TestCase):
    token = "gateway-test-token"
    event = json.dumps({"type": SUPPORTED_EVENT}, separators=(",", ":")).encode()

    def test_exact_schema_is_accepted(self) -> None:
        self.assertEqual(validate_event_payload(self.event), ("codex", None))

    def test_claude_source_and_safe_label_are_accepted(self) -> None:
        event = b'{"type":"agent-turn-complete","source":"claude","label":"Research"}'
        self.assertEqual(validate_event_payload(event), ("claude", "Research"))

    def test_prompt_and_other_fields_are_rejected(self) -> None:
        for body in (
            b'{"type":"agent-turn-complete","message":"private"}',
            b'{"type":"agent-turn-complete","cwd":"/private"}',
            b'{"type":"approval-requested"}',
            b'{"type":"agent-turn-complete","type":"agent-turn-complete"}',
            b'{"type":"agent-turn-complete","source":"other"}',
            b'{"type":"agent-turn-complete","label":"private\ntext"}',
            b'{"type":"agent-turn-complete","label":null}',
            b'{"type":"agent-turn-complete","label":"' + b"x" * 49 + b'"}',
        ):
            with self.assertRaises(GatewayRequestError) as error:
                validate_event_payload(body)
            self.assertEqual(error.exception.status, 400)

    def test_authentication_is_required_before_publish(self) -> None:
        published = []
        with self.assertRaises(GatewayRequestError) as error:
            process_event(
                self.event,
                "Bearer wrong",
                self.token,
                publisher=lambda: published.append(True),
            )
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(published, [])

    def test_valid_event_publishes_without_forwarding_request_body(self) -> None:
        published = []
        process_event(
            self.event,
            f"Bearer {self.token}",
            self.token,
            publisher=lambda: published.append(True),
        )
        self.assertEqual(published, [True])

    def test_claude_event_publishes_only_source_and_label_metadata(self) -> None:
        published: list[object] = []

        process_event(
            b'{"type":"agent-turn-complete","source":"claude","label":"Research"}',
            f"Bearer {self.token}",
            self.token,
            publisher=lambda source, label: published.append((source, label)),
        )
        self.assertEqual(published, [("claude", "Research")])

    def test_auth_compare_is_exact(self) -> None:
        authorize(f"Bearer {self.token}", self.token)
        with self.assertRaises(GatewayRequestError):
            authorize(f"bearer {self.token}", self.token)

    def test_http_route_enforces_auth_and_schema(self) -> None:
        published: list[object] = []
        settings = GatewaySettings(
            token=self.token,
            ntfy=NtfySettings("http://ntfy", "private_topic", "publisher_token"),
            listen_host="127.0.0.1",
            listen_port=0,
        )
        server = GatewayServer((settings.listen_host, settings.listen_port), GatewayHandler)
        server.settings = settings
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/codex/turn-complete"
            request = Request(
                endpoint,
                data=self.event,
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                method="POST",
            )
            with patch("codex_notify_gateway.publish", lambda _settings: published.append(True)):
                with urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 204)
                self.assertEqual(published, [True])

                invalid = Request(
                    endpoint,
                    data=b'{"type":"agent-turn-complete","message":"private"}',
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                self.assertEqual(self._http_error_code(invalid), 400)
                self.assertEqual(published, [True])

                unauthorized = Request(
                    endpoint,
                    data=self.event,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                self.assertEqual(self._http_error_code(unauthorized), 401)
                self.assertEqual(published, [True])

                claude_published: list[object] = []
                claude = Request(
                    endpoint,
                    data=b'{"type":"agent-turn-complete","source":"claude","label":"Research"}',
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with patch(
                    "codex_notify_gateway.publish",
                    lambda _settings, **kwargs: claude_published.append(kwargs),
                ):
                    with urlopen(claude, timeout=2) as response:
                        self.assertEqual(response.status, 204)
                self.assertEqual(claude_published, [{"source": "claude", "label": "Research"}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @staticmethod
    def _http_error_code(request: Request) -> int:
        try:
            urlopen(request, timeout=2)
        except HTTPError as error:
            code = error.code
            error.close()
            return code
        raise AssertionError("request unexpectedly succeeded")


if __name__ == "__main__":
    unittest.main()
