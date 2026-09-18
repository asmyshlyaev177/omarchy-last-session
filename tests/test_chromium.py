"""Chromium marks its exit as a crash from startup until it quits cleanly, and
refuses to restore the last session after one. The mark is cleared before a
relaunch; the session file itself survived the kill."""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from omarchy_last_session import chromium, config


class MarkCleanExit(unittest.TestCase):
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

    def test_a_crashed_profile_is_marked_normal_and_nothing_else_changes(self):
        prefs = {"profile": {"exit_type": "Crashed", "name": "Me"}, "other": [1, "é"]}
        path = self.write_prefs("Default", prefs)
        self.assertEqual(chromium.mark_clean_exit("brave-browser", "/opt/brave-bin/brave"), 1)
        prefs["profile"]["exit_type"] = "Normal"
        self.assertEqual(self.read_prefs(path), prefs)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

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
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertEqual(chromium.mark_clean_exit("brave-browser", "brave"), 0)
        self.assertIn("could not mark", err.getvalue())
        with open(path) as f:
            self.assertEqual(f.read(), "{not json")
