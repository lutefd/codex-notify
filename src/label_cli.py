"""Manage the local opaque-session-to-label registry."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from status_labels import LabelError, list_labels, remove_label, set_label


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-notify-label",
        description="Map Codex thread IDs or Claude session IDs to local safe labels.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("set", "assign or replace a local label"),
        ("remove", "remove a local label"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--source", choices=("codex", "claude"), required=True)
        command.add_argument("--id", required=True, help="opaque thread/session identifier")
        if name == "set":
            command.add_argument("--label", required=True)
    commands.add_parser("list", help="list observed IDs and local labels")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "set":
            set_label(args.source, args.id, args.label)
            print("label saved locally")
        elif args.command == "remove":
            print("label removed locally" if remove_label(args.source, args.id) else "no label found")
        else:
            registry = list_labels()
            for source in ("codex", "claude"):
                for identifier, label in sorted(registry[source].items()):
                    print(f"{source}\t{identifier}\t{label or '<unlabeled>'}")
    except LabelError as exc:
        print(f"codex-notify-label: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
