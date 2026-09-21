"""Local mapping of opaque agent session identifiers to safe labels.

The mapping never leaves the host.  The relay receives only the selected
label, after the gateway validates its small printable allowlist.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Mapping


LABEL_MAX_LENGTH = 48
IDENTIFIER_MAX_LENGTH = 256
SUPPORTED_SOURCES = frozenset({"codex", "claude"})
_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._-]{0,46}[A-Za-z0-9])?$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
LABELS_FILE_ENV = "CODEX_NOTIFY_LABELS_FILE"


class LabelError(ValueError):
    """Raised when a local source, identifier, or label is unsafe."""


def validate_source(source: object) -> str:
    if not isinstance(source, str) or source not in SUPPORTED_SOURCES:
        raise LabelError("source must be codex or claude")
    return source


def validate_identifier(identifier: object) -> str:
    if not isinstance(identifier, str) or not _IDENTIFIER_RE.fullmatch(identifier):
        raise LabelError("session identifier must be a short opaque value")
    return identifier


def validate_label(label: object) -> str:
    if not isinstance(label, str) or not _LABEL_RE.fullmatch(label):
        raise LabelError(
            "label must be 1-48 characters using letters, numbers, spaces, '.', '_' or '-'")
    return label


def labels_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get(LABELS_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        appdata = env.get("APPDATA", "").strip()
        root = Path(appdata or Path.home() / "AppData" / "Roaming")
    else:
        root = Path.home() / ".config"
    return root / "codex-notify" / "labels.json"


def _empty_registry() -> dict[str, dict[str, str | None]]:
    return {"codex": {}, "claude": {}}


def _read_registry(path: Path) -> dict[str, dict[str, str | None]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return _empty_registry()
    if not isinstance(raw, dict):
        return _empty_registry()
    registry = _empty_registry()
    for source in SUPPORTED_SOURCES:
        entries = raw.get(source)
        if not isinstance(entries, dict):
            continue
        for identifier, label in entries.items():
            try:
                safe_identifier = validate_identifier(identifier)
                safe_label = None if label is None else validate_label(label)
            except LabelError:
                continue
            registry[source][safe_identifier] = safe_label
    return registry


def _write_registry(path: Path, registry: dict[str, dict[str, str | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(registry, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def get_label(source: str, identifier: str | None, *, environ: Mapping[str, str] | None = None) -> str | None:
    """Return a local label for an opaque source identifier, if registered."""

    try:
        safe_source = validate_source(source)
        if identifier is None:
            return None
        safe_identifier = validate_identifier(identifier)
    except LabelError:
        return None
    label = _read_registry(labels_path(environ))[safe_source].get(safe_identifier)
    return label if isinstance(label, str) else None


def remember_identifier(
    source: str,
    identifier: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Remember an observed opaque identifier without assigning a label."""

    if identifier is None:
        return
    try:
        safe_source = validate_source(source)
        safe_identifier = validate_identifier(identifier)
    except LabelError:
        return
    path = labels_path(environ)
    registry = _read_registry(path)
    registry[safe_source].setdefault(safe_identifier, None)
    try:
        _write_registry(path, registry)
    except OSError:
        # Label discovery is best effort; a read-only home must not suppress
        # the fixed completion notification.
        return


def set_label(
    source: str,
    identifier: str,
    label: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    safe_source = validate_source(source)
    safe_identifier = validate_identifier(identifier)
    safe_label = validate_label(label)
    path = labels_path(environ)
    registry = _read_registry(path)
    registry[safe_source][safe_identifier] = safe_label
    _write_registry(path, registry)
    return path


def remove_label(
    source: str,
    identifier: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    safe_source = validate_source(source)
    safe_identifier = validate_identifier(identifier)
    path = labels_path(environ)
    registry = _read_registry(path)
    if safe_identifier not in registry[safe_source]:
        return False
    del registry[safe_source][safe_identifier]
    _write_registry(path, registry)
    return True


def list_labels(*, environ: Mapping[str, str] | None = None) -> dict[str, dict[str, str | None]]:
    """Return a sanitized copy suitable for local CLI display."""

    return _read_registry(labels_path(environ))
