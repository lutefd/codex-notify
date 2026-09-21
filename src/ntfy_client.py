"""Small fixed-payload ntfy client used by the local relay."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from status_labels import LabelError, validate_label, validate_source

MESSAGE_BY_SOURCE = {
    "codex": "Codex turn completed.",
    "claude": "Claude turn completed.",
}
TITLE_BY_SOURCE = {
    "codex": "Codex",
    "claude": "Claude",
}
MESSAGE = MESSAGE_BY_SOURCE["codex"]
TITLE = TITLE_BY_SOURCE["codex"]
DEFAULT_TIMEOUT_SECONDS = 10.0
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NtfyConfigurationError(ValueError):
    """Raised when the relay's private ntfy settings are unsafe or incomplete."""


class NtfyDeliveryError(RuntimeError):
    """Raised when ntfy cannot accept the fixed status message."""


@dataclass(frozen=True)
class NtfySettings:
    base_url: str
    topic: str
    token: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    title: str = TITLE

    @property
    def endpoint(self) -> str:
        parsed = urlsplit(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}/{self.topic}"


def _required_text(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise NtfyConfigurationError(f"{name} is required")
    if "\r" in value or "\n" in value:
        raise NtfyConfigurationError(f"{name} must not contain newlines")
    return value


def _read_token(environ: Mapping[str, str]) -> str:
    token = environ.get("NTFY_TOKEN", "").strip()
    token_file = environ.get("NTFY_TOKEN_FILE", "").strip()
    if token and token_file:
        raise NtfyConfigurationError("set only one of NTFY_TOKEN and NTFY_TOKEN_FILE")
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise NtfyConfigurationError("NTFY_TOKEN_FILE could not be read") from exc
    if not token:
        raise NtfyConfigurationError("NTFY_TOKEN or NTFY_TOKEN_FILE is required")
    if any(char.isspace() for char in token):
        raise NtfyConfigurationError("ntfy token must be a single non-whitespace value")
    return token


def load_settings(environ: Mapping[str, str] | None = None) -> NtfySettings:
    """Load settings for the relay-to-ntfy private hop.

    The compose deployment normally uses HTTP on an isolated Docker network.
    ``NTFY_ALLOW_INSECURE_HTTP=1`` is therefore explicit and should never be
    used for a public URL; the public work-computer hop is HTTPS-only in
    ``codex_notify.py``.
    """

    env = os.environ if environ is None else environ
    base_url = _required_text(env, "NTFY_BASE_URL").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise NtfyConfigurationError("NTFY_BASE_URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NtfyConfigurationError("NTFY_BASE_URL must not contain credentials, query, or fragment")
    if parsed.scheme == "http" and env.get("NTFY_ALLOW_INSECURE_HTTP", "") not in {
        "1",
        "true",
        "yes",
    }:
        raise NtfyConfigurationError("HTTP ntfy transport requires NTFY_ALLOW_INSECURE_HTTP=1")

    topic = _required_text(env, "NTFY_TOPIC")
    if not _TOPIC_RE.fullmatch(topic):
        raise NtfyConfigurationError("NTFY_TOPIC must contain only letters, numbers, '-' or '_'")
    title = env.get("NTFY_TITLE", TITLE).strip() or TITLE
    if "\r" in title or "\n" in title or len(title) > 120:
        raise NtfyConfigurationError("NTFY_TITLE must be a short single-line value")

    raw_timeout = env.get("NTFY_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise NtfyConfigurationError("NTFY_TIMEOUT_SECONDS must be a number") from exc
    if not 0.1 <= timeout <= 60:
        raise NtfyConfigurationError("NTFY_TIMEOUT_SECONDS must be between 0.1 and 60")

    return NtfySettings(
        base_url=base_url,
        topic=topic,
        token=_read_token(env),
        timeout_seconds=timeout,
        title=title,
    )


def publish(
    settings: NtfySettings,
    *,
    source: str = "codex",
    label: str | None = None,
    opener: Callable[..., Any] = urlopen,
) -> None:
    """Publish only a fixed source message with an optional safe label."""

    try:
        safe_source = validate_source(source)
        safe_label = None if label is None else validate_label(label)
    except LabelError as exc:
        raise NtfyConfigurationError(str(exc)) from exc
    message = MESSAGE_BY_SOURCE[safe_source]
    title = TITLE_BY_SOURCE[safe_source]
    if safe_label is not None:
        message = f"{message} [{safe_label}]"
        title = f"{title} [{safe_label}]"

    request = Request(
        settings.endpoint,
        data=message.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.token}",
            "Content-Type": "text/plain; charset=utf-8",
            "Title": title,
            "Priority": "default",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=settings.timeout_seconds) as response:
            status = getattr(response, "status", None) or response.getcode()
    except HTTPError as exc:
        raise NtfyDeliveryError(f"ntfy rejected the notification (HTTP {exc.code})") from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise NtfyDeliveryError("ntfy could not be reached") from exc

    if not 200 <= status < 300:
        raise NtfyDeliveryError(f"ntfy rejected the notification (HTTP {status})")
