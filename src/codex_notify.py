#!/usr/bin/env python3
"""Send a status-only Codex completion event to the public relay.

Codex passes one JSON object to the configured ``notify`` command. The
notifier deliberately keeps only the event type and sends a fixed schema to
the relay; it never forwards prompts, assistant output, the working
directory, or any other event fields.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from status_labels import LabelError, get_label, remember_identifier, validate_label, validate_source


SUPPORTED_EVENT = "agent-turn-complete"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class CompletionNotification:
    """The only Codex metadata retained locally by the helper."""

    source: str
    identifier: str | None


class ConfigurationError(ValueError):
    """Raised when the notifier configuration is missing or unsafe."""


class DeliveryError(RuntimeError):
    """Raised when the relay rejects or cannot receive the notification."""


@dataclass(frozen=True)
class RelaySettings:
    url: str
    token: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    allow_insecure_http: bool = False


def parse_event(raw: str) -> str | None:
    """Accept only a Codex completion event and discard every other field.

    ``None`` means that the event is valid JSON but not one this notifier is
    intended to deliver. Keeping this function's return value a constant is
    an intentional data-flow boundary: caller supplied event fields cannot
    become relay content.
    """

    return (
        SUPPORTED_EVENT
        if parse_notification(raw) is not None
        else None
    )


def parse_notification(raw: str) -> CompletionNotification | None:
    """Parse Codex input while retaining only its opaque session identifier."""

    try:
        event: Any = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("Codex notification input is not valid JSON") from exc

    if not isinstance(event, dict):
        raise ConfigurationError("Codex notification input must be a JSON object")
    if event.get("type") != SUPPORTED_EVENT:
        return None
    identifier = event.get("thread-id")
    if not isinstance(identifier, str):
        identifier = None
    return CompletionNotification(source="codex", identifier=identifier)


def _required_text(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    if "\r" in value or "\n" in value:
        raise ConfigurationError(f"{name} must not contain newlines")
    return value


def _read_token(environ: Mapping[str, str], value_name: str, file_name: str) -> str:
    token = environ.get(value_name, "").strip()
    token_file = environ.get(file_name, "").strip()
    if token and token_file:
        raise ConfigurationError(f"set only one of {value_name} and {file_name}")
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise ConfigurationError(f"{file_name} could not be read") from exc
    if not token:
        raise ConfigurationError(f"{value_name} or {file_name} is required")
    if "\r" in token or "\n" in token or any(char.isspace() for char in token):
        raise ConfigurationError("notification token must be a single non-whitespace value")
    return token


def _parse_timeout(environ: Mapping[str, str]) -> float:
    raw = environ.get("CODEX_NOTIFY_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ConfigurationError("CODEX_NOTIFY_TIMEOUT_SECONDS must be a number") from exc
    if not 0.1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ConfigurationError("CODEX_NOTIFY_TIMEOUT_SECONDS must be between 0.1 and 60")
    return timeout


def _validate_url(url: str, allow_insecure_http: bool) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ConfigurationError("CODEX_NOTIFY_URL must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ConfigurationError("CODEX_NOTIFY_URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("CODEX_NOTIFY_URL must not contain a query or fragment")
    if parsed.scheme == "http":
        if not allow_insecure_http or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ConfigurationError("insecure HTTP is allowed only for localhost tests")


def load_settings(environ: Mapping[str, str] | None = None) -> RelaySettings:
    """Load relay settings from the environment; production requires HTTPS."""

    env = os.environ if environ is None else environ
    url = _required_text(env, "CODEX_NOTIFY_URL")
    allow_insecure_http = env.get("CODEX_NOTIFY_ALLOW_INSECURE_HTTP", "").lower() in {
        "1",
        "true",
        "yes",
    }
    _validate_url(url, allow_insecure_http)
    return RelaySettings(
        url=url,
        token=_read_token(env, "CODEX_NOTIFY_TOKEN", "CODEX_NOTIFY_TOKEN_FILE"),
        timeout_seconds=_parse_timeout(env),
        allow_insecure_http=allow_insecure_http,
    )


def publish(
    settings: RelaySettings,
    *,
    source: str = "codex",
    label: str | None = None,
    opener: Callable[..., Any] = urlopen,
) -> None:
    """POST a fixed completion event and an optional safe local label."""

    try:
        safe_source = validate_source(source)
        safe_label = None if label is None else validate_label(label)
    except LabelError as exc:
        raise ConfigurationError(str(exc)) from exc
    payload: dict[str, str] = {"type": SUPPORTED_EVENT}
    if safe_source != "codex":
        payload["source"] = safe_source
    if safe_label is not None:
        payload["label"] = safe_label
    event_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    request = Request(
        settings.url,
        data=event_payload,
        headers={
            "Authorization": f"Bearer {settings.token}",
            "Content-Type": "application/json",
            "User-Agent": "codex-notify/1.0",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=settings.timeout_seconds) as response:
            status = getattr(response, "status", None) or response.getcode()
    except HTTPError as exc:
        raise DeliveryError(f"relay rejected the notification (HTTP {exc.code})") from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise DeliveryError("notification relay could not be reached") from exc

    if not 200 <= status < 300:
        raise DeliveryError(f"relay rejected the notification (HTTP {status})")


def _input_argument(argv: Sequence[str], stdin: TextIO) -> str:
    if len(argv) > 1:
        return argv[1]
    return stdin.read()


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stderr: TextIO | None = None,
    opener: Callable[..., Any] = urlopen,
) -> int:
    """CLI entrypoint; diagnostics intentionally contain no event content."""

    args = sys.argv if argv is None else argv
    input_stream = sys.stdin if stdin is None else stdin
    error_stream = sys.stderr if stderr is None else stderr

    try:
        notification = parse_notification(_input_argument(args, input_stream))
        if notification is None:
            return 0
        settings = load_settings(environ)
        remember_identifier(notification.source, notification.identifier, environ=environ)
        env = os.environ if environ is None else environ
        configured_label = env.get("CODEX_NOTIFY_LABEL", "").strip()
        label = configured_label or get_label(
            notification.source,
            notification.identifier,
            environ=environ,
        )
        if configured_label:
            try:
                label = validate_label(configured_label)
            except LabelError as exc:
                raise ConfigurationError(str(exc)) from exc
        publish(settings, source=notification.source, label=label, opener=opener)
    except (ConfigurationError, DeliveryError) as exc:
        print(f"codex-notify: {exc}", file=error_stream)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
