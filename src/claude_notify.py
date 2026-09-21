#!/usr/bin/env python3
"""Send a fixed status event from a Claude Code ``Stop`` hook.

Claude provides response text, transcript paths, working directories, and
session identifiers to hooks.  This helper reads only the hook name, the
loop-guard flag, and the opaque session identifier used for a local label
lookup.  It never reads or forwards response content or transcript files.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TextIO

from codex_notify import ConfigurationError, DeliveryError, RelaySettings, load_settings, publish
from status_labels import LabelError, get_label, remember_identifier, validate_label


HOOK_EVENT = "Stop"


@dataclass(frozen=True)
class HookNotification:
    session_id: str | None


def parse_hook(raw: str) -> HookNotification | None:
    """Return the opaque session id for a safe, non-recursive Stop event."""

    try:
        event: Any = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("Claude hook input is not valid JSON") from exc
    if not isinstance(event, dict):
        raise ConfigurationError("Claude hook input must be a JSON object")
    if event.get("hook_event_name") != HOOK_EVENT:
        return None
    if "stop_hook_active" in event and event.get("stop_hook_active") is not False:
        return None
    session_id = event.get("session_id")
    return HookNotification(session_id if isinstance(session_id, str) else None)


def _input_argument(argv: Sequence[str], stdin: TextIO) -> str:
    if len(argv) > 1:
        return argv[1]
    return stdin.read()


def _label(environ: Mapping[str, str] | None, session_id: str | None) -> str | None:
    env = os.environ if environ is None else environ
    configured = env.get("CLAUDE_NOTIFY_LABEL", "").strip()
    if configured:
        try:
            return validate_label(configured)
        except LabelError as exc:
            raise ConfigurationError(str(exc)) from exc
    return get_label("claude", session_id, environ=environ)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stderr: TextIO | None = None,
    opener: Any = None,
) -> int:
    """Run the hook without ever blocking Claude's turn completion.

    Claude treats exit status 2 as a blocking Stop decision.  This notifier
    returns zero for ignored, configuration, and delivery-error paths so a
    notification outage cannot make Claude continue or loop.
    """

    args = sys.argv if argv is None else argv
    input_stream = sys.stdin if stdin is None else stdin
    error_stream = sys.stderr if stderr is None else stderr
    try:
        notification = parse_hook(_input_argument(args, input_stream))
        if notification is None:
            return 0
        session_id = notification.session_id
        settings: RelaySettings = load_settings(environ)
        remember_identifier("claude", session_id, environ=environ)
        label = _label(environ, session_id)
        kwargs = {"source": "claude", "label": label}
        if opener is not None:
            kwargs["opener"] = opener
        publish(settings, **kwargs)
    except (ConfigurationError, DeliveryError):
        print("claude-notify: notification was not delivered", file=error_stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
