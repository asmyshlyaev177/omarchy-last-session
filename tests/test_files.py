"""replace_file writes a temp file beside its target and renames it into place,
so the temp file's name is the one a planted symlink would need."""

import os
import tempfile
import unittest
from unittest import mock

from omarchy_last_session import files
from tests.helpers import mode_of


class ReplaceFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "Preferences")

    def test_a_symlink_planted_under_a_guessed_temp_name_is_skipped(self):
        bashrc = os.path.join(self.dir.name, "bashrc")
        with open(bashrc, "w") as f:
            f.write("export EDITOR=nvim\n")
        os.chmod(bashrc, 0o600)
        os.symlink(bashrc, os.path.join(self.dir.name, "Preferences.guessed.tmp"))
        # mkstemp's first candidate is the planted name, as if its random pick had been guessed.
        names = iter(["guessed", "fresh"])
        with mock.patch.object(tempfile, "_get_candidate_names", return_value=names):
            files.replace_file(self.path, "{}", 0o644)
        self.assertEqual(list(names), [], "mkstemp never tried the planted name")
        with open(bashrc) as f:
            self.assertEqual(f.read(), "export EDITOR=nvim\n")
        self.assertEqual(mode_of(bashrc), 0o600)
        with open(self.path) as f:
            self.assertEqual(f.read(), "{}")
        self.assertEqual(mode_of(self.path), 0o644)
