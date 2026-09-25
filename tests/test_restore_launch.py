import io
import itertools
import sys
import time
import unittest
from unittest import mock

from omarchy_last_session import chromium, config, hypr, proc
from omarchy_last_session.restore import launch
from tests.helpers import RestoreHarness, live_window, saved_window


class ExecRules(unittest.TestCase):
    def test_floating_window_carries_geometry(self):
        rules = launch.build_exec_rules(saved_window("x", floating=True, at=(10, 20), size=(300, 400)), {})
        self.assertIn("float = true", rules)
        self.assertIn("move = {10, 20}", rules)
        self.assertIn("size = {300, 400}", rules)
        self.assertIn("'2 silent'", rules)

    def test_floating_position_is_relative_to_the_saved_monitor(self):
        """Hyprland reads the move rule relative to the monitor the window
        opens on, and keeps that offset when the workspace changes monitor."""
        rules = launch.build_exec_rules(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9"), {"DP-9": (1920, 0)}
        )
        self.assertIn("move = {330, 485}", rules)

    def test_unknown_monitor_keeps_the_absolute_position(self):
        rules = launch.build_exec_rules(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="gone"), {"DP-9": (1920, 0)}
        )
        self.assertIn("move = {2250, 485}", rules)

    def test_tiled_window_has_no_geometry(self):
        rules = launch.build_exec_rules(saved_window("x", at=(10, 20), size=(300, 400)), {})
        self.assertNotIn("float", rules)
        self.assertNotIn("move", rules)

    def test_pinned_window_is_pinned_by_rule(self):
        self.assertIn("pin = true", launch.build_exec_rules(saved_window("x", pinned=True), {}))


class RestoreSpawning(RestoreHarness):
    def test_only_spawn_entries_are_launched(self):
        """Extra windows of one process are left to the sweep."""
        self.write_session(
            [saved_window("brave-browser", spawn=True), saved_window("brave-browser", spawn=False)]
        )
        self.assertEqual(len([e for e in self.run_restore([{}]) if "exec_cmd" in e]), 1)

    def test_tiled_windows_spawn_before_floating(self):
        self.write_session(
            [
                saved_window("floaty", ws=1, floating=True, at=(0, 0)),
                saved_window("tiled", ws=1, floating=False, at=(500, 0)),
            ]
        )
        emitted = self.run_restore([{}])
        self.assertIn("tiled", emitted[0])
        self.assertIn("floaty", emitted[1])

    def test_workspaces_spawn_in_order(self):
        self.write_session([saved_window("second", ws=5), saved_window("first", ws=1)])
        emitted = self.run_restore([{}])
        self.assertIn("first", emitted[0])
        self.assertIn("second", emitted[1])

    def test_dispatch_carries_silent_workspace_and_command(self):
        self.write_session([saved_window("code", ws=3, cmd="/usr/share/code/code")])
        emitted = self.run_restore([{}])[0]
        self.assertIn("[[/usr/share/code/code]]", emitted)
        self.assertIn("'3 silent'", emitted)

    def test_command_containing_long_bracket_is_escaped(self):
        self.write_session([saved_window("odd", cmd="sh -c ]]")])
        emitted = self.run_restore([{}])[0]
        self.assertIn("[=[sh -c ]]]=]", emitted)


class BrowserRelaunch(RestoreHarness):
    """Chromium refuses to restore its session after an unclean exit, and a
    browser the power menu killed has recorded one, so its profiles are marked
    as cleanly exited before it is launched."""

    def test_a_browser_is_marked_cleanly_exited_before_its_launch(self):
        self.write_session([saved_window("brave-browser", cmd="/opt/brave-bin/brave --restore-last-session")])
        emitted = self.run_restore([{}])
        self.assertEqual(
            emitted[0], "mark_clean_exit brave-browser /opt/brave-bin/brave --restore-last-session"
        )
        self.assertIn("exec_cmd", emitted[1])

    def test_a_browser_already_running_is_left_alone(self):
        """Its profile is in use, and the launch only adds a window to it."""
        self.write_session([saved_window("brave-browser")])
        already = {"0xb": {"class": "brave-browser", "workspace": {"id": 1}, "at": [0, 0], "floating": False}}
        emitted = self.run_restore([already, already])
        self.assertFalse(any(e.startswith("mark_clean_exit") for e in emitted))

    def test_other_apps_are_not_marked(self):
        self.write_session([saved_window("code"), saved_window("foot")])
        self.assertFalse(any(e.startswith("mark_clean_exit") for e in self.run_restore([{}])))

    def test_one_browser_process_is_marked_once(self):
        self.write_session(
            [saved_window("brave-browser", ws=1), saved_window("brave-browser", ws=2, spawn=False)]
        )
        marks = [e for e in self.run_restore([{}]) if e.startswith("mark_clean_exit")]
        self.assertEqual(len(marks), 1)


