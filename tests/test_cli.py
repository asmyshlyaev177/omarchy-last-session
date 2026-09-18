import io
import os
import sys
import unittest
from unittest import mock

from omarchy_last_session import cli, hypr, proc, session
from tests.helpers import StateDirCase, client


class Shutdown(StateDirCase):
    """session.json is overwritten shortly after the next login, so the
    shutdown copy is what a post-reboot comparison has to read."""

    def run_shutdown(self, clients):
        with (
            mock.patch.object(hypr, "query", return_value=clients),
            mock.patch.object(proc, "read_cmdline", return_value=["/bin/sh"]),
            mock.patch.object(session, "quit_session_keeping_apps", return_value=set()),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            return cli.main(["shutdown"])

    def test_shutdown_keeps_a_copy_of_the_snapshot(self):
        self.assertEqual(self.run_shutdown([client("code"), client("foot")]), 0)
        self.assertEqual(sorted(w["class"] for w in self.read_session(self.copy)), ["code", "foot"])

    def test_copy_matches_the_snapshot_exactly(self):
        self.run_shutdown([client("code")])
        with open(self.session) as a, open(self.copy) as b:
            self.assertEqual(a.read(), b.read())

    def test_plain_save_does_not_touch_the_copy(self):
        """Only a clean exit updates it; the 60-second daemon must not."""
        self.save_with([client("code")])
        self.assertFalse(os.path.exists(self.copy))

    def test_an_empty_desktop_at_shutdown_is_recorded_as_such(self):
        """Everything was closed before the power menu ran, so nothing should
        come back. The daemon's minute-old snapshot used to be kept instead."""
        self.write_session([{"class": "code"}])
        self.assertEqual(self.run_shutdown([]), 0)
        self.assertEqual(self.read_session(), [])
        self.assertEqual(self.read_session(self.copy), [])


class Usage(unittest.TestCase):
    def test_unknown_command_prints_usage_and_fails(self):
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertEqual(cli.main(["bogus"]), 1)
        self.assertIn("restore", err.getvalue())

    def test_no_command_prints_usage_and_fails(self):
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(cli.main([]), 1)
