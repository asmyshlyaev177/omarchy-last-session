import io
import itertools
import os
import sys
import time
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
        come back."""
        self.write_session([{"class": "code"}])
        self.assertEqual(self.run_shutdown([]), 0)
        self.assertEqual(self.read_session(), [])
        self.assertEqual(self.read_session(self.copy), [])


class Daemon(unittest.TestCase):
    """The loop sleeps on the event stream, looks at the desktop each time it
    wakes, and stops when the compositor goes away."""

    A = {"0xa": {"class": "code"}}
    AB = {"0xa": {"class": "code"}, "0xb": {"class": "foot"}}

    def run_wakes(self, layouts, save=None):
        """One wake per layout, then the compositor goes. Returns the saves
        made, the sleep the loop asked for each time, and stderr."""
        waits = []

        def wait(stream, timeout):
            waits.append(timeout)
            return len(waits) < len(layouts)

        with (
            mock.patch.object(time, "sleep"),
            mock.patch.object(time, "monotonic", side_effect=itertools.count(1000, 5)),
            mock.patch.object(hypr, "open_event_stream", return_value="stream"),
            mock.patch.object(hypr, "wait_for_placement_change", side_effect=wait),
            mock.patch.object(hypr, "get_layout", side_effect=layouts),
            mock.patch.object(session, "save_session", side_effect=save or itertools.count(1)) as saved,
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            cli.run_daemon()
        return saved.call_count, waits, err.getvalue()

    def test_saves_on_the_first_look_and_then_only_on_a_change(self):
        saves, _, _ = self.run_wakes([self.A, self.A, self.AB])
        self.assertEqual(saves, 2)

    def test_it_waits_for_the_compositor_rather_than_looking_on_a_timer(self):
        _, waits, _ = self.run_wakes([self.A, self.A])
        self.assertEqual(len(waits), 2)
        self.assertTrue(all(wait > 0 for wait in waits), waits)

    def test_it_stops_when_the_compositor_goes_away(self):
        """Its windows are closing; saving now would record a teardown."""
        saves, waits, _ = self.run_wakes([self.A])
        self.assertEqual((saves, len(waits)), (1, 1))

    def test_a_failed_save_is_reported_and_tried_again(self):
        saves, _, err = self.run_wakes([self.A, self.A], save=[OSError("disk full"), 1])
        self.assertEqual(saves, 2)
        self.assertIn("save failed: disk full", err)


class Usage(unittest.TestCase):
    def test_unknown_command_prints_usage_and_fails(self):
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertEqual(cli.main(["bogus"]), 1)
        self.assertIn("restore", err.getvalue())

    def test_no_command_prints_usage_and_fails(self):
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(cli.main([]), 1)
