#!/usr/bin/env python3
"""Authenticated, status-only HTTP relay for the private ntfy service."""

from __future__ import annotations

import hmac
import json
import os
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping
from urllib.parse import urlsplit

from codex_notify import SUPPORTED_EVENT, _read_token
from ntfy_client import NtfyDeliveryError, NtfySettings, load_settings, publish


PUBLIC_PATH = "/v1/codex/turn-complete"
HEALTH_PATH = "/healthz"
MAX_BODY_BYTES = 1024


class GatewayRequestError(ValueError):
    """A request failed authentication or the exact event schema."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GatewaySettings:
    token: str
    ntfy: NtfySettings
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080


def validate_event_payload(raw: bytes) -> None:
    """Require the exact event object and reject arbitrary text fields."""

    if len(raw) > MAX_BODY_BYTES:
        raise GatewayRequestError(413, "request too large")
    try:
        event = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GatewayRequestError(400, "invalid event") from exc
    if not isinstance(event, dict) or set(event) != {"type"}:
        raise GatewayRequestError(400, "invalid event")
    if event.get("type") != SUPPORTED_EVENT:
        raise GatewayRequestError(400, "invalid event")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    event: dict[str, object] = {}
    for key, value in pairs:
        if key in event:
            raise ValueError("duplicate JSON key")
        event[key] = value
    return event


def authorize(header: str | None, token: str) -> None:
    expected = f"Bearer {token}"
    if header is None or not hmac.compare_digest(header, expected):
        raise GatewayRequestError(401, "unauthorized")


def process_event(
    raw: bytes,
    authorization: str | None,
    token: str,
    *,
    publisher: Callable[[], None],
) -> None:
    """Validate a request and invoke a publisher with no request content."""

    authorize(authorization, token)
    validate_event_payload(raw)
    try:
        publisher()
    except NtfyDeliveryError as exc:
        raise GatewayRequestError(502, "notification backend unavailable") from exc


def _port(environ: Mapping[str, str]) -> int:
    raw = environ.get("CODEX_NOTIFY_GATEWAY_PORT", "8080").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("CODEX_NOTIFY_GATEWAY_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("CODEX_NOTIFY_GATEWAY_PORT must be between 1 and 65535")
    return port


def load_gateway_settings(environ: Mapping[str, str] | None = None) -> GatewaySettings:
    env = os.environ if environ is None else environ
    return GatewaySettings(
        token=_read_token(env, "CODEX_NOTIFY_GATEWAY_TOKEN", "CODEX_NOTIFY_GATEWAY_TOKEN_FILE"),
        ntfy=load_settings(env),
        listen_host=env.get("CODEX_NOTIFY_GATEWAY_HOST", "0.0.0.0").strip() or "0.0.0.0",
        listen_port=_port(env),
    )


class GatewayHandler(BaseHTTPRequestHandler):
    """HTTP handler with no request-body or token logging."""

    protocol_version = "HTTP/1.0"

    def _respond(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if body:
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path == HEALTH_PATH and not parsed.query and not parsed.fragment:
            self._respond(200, b"ok\n")
        else:
            self._respond(404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if parsed.path != PUBLIC_PATH or parsed.query or parsed.fragment:
            self._respond(404)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            content_length = -1
        if content_length < 0:
            self._respond(411)
            return
        if content_length > MAX_BODY_BYTES:
            self._respond(413)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._respond(415)
            return
        try:
            raw = self.rfile.read(content_length)
            process_event(
                raw,
                self.headers.get("Authorization"),
                self.server.settings.token,  # type: ignore[attr-defined]
                publisher=lambda: publish(self.server.settings.ntfy),  # type: ignore[attr-defined]
            )
        except GatewayRequestError as exc:
            self._respond(exc.status)
            return
        self._respond(204)

    def log_message(self, _format: str, *_args: object) -> None:
        # Request paths, headers, and bodies stay out of logs by design.
        return None


class GatewayServer(ThreadingHTTPServer):
    settings: GatewaySettings


def serve(settings: GatewaySettings) -> None:
    server = GatewayServer((settings.listen_host, settings.listen_port), GatewayHandler)
    server.settings = settings
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> int:
    try:
        serve(load_gateway_settings())
    except (ValueError, OSError) as exc:
        print(f"codex-notify-gateway: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
