import os
import time
import unittest
from unittest import mock

from omarchy_last_session import config, hypr, proc, session
from tests.helpers import BRAVE_BLOB, StateDirCase, client, grouped_clients, pretend_runnable


class SaveGuard(StateDirCase):
    """omarchy closes every window before poweroff; a daemon tick landing in
    that gap must not blank the snapshot the power menu just took."""

    def test_empty_save_keeps_existing_session(self):
        self.write_session([{"class": "code"}])
        self.assertIsNone(self.save_with([]))
        self.assertEqual(len(self.read_session()), 1)

    def test_empty_save_writes_when_no_session_yet(self):
        self.assertEqual(self.save_with([]), 0)
        self.assertEqual(self.read_session(), [])

    def test_empty_save_can_be_told_to_record_it(self):
        """The power menu runs shutdown before anything is closed, so an empty
        desktop at that point is what the user left."""
        self.write_session([{"class": "code"}])
        with mock.patch.object(hypr, "query", return_value=[]):
            self.assertEqual(session.save_session(keep_previous_when_empty=False), 0)
        self.assertEqual(self.read_session(), [])

    def test_second_window_of_one_process_is_not_respawned(self):
        two = [
            client("brave-browser", pid=7, workspace={"id": 1, "name": "1"}),
            client("brave-browser", pid=7, workspace={"id": 2, "name": "2"}),
        ]
        with pretend_runnable():
            self.assertEqual(self.save_with(two, cmdline=(BRAVE_BLOB,)), 2)
        self.assertEqual([w["spawn"] for w in self.read_session()], [True, False])

    def test_excluded_class_is_not_saved(self):
        with mock.patch.object(config, "EXCLUDE_CLASSES", {"jobbot-search"}):
            self.save_with([client("jobbot-search")])
        self.assertEqual(self.read_session(), [])


class SecondWindowOfOneProcess(StateDirCase):
    """Nautilus reopens nothing by itself, so every window of its has to be
    launched. A browser and Code reopen their own, and one process serves all
    of their windows: launching again opens a spare empty one instead."""

    def spawn_flags(self, clients):
        self.save_with(clients)
        return [w["spawn"] for w in self.read_session()]

    def test_every_window_of_an_ordinary_app_is_launched(self):
        two = [client("com.mitchellh.ghostty", pid=7), client("com.mitchellh.ghostty", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, True])

    def test_code_is_launched_once_per_process(self):
        """One process serves every Code window and it reopens them itself, so
        a second launch only adds an empty window with no project in it."""
        two = [client("code", pid=7), client("code", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, False])

    def test_nautilus_second_window_is_launched(self):
        two = [client("org.gnome.Nautilus", pid=7), client("org.gnome.Nautilus", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, True])

    def test_browser_is_still_launched_once_per_process(self):
        """Brave restores its own windows; launching again would duplicate."""
        two = [client("brave-browser", pid=7), client("brave-browser", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, False])

    def test_separate_processes_are_each_launched(self):
        two = [client("brave-browser", pid=7), client("brave-browser", pid=8)]
        self.assertEqual(self.spawn_flags(two), [True, True])


class GroupCapture(StateDirCase):
    def saved(self, clients):
        self.save_with(clients)
        return self.read_session()

    def test_group_membership_is_recorded(self):
        made = grouped_clients(("code", 1, "a"), ("brave-browser", 2, "a"), ("org.gnome.Mines", 3, None))
        groups = [w["group"] for w in self.saved(made)]
        self.assertEqual(groups[0], groups[1])
        self.assertIsNone(groups[2])

    def test_two_groups_get_distinct_ids(self):
        made = grouped_clients(
            ("code", 1, "a"), ("brave-browser", 2, "a"), ("org.kde.kdenlive", 3, "b"), ("foot", 4, "b")
        )
        groups = [w["group"] for w in self.saved(made)]
        self.assertEqual(groups[0], groups[1])
        self.assertEqual(groups[2], groups[3])
        self.assertNotEqual(groups[0], groups[2])

    def test_a_group_of_one_is_not_a_group(self):
        made = grouped_clients(("code", 1, "a"))
        self.assertIsNone(self.saved(made)[0]["group"])


class MonitorCapture(StateDirCase):
    def save_with_monitors(self, clients, monitors):
        def fake_query(cmd):
            return {"clients": clients, "monitors": monitors}[cmd]

        with (
            mock.patch.object(hypr, "query", side_effect=fake_query),
            mock.patch.object(proc, "read_cmdline", return_value=["/bin/sh"]),
        ):
            session.save_session()
        return self.read_session()[0]

    def test_monitor_name_is_recorded_from_the_id_map(self):
        monitors = [{"id": 0, "name": "eDP-1"}, {"id": 1, "name": "DP-9", "x": 1920, "y": 0}]
        saved = self.save_with_monitors([client("code", monitor=1)], monitors)
        self.assertEqual(saved["monitor_name"], "DP-9")
        self.assertEqual(saved["monitor_at"], [1920, 0])

    def test_unknown_monitor_id_records_an_empty_name(self):
        saved = self.save_with_monitors([client("code", monitor=9)], [{"id": 0, "name": "eDP-1"}])
        self.assertEqual(saved["monitor_name"], "")


class QuitSessionKeepingApps(unittest.TestCase):
    """Chromium only keeps its tabs if it exits cleanly, so shutdown has to
    signal it and wait rather than let the compositor kill it."""

    def quit(self, clients, timeout, alive, kill=None):
        """Returns (pids still alive at the end, pids signalled)."""
        with (
            mock.patch.object(hypr, "query", return_value=clients),
            mock.patch.object(os, "kill", side_effect=kill) as killed,
            mock.patch.object(os.path, "exists", return_value=alive),
            mock.patch.object(time, "sleep"),
            mock.patch.object(time, "time", side_effect=[0, 1, 2]),
        ):
            stubborn = session.quit_session_keeping_apps(timeout=timeout)
        return stubborn, sorted(c.args[0] for c in killed.call_args_list)

    def test_signals_only_session_keeping_apps(self):
        clients = [
            client("brave-browser", pid=11),
            client("com.mitchellh.ghostty", pid=22),
            client("org.gnome.Nautilus", pid=33),
            client("code", pid=44),
        ]
        _, signalled = self.quit(clients, timeout=0, alive=False)
        self.assertEqual(
            signalled,
            [11, 44],
            "Code has to be asked to quit too, or the folder opened last is never written",
        )

    def test_reports_apps_that_outlive_the_timeout(self):
        stubborn, _ = self.quit([client("brave-browser", pid=11)], timeout=1, alive=True)
        self.assertEqual(stubborn, {11})

    def test_reports_nothing_when_everything_exits(self):
        stubborn, _ = self.quit([client("brave-browser", pid=11)], timeout=5, alive=False)
        self.assertEqual(stubborn, set())

    def test_a_process_that_is_already_gone_is_not_reported(self):
        stubborn, _ = self.quit(
            [client("brave-browser", pid=11)], timeout=5, alive=True, kill=ProcessLookupError
        )
        self.assertEqual(stubborn, set())