class LaunchedInParallel(RestoreHarness):
    """Every window is launched before any of them is waited for, so a slow
    app overlaps with the others instead of holding up the queue."""

    def test_group_members_carry_their_own_workspace_rule(self):
        """They used to be launched bare, one at a time, so that a focused
        group would absorb each one as it opened."""
        self.write_session(
            [dict(saved_window("code", ws=2), group=0), dict(saved_window("foot", ws=2), group=0)]
        )
        launches = [e for e in self.run_restore([{}]) if "exec_cmd" in e]
        self.assertEqual(len(launches), 2)
        self.assertTrue(all("workspace = '2 silent'" in e for e in launches))

    def test_nothing_is_placed_or_grouped_until_everything_is_launched(self):
        windows = [
            dict(saved_window("code", ws=2), group=0),
            dict(saved_window("foot", ws=2), group=0),
            saved_window("ghostty", ws=5),
        ]
        self.write_session(windows)
        settled = {
            "0xa": live_window("code", ws=2),
            "0xb": live_window("foot", ws=2),
            "0xg": live_window("ghostty", ws=1),
        }
        emitted = self.run_restore([{}, settled], sweep_timeout=5)
        last_launch = max(i for i, e in enumerate(emitted) if "exec_cmd" in e)
        after = [e for e in emitted[last_launch + 1 :] if "window.move" in e or "group" in e]
        self.assertTrue(after)
        self.assertFalse(any("window.move" in e or "group" in e for e in emitted[:last_launch]))

    def test_a_browser_is_still_launched_once_per_process(self):
        """Its other windows come back from its own session; a second launch
        would duplicate them."""
        self.write_session(
            [
                dict(saved_window("brave-browser", ws=2), group=0),
                dict(saved_window("brave-browser", ws=5, spawn=False), group=1),
            ]
        )
        emitted = self.run_restore([{}])
        self.assertEqual(len([e for e in emitted if "exec_cmd" in e]), 1)

    def test_a_window_that_never_turns_up_is_reported(self):
        self.write_session([saved_window("ghost", ws=2)])
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.run_restore([{}], sweep_timeout=3)
        self.assertIn("no window turned up for ghost", err.getvalue())


class BrowserBeforeItsWebApps(unittest.TestCase):
    """A web app launched while its browser is not running starts the browser
    itself, without --restore-last-session, and Chromium ignores that flag on a
    browser already running: it opens a new tab and none of the session."""

    CHROME = "/opt/google/chrome/chrome"

    def launch(self, windows, views):
        sent = []

        def poll():
            sent.append("poll")
            return views.pop(0) if len(views) > 1 else views[0]

        with (
            mock.patch.object(hypr, "get_managed_clients", side_effect=poll),
            mock.patch.object(hypr, "dispatch", side_effect=sent.append),
            mock.patch.object(chromium, "mark_clean_exit"),
            mock.patch.object(proc, "read_cmdline", return_value=[self.CHROME]),
            mock.patch.object(time, "sleep"),
            mock.patch.object(time, "time", side_effect=itertools.count(1001)),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            launch.launch_saved_windows(launch.sort_for_launch(windows), {}, set())
        return ["poll" if line == "poll" else line.split("--")[-1].split("]]")[0] for line in sent]

    def test_the_browser_is_launched_first_and_its_web_app_once_it_has_a_window(self):
        windows = [
            saved_window(
                "chrome-web.whatsapp.com__-Default",
                ws=-98,
                cmd=f"{self.CHROME} --app=https://web.whatsapp.com/",
            ),
            saved_window("google-chrome", ws=3, cmd=f"{self.CHROME} --restore-last-session"),
        ]
        chrome_up = {"0xc": {"class": "google-chrome", "pid": 7}}
        self.assertEqual(
            self.launch(windows, [{}, {}, chrome_up]),
            ["restore-last-session", "poll", "poll", "poll", "app=https://web.whatsapp.com/"],
        )

    def test_a_browser_that_never_opens_a_window_holds_its_web_apps_only_so_long(self):
        windows = [
            saved_window("google-chrome", cmd=f"{self.CHROME} --restore-last-session"),
            saved_window(
                "chrome-web.whatsapp.com__-Default", cmd=f"{self.CHROME} --app=https://web.whatsapp.com/"
            ),
        ]
        with mock.patch.object(config, "BROWSER_START_TIMEOUT", 3):
            sent = self.launch(windows, [{}])
        self.assertEqual(sent[-1], "app=https://web.whatsapp.com/")
        self.assertLessEqual(sent.count("poll"), 3)

    def test_other_apps_are_not_held_up_by_a_starting_browser(self):
        windows = [
            saved_window("google-chrome", ws=1, cmd=f"{self.CHROME} --restore-last-session"),
            saved_window("foot", ws=2, cmd="/usr/bin/foot"),
        ]
        self.assertNotIn("poll", self.launch(windows, [{}]))
