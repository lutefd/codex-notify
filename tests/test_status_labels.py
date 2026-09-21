from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from status_labels import LabelError, get_label, list_labels, remember_identifier, set_label


class StatusLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = {
            "CODEX_NOTIFY_LABELS_FILE": str(Path(self.directory.name) / "labels.json")
        }

    def test_codex_and_claude_ids_are_namespaced_locally(self) -> None:
        remember_identifier("codex", "same-id", environ=self.environment)
        remember_identifier("claude", "same-id", environ=self.environment)
        set_label("codex", "same-id", "Build API", environ=self.environment)
        set_label("claude", "same-id", "Research", environ=self.environment)
        self.assertEqual(get_label("codex", "same-id", environ=self.environment), "Build API")
        self.assertEqual(get_label("claude", "same-id", environ=self.environment), "Research")
        self.assertEqual(list_labels(environ=self.environment)["codex"]["same-id"], "Build API")

    def test_unsafe_labels_are_rejected(self) -> None:
        for label in ("private\ntext", " leading", "trailing ", "x" * 49):
            with self.assertRaises(LabelError):
                set_label("codex", "thread-1", label, environ=self.environment)

    def test_registry_file_is_private(self) -> None:
        set_label("codex", "thread-1", "Build", environ=self.environment)
        if os.name != "nt":
            self.assertEqual(os.stat(self.environment["CODEX_NOTIFY_LABELS_FILE"]).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
