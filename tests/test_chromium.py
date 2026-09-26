"""Chromium marks its exit as a crash from startup until it quits cleanly, and
refuses to restore the last session after one. The mark is cleared before a
relaunch; the session file itself survived the kill."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

from omarchy_last_session import chromium, config
from tests.helpers import mode_of


class ProfileCase(unittest.TestCase):
    """Brave's default user data directory, under a temporary config home."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(config, "CONFIG_HOME", self.dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user_data = os.path.join(self.dir.name, "BraveSoftware", "Brave-Browser")

    def write_prefs(self, profile, prefs, user_data=None):
        path = os.path.join(user_data or self.user_data, profile, "Preferences")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(prefs, f)
        os.chmod(path, 0o600)
        return path

    def read_prefs(self, path):
        with open(path) as f:
            return json.load(f)

    def mark_quietly(self):
        """The count of profiles marked, and what was warned on the way."""
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            return chromium.mark_clean_exit("brave-browser", "brave"), err.getvalue()


class MarkCleanExit(ProfileCase):
    def test_a_crashed_profile_is_marked_normal_and_nothing_else_changes(self):
        prefs = {"profile": {"exit_type": "Crashed", "name": "Me"}, "other": [1, "é"]}
        path = self.write_prefs("Default", prefs)
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "/opt/brave-bin/brave"), 1)
        prefs["profile"]["exit_type"] = "Normal"
        self.assertEqual(self.read_prefs(path), prefs)
        self.assertEqual(mode_of(path), 0o600)

    def test_every_profile_is_marked(self):
        self.write_prefs("Default", {"profile": {"exit_type": "Crashed"}})
        self.write_prefs("Profile 1", {"profile": {"exit_type": "Crashed"}})
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "brave"), 2)

    def test_a_clean_profile_is_left_untouched(self):
        path = self.write_prefs("Default", {"profile": {"exit_type": "Normal"}})
        before = os.stat(path).st_mtime_ns
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "brave"), 0)
        self.assertEqual(os.stat(path).st_mtime_ns, before)

    def test_the_user_data_dir_flag_is_honoured(self):
        elsewhere = os.path.join(self.dir.name, "work profile")
        path = self.write_prefs("Default", {"profile": {"exit_type": "Crashed"}}, user_data=elsewhere)
        cmd = f"brave --user-data-dir='{elsewhere}' --restore-last-session"
        self.assertEqual(chromium.mark_clean_exit("brave-browser", cmd), 1)
        self.assertEqual(self.read_prefs(path)["profile"]["exit_type"], "Normal")

    def test_a_browser_never_run_is_a_no_op(self):
        self.assertEqual(chromium.mark_clean_exit("chromium", "chromium"), 0)

    def test_a_missing_profile_section_is_created(self):
        path = self.write_prefs("Default", {"other": 1})
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "brave"), 1)
        self.assertEqual(self.read_prefs(path), {"other": 1, "profile": {"exit_type": "Normal"}})

    def test_unreadable_preferences_are_reported_and_left_alone(self):
        path = self.write_prefs("Default", {})
        with open(path, "w") as f:
            f.write("{not json")
        count, err = self.mark_quietly()
        self.assertEqual(count, 0)
        self.assertIn("could not mark", err)
        with open(path) as f:
            self.assertEqual(f.read(), "{not json")

    def test_rewritten_preferences_are_readable_only_by_the_user(self):
        path = self.write_prefs("Default", {"profile": {"exit_type": "Crashed"}})
        os.chmod(path, 0o644)
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "brave"), 1)
        self.assertEqual(mode_of(path), 0o600)

    def test_a_failed_write_leaves_preferences_as_they_were_and_no_temp_file(self):
        path = self.write_prefs("Default", {"profile": {"exit_type": "Crashed"}})
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            count, err = self.mark_quietly()
        self.assertEqual(count, 0)
        self.assertIn("disk full", err)
        self.assertEqual(os.listdir(os.path.dirname(path)), ["Preferences"])
        self.assertEqual(self.read_prefs(path)["profile"]["exit_type"], "Crashed")


class TamperedProfile(ProfileCase):
    """A profile directory can be writable by something the user trusts less than
    this plugin, such as a sandboxed app or another user sharing a
    --user-data-dir, so nothing planted in it may redirect a read or a write."""

    def setUp(self):
        super().setUp()
        self.bashrc = os.path.join(self.dir.name, "bashrc")
        with open(self.bashrc, "w") as f:
            f.write("export EDITOR=nvim\n")
        os.chmod(self.bashrc, 0o600)

    def plant_symlink_at_the_old_temp_name(self, path):
        """The writer used to derive its temp name from the pid, so anyone could
        plant a link there ahead of a restore."""
        os.symlink(self.bashrc, f"{path}.{os.getpid()}.tmp")

    def empty_profile(self):
        """Where Default's Preferences goes, with nothing there yet."""
        os.makedirs(os.path.join(self.user_data, "Default"))
        return os.path.join(self.user_data, "Default", "Preferences")

    def test_a_symlink_planted_at_the_temp_name_is_not_written_through(self):
        path = self.write_prefs("Default", {"profile": {"exit_type": "Crashed"}})
        self.plant_symlink_at_the_old_temp_name(path)
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "brave"), 1)
        with open(self.bashrc) as f:
            self.assertEqual(f.read(), "export EDITOR=nvim\n")
        self.assertFalse(os.path.islink(path))
        self.assertEqual(self.read_prefs(path)["profile"]["exit_type"], "Normal")

    def test_a_symlink_planted_at_the_temp_name_is_not_chmodded_through(self):
        path = self.write_prefs("Default", {"profile": {"exit_type": "Crashed"}})
        # A mode other than the 0600 the rewrite sets, so a chmod through the link would show.
        os.chmod(self.bashrc, 0o644)
        self.plant_symlink_at_the_old_temp_name(path)
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "brave"), 1)
        self.assertEqual(mode_of(self.bashrc), 0o644)

    def test_a_symlink_in_place_of_preferences_is_neither_read_nor_written_through(self):
        elsewhere = os.path.join(self.dir.name, "other.json")
        with open(elsewhere, "w") as f:
            json.dump({"profile": {"exit_type": "Crashed"}}, f)
        path = self.empty_profile()
        os.symlink(elsewhere, path)
        count, err = self.mark_quietly()
        self.assertEqual(count, 0)
        self.assertIn("could not mark", err)
        self.assertTrue(os.path.islink(path), "the link was replaced by a copy of what it points at")
        self.assertEqual(self.read_prefs(elsewhere)["profile"]["exit_type"], "Crashed")

    def test_a_fifo_in_place_of_preferences_is_refused_without_blocking(self):
        os.mkfifo(self.empty_profile())
        marked = []
        # Opening a FIFO waits for a writer, so a regression fails on the join's
        # timeout instead of hanging the suite.
        worker = threading.Thread(
            target=lambda: marked.append(chromium.mark_clean_exit("brave-browser", "brave")), daemon=True
        )
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            worker.start()
            worker.join(timeout=5)
        self.assertEqual(marked, [0], "opening the FIFO blocked")
        self.assertIn("is not a regular file", err.getvalue())
