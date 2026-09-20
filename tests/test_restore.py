"""The restore pass: spawn order, the double-restore guard, the sweep, group
rebuilding, monitor placement, and the Lua the compositor receives."""

import io
import itertools
import os
import shlex
import sys
import time
import unittest
from unittest import mock

from omarchy_last_session import chromium, config, hypr, proc, restore
from tests.helpers import StateDirCase, live_window, pretend_runnable, saved_window


class ExecRules(unittest.TestCase):
    def test_floating_window_carries_geometry(self):
        rules = restore.build_exec_rules(saved_window("x", floating=True, at=(10, 20), size=(300, 400)), {})
        self.assertIn("float = true", rules)
        self.assertIn("move = {10, 20}", rules)
        self.assertIn("size = {300, 400}", rules)
        self.assertIn("'2 silent'", rules)

    def test_floating_position_is_relative_to_the_saved_monitor(self):
        """Hyprland reads the move rule relative to the monitor the window
        opens on, and keeps that offset when the workspace changes monitor."""
        rules = restore.build_exec_rules(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9"), {"DP-9": (1920, 0)}
        )
        self.assertIn("move = {330, 485}", rules)

    def test_unknown_monitor_keeps_the_absolute_position(self):
        rules = restore.build_exec_rules(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="gone"), {"DP-9": (1920, 0)}
        )
        self.assertIn("move = {2250, 485}", rules)

    def test_tiled_window_has_no_geometry(self):
        rules = restore.build_exec_rules(saved_window("x", at=(10, 20), size=(300, 400)), {})
        self.assertNotIn("float", rules)
        self.assertNotIn("move", rules)

    def test_pinned_window_is_pinned_by_rule(self):
        self.assertIn("pin = true", restore.build_exec_rules(saved_window("x", pinned=True), {}))


class RestoreHarness(StateDirCase):
    """restore reaches the compositor through hypr.dispatch, hypr.eval_lua and
    hypr.get_managed_clients, so the whole pass is observable. The first two
    both send Lua and are recorded in one list, so their order is too."""

    def setUp(self):
        super().setUp()
        self.patch(time, "sleep", mock.Mock())
        # restore reports what it did on stdout and what it could not do on
        # stderr; a test that cares captures them itself, the rest keep both
        # out of the run
        self.patch(sys, "stdout", io.StringIO())
        self.patch(sys, "stderr", io.StringIO())
        # one monitor by default: the placement pass stays a no-op
        self.patch(hypr, "query", mock.Mock(return_value=[]))
        # never touch a real browser profile
        self.patch(chromium, "mark_clean_exit", mock.Mock(return_value=0))

    def run_restore(self, clients_sequence, sweep_timeout=0):
        """Every line of Lua restore sent, dispatched or evaluated, and each
        browser profile it marked as cleanly exited, in order.

        The sweep polls until it runs out of time, so the clock is faked (one
        second per reading) and the last client view repeats forever. Without
        both, a window that never turns up hangs or exhausts the mock.
        """
        views = list(clients_sequence)

        def next_view():
            return views.pop(0) if len(views) > 1 else views[0]

        sent = []
        with (
            mock.patch.object(config, "SWEEP_TIMEOUT", sweep_timeout),
            mock.patch.object(config, "SPAWN_STAGGER", 0),
            mock.patch.object(time, "time", side_effect=itertools.count(1001)),
            mock.patch.object(hypr, "get_managed_clients", side_effect=next_view),
            mock.patch.object(hypr, "dispatch", side_effect=sent.append),
            mock.patch.object(hypr, "eval_lua", side_effect=sent.append),
            mock.patch.object(
                chromium,
                "mark_clean_exit",
                side_effect=lambda cls, cmd: sent.append(f"mark_clean_exit {cls} {cmd}"),
            ),
        ):
            restore.restore_session()
        return sent


class RestoreGuards(RestoreHarness):
    def test_disable_flag_stops_restore(self):
        self.write_session([saved_window("code")])
        open(self.disabled, "w").close()
        self.assertEqual(self.run_restore([{}]), [])

    def test_missing_session_file(self):
        self.assertEqual(self.run_restore([{}]), [])

    def test_malformed_session_file(self):
        with open(self.session, "w") as f:
            f.write("{not json")
        self.assertEqual(self.run_restore([{}]), [])

    def test_empty_window_list(self):
        self.write_session([])
        self.assertEqual(self.run_restore([{}]), [])

    def test_aborts_when_desktop_already_populated(self):
        """The double-restore guard: more than 3 windows means a real session."""
        self.write_session([saved_window("code")])
        busy = {f"0x{i}": {"class": "x", "workspace": {"id": 1}} for i in range(4)}
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertEqual(self.run_restore([busy]), [])
        self.assertIn("aborting", err.getvalue())

    def test_proceeds_when_only_a_few_windows_open(self):
        self.write_session([saved_window("code")])
        few = {f"0x{i}": {"class": "x", "workspace": {"id": 1}} for i in range(3)}
        self.assertEqual(len(self.run_restore([few])), 1)


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


class RestoreSweep(RestoreHarness):
    """Single-instance apps ignore the spawn rules, so their windows come up
    unplaced and have to be moved afterwards."""

    def test_misplaced_window_is_moved(self):
        self.write_session([saved_window("brave-browser", ws=4)])
        landed = {"0xaa": {"class": "brave-browser", "workspace": {"id": 1}, "at": [0, 0], "floating": False}}
        emitted = self.run_restore([{}, landed], sweep_timeout=5)
        moves = [e for e in emitted if "window.move" in e and "address:0xaa" in e]
        self.assertTrue(moves)
        self.assertIn("'4'", moves[0])

    def test_correctly_placed_window_is_left_alone(self):
        self.write_session([saved_window("brave-browser", ws=4)])
        landed = {"0xaa": {"class": "brave-browser", "workspace": {"id": 4}, "at": [0, 0], "floating": False}}
        emitted = self.run_restore([{}, landed], sweep_timeout=5)
        self.assertEqual([e for e in emitted if "address:0xaa" in e], [])

    def test_window_present_before_restore_is_not_touched(self):
        self.write_session([saved_window("brave-browser", ws=4)])
        already = {
            "0xbb": {"class": "brave-browser", "workspace": {"id": 1}, "at": [0, 0], "floating": False}
        }
        emitted = self.run_restore([already, already], sweep_timeout=5)
        self.assertEqual([e for e in emitted if "address:0xbb" in e], [])

    def test_pairing_prefers_the_entry_on_the_same_workspace(self):
        """Two saved windows of one class: match the one that fits."""
        self.write_session(
            [saved_window("brave-browser", ws=1, at=(0, 0)), saved_window("brave-browser", ws=9, at=(1, 0))]
        )
        landed = {"0xaa": {"class": "brave-browser", "workspace": {"id": 9}, "at": [0, 0], "floating": False}}
        emitted = self.run_restore([{}, landed], sweep_timeout=5)
        self.assertEqual([e for e in emitted if "address:0xaa" in e], [])

    def test_a_grouped_window_is_never_moved(self):
        """Moving one member moves the whole group."""
        self.write_session([saved_window("foot", ws=5)])
        landed = {"0xf": {"class": "foot", "workspace": {"id": 1}, "grouped": ["0xf", "0xg"]}}
        emitted = self.run_restore([{}, landed], sweep_timeout=2)
        self.assertFalse(any("window.move" in e for e in emitted))

    def test_a_window_saved_tiled_that_came_back_floating_is_tiled_before_grouping(self):
        """Omarchy floats every Steam window by rule. Saved tiled in a group,
        the relaunched window floats, and a floating window cannot join a group,
        so the sweep has to tile it first."""
        mines, steam = saved_window("org.gnome.Mines", ws=1), saved_window("steam", ws=1, at=(1, 0))
        mines["group"] = steam["group"] = 0
        self.write_session([mines, steam])
        landed = {
            "0xa": {"class": "org.gnome.Mines", "workspace": {"id": 1}, "at": [0, 0], "floating": False},
            "0xb": {"class": "steam", "workspace": {"id": 1}, "at": [1, 0], "floating": True},
        }
        emitted = self.run_restore([{}, landed], sweep_timeout=5)
        tiled = next(i for i, e in enumerate(emitted) if "action = 'off'" in e and "address:0xb" in e)
        grouped = next(i for i, e in enumerate(emitted) if "group:add" in e)
        self.assertLess(tiled, grouped)
        self.assertFalse(any("address:0xa" in e and "window.float" in e for e in emitted))

    def test_group_of_one_is_still_moved(self):
        """A lone window can report itself as a group of one; the sweep must
        still move it. Only a real group of two or more is left alone."""
        self.write_session([saved_window("org.gnome.Mines", ws=17)])
        landed = {"0xb": {"class": "org.gnome.Mines", "workspace": {"id": 1}, "grouped": ["0xb"]}}
        emitted = self.run_restore([{}, landed], sweep_timeout=2)
        self.assertTrue(any("window.move" in e and "0xb" in e for e in emitted))


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


class SweepWaitsForTitles(RestoreHarness):
    """A browser window first shows up titled Untitled, New Tab or about:blank.
    Pairing it then would go by the app name alone, so the sweep gives it a
    moment to say what it shows."""

    def setUp(self):
        super().setUp()
        self.patch(config, "TITLE_SETTLE", 3)

    def brave(self, title, ws=2):
        return {
            "class": "brave-browser",
            "workspace": {"id": ws},
            "title": title,
            "at": [0, 0],
            "floating": False,
        }

    def test_a_loading_window_is_paired_once_its_title_arrives(self):
        self.write_session(
            [
                dict(saved_window("brave-browser", ws=1), title="Trade BTCUSDT - Brave"),
                dict(saved_window("brave-browser", ws=3), title="about:blank - Brave"),
            ]
        )
        loading = {"0xb": self.brave("Untitled - Brave")}
        loaded = {"0xb": self.brave("Trade BTCUSDT - Brave")}
        emitted = self.run_restore([{}, loading, loaded], sweep_timeout=10)
        moves = [e for e in emitted if "window.move" in e and "0xb" in e]
        self.assertEqual(len(moves), 1)
        self.assertIn("workspace = '1'", moves[0])

    def test_a_window_that_stays_blank_is_paired_after_the_wait(self):
        self.write_session([dict(saved_window("brave-browser", ws=3), title="about:blank - Brave")])
        emitted = self.run_restore([{}, {"0xb": self.brave("about:blank - Brave")}], sweep_timeout=10)
        self.assertTrue(any("window.move" in e and "0xb" in e and "workspace = '3'" in e for e in emitted))

    def test_the_wait_ends_with_the_sweep(self):
        """A window still loading when time runs out is placed by what it has."""
        self.patch(config, "TITLE_SETTLE", 100)
        self.write_session([dict(saved_window("brave-browser", ws=3), title="Trade - Brave")])
        emitted = self.run_restore([{}, {"0xb": self.brave("Untitled - Brave")}], sweep_timeout=5)
        self.assertTrue(any("window.move" in e and "0xb" in e for e in emitted))


class RestoreKeepsACopy(RestoreHarness):
    """session.json is overwritten soon after login, so the copy is what
    answers what restore tried to bring back."""

    def test_the_snapshot_restore_used_is_kept(self):
        self.write_session([saved_window("code")])
        self.run_restore([{}])
        with open(self.session) as a, open(self.restored) as b:
            self.assertEqual(a.read(), b.read())

    def test_an_aborted_restore_keeps_nothing(self):
        self.write_session([saved_window("code")])
        busy = {f"0x{i}": {"class": "x", "workspace": {"id": 1}} for i in range(4)}
        self.run_restore([busy])
        self.assertFalse(os.path.exists(self.restored))


class PairingIsLogged(RestoreHarness):
    """On stdout: it is an account of what was done, and stderr stays empty
    for a restore where nothing went wrong."""

    def test_each_placement_names_both_windows(self):
        self.write_session([dict(saved_window("brave-browser", ws=4), title="Trade - Brave")])
        landed = {
            "0xaa": {"class": "brave-browser", "workspace": {"id": 1}, "title": "Trade - Brave", "at": [0, 0]}
        }
        with (
            mock.patch.object(sys, "stdout", io.StringIO()) as out,
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            self.run_restore([{}, landed], sweep_timeout=5)
        line = next(line for line in out.getvalue().splitlines() if "is the saved" in line)
        self.assertIn("brave-browser 'Trade - Brave' on workspace 1", line)
        self.assertIn("from workspace 4", line)
        self.assertIn("placing it", line)
        self.assertEqual(err.getvalue(), "")


class ExcludedAtRestore(RestoreHarness):
    def test_excluded_class_in_the_snapshot_is_not_launched(self):
        """The exclusion list can grow after the snapshot was taken, and an
        autostarted app relaunched from an old snapshot comes up twice."""
        self.write_session([saved_window("omacal", ws=1), saved_window("code", ws=2)])
        with mock.patch.object(config, "EXCLUDE_CLASSES", {"omacal"}):
            emitted = self.run_restore([{}])
        launched = [e for e in emitted if "exec_cmd" in e]
        self.assertEqual(len(launched), 1)
        self.assertIn("code", launched[0])


class PairAcrossAClassShift(unittest.TestCase):
    """Chromium reports chromium-browser when restore relaunches it from a
    command line rather than from its desktop entry. The window has to be
    recognised anyway, or it is never placed and never rejoins its group."""

    CHROMIUM = "/usr/lib/chromium/chromium --restore-last-session"

    def client(self, cls, ws=2):
        return {"class": cls, "workspace": {"id": ws}, "pid": 77, "at": [0, 0], "floating": False}

    def pair(self, saved, client, argv):
        with mock.patch.object(proc, "read_cmdline", return_value=argv):
            return restore.match_saved_entry(saved, client)

    def test_the_program_matches_when_the_class_does_not(self):
        saved = [saved_window("chromium", ws=2, cmd=self.CHROMIUM)]
        self.assertIs(
            self.pair(saved, self.client("chromium-browser"), ["/usr/lib/chromium/chromium"]), saved[0]
        )

    def test_a_path_that_moved_still_matches_on_the_program_name(self):
        """An AppImage mounts somewhere new every launch."""
        saved = [saved_window("joplin", cmd="/tmp/.mount_abc/joplin")]
        self.assertIs(
            self.pair(saved, self.client("appimagekit-joplin"), ["/tmp/.mount_xyz/joplin"]), saved[0]
        )

    def test_another_program_is_not_matched(self):
        saved = [saved_window("chromium", cmd=self.CHROMIUM)]
        self.assertIsNone(self.pair(saved, self.client("zen"), ["/opt/zen/zen"]))

    def test_an_exact_class_is_preferred_over_a_shared_program(self):
        shifted = saved_window("chromium", ws=1, cmd=self.CHROMIUM)
        exact = saved_window("chromium-browser", ws=8, cmd=self.CHROMIUM)
        landed = self.client("chromium-browser")
        self.assertIs(self.pair([shifted, exact], landed, ["/usr/lib/chromium/chromium"]), exact)

    def test_the_workspace_still_breaks_a_tie_between_programs(self):
        here = saved_window("chromium", ws=2, cmd=self.CHROMIUM)
        elsewhere = saved_window("chromium", ws=9, cmd=self.CHROMIUM)
        landed = self.client("chromium-browser")
        self.assertIs(self.pair([elsewhere, here], landed, ["/usr/lib/chromium/chromium"]), here)

    def test_a_window_with_no_readable_process_is_left_unmatched(self):
        saved = [saved_window("chromium", cmd=self.CHROMIUM)]
        self.assertIsNone(self.pair(saved, self.client("chromium-browser"), None))


class PairAmongWindowsOfOneClass(unittest.TestCase):
    """A browser owns several windows of one class, so the class cannot say
    which is which and the two get filled into each other's places, and each
    then comes back on the other's monitor. Their titles tell them apart, near
    enough: a page title drifts as the page changes, it does not turn into
    another page's title."""

    BYBIT = "▼ 78087.8 | Trade BTCUSDT | Bybit Perpetual"
    KOBEISSI = 'The Kobeissi Letter on X: "BREAKING"'

    def setUp(self):
        patcher = mock.patch.object(proc, "read_cmdline", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, title, ws=2, cls="brave-browser"):
        return {
            "class": cls,
            "workspace": {"id": ws},
            "pid": 7,
            "title": title,
            "at": [0, 0],
            "floating": False,
        }

    def brave(self, ws, title):
        return dict(saved_window("brave-browser", ws=ws), title=title)

    def test_the_nearest_title_wins_over_the_workspace(self):
        """The window opened on workspace 2, where the other one belongs."""
        bybit, kobeissi = self.brave(1, self.BYBIT), self.brave(2, self.KOBEISSI)
        landed = self.client("▲ 78033.3 | Trade BTCUSDT | Bybit Perpetual", ws=2)
        self.assertIs(restore.match_saved_entry([bybit, kobeissi], landed), bybit)

    def test_each_window_takes_its_own_place(self):
        bybit, kobeissi = self.brave(1, self.BYBIT), self.brave(2, self.KOBEISSI)
        pending = [bybit, kobeissi]
        first = restore.match_saved_entry(pending, self.client(self.KOBEISSI, ws=1))
        pending.remove(first)
        self.assertIs(first, kobeissi)
        self.assertIs(restore.match_saved_entry(pending, self.client(self.BYBIT, ws=1)), bybit)

    def test_the_workspace_decides_when_there_are_no_titles(self):
        away, here = self.brave(9, ""), self.brave(2, "")
        self.assertIs(restore.match_saved_entry([away, here], self.client("", ws=2)), here)

    def test_matching_titles_fall_back_to_the_workspace(self):
        away, here = self.brave(9, "fish"), self.brave(2, "fish")
        self.assertIs(restore.match_saved_entry([away, here], self.client("fish", ws=2)), here)

    def test_a_title_the_window_no_longer_has_still_beats_a_stranger(self):
        """VS Code drops the project name when it reopens on its welcome tab."""
        study = dict(saved_window("code", ws=1), title="STUDY-PLAN.md - projects")
        other = dict(saved_window("code", ws=2), title="omarchy-last-session - Code")
        landed = self.client("STUDY-PLAN.md - projects (Workspace)", ws=2, cls="code")
        self.assertIs(restore.match_saved_entry([study, other], landed), study)


class TitleLikeness(unittest.TestCase):
    """Browsers append their own name to every title, and title a window
    Untitled, New Tab or about:blank until its page has loaded. Neither may
    count as likeness, or a window still loading pairs with whichever saved
    entry has the least in its title."""

    BYBIT = "▲ 78000.5 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"
    BLANK = "about:blank - Brave"

    def test_a_loading_title_scores_nothing_against_anything(self):
        for live in ("Untitled - Brave", "New Tab - Brave", "about:blank - Brave", "Brave"):
            with self.subTest(live=live):
                self.assertEqual(restore.score_title_likeness(self.BYBIT, live), 0.0)
                self.assertEqual(restore.score_title_likeness(self.BLANK, live), 0.0)

    def test_a_saved_placeholder_scores_nothing_too(self):
        self.assertEqual(restore.score_title_likeness(self.BLANK, "Example Domain - Brave"), 0.0)

    def test_the_app_name_does_not_count(self):
        """Two unrelated pages share ' - Brave'; the pages alone decide."""
        self.assertLess(restore.score_title_likeness("Home / X - Brave", "Example Domain - Brave"), 0.3)

    def test_a_drifted_page_title_still_matches(self):
        drifted = "▼ 80697.5 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"
        self.assertGreater(restore.score_title_likeness(self.BYBIT, drifted), 0.9)

    def test_titles_without_an_app_name_compare_whole(self):
        self.assertEqual(restore.score_title_likeness("alex@host:~", "alex@host:~"), 1.0)
        self.assertEqual(restore.score_title_likeness("", "anything"), 0.0)

    def test_a_loading_title_is_recognised(self):
        for title in ("Untitled - Brave", "New Tab - Brave", "about:blank - Brave"):
            self.assertTrue(restore.is_still_loading(title), title)
        for title in ("Trade - Brave", "alex@host:~", "", "Mines"):
            self.assertFalse(restore.is_still_loading(title), title)


class CommandMatch(unittest.TestCase):
    BRAVE = "/opt/brave-bin/brave --ozone-platform=wayland --restore-last-session"

    def test_the_same_command_line_is_an_exact_match(self):
        argv = ["/opt/brave-bin/brave", "--ozone-platform=wayland", "--restore-last-session"]
        self.assertEqual(restore.score_command_match(self.BRAVE, argv), 2)

    def test_the_restore_flag_is_ignored(self):
        """A browser launched by something else lacks the plugin's flag."""
        argv = ["/opt/brave-bin/brave", "--ozone-platform=wayland"]
        self.assertEqual(restore.score_command_match(self.BRAVE, argv), 2)

    def test_another_profile_is_another_command(self):
        argv = ["/opt/brave-bin/brave", "--ozone-platform=wayland", "--user-data-dir=/x"]
        self.assertEqual(restore.score_command_match(self.BRAVE, argv), 1)

    def test_a_desktop_entry_command_matches_the_program(self):
        self.assertEqual(restore.score_command_match("gnome-mines", ["/usr/bin/gnome-mines"]), 1)

    def test_another_program_does_not_match(self):
        self.assertEqual(restore.score_command_match(self.BRAVE, ["/opt/zen/zen"]), 0)

    def test_nothing_known_scores_nothing(self):
        self.assertEqual(restore.score_command_match(self.BRAVE, []), 0)
        self.assertEqual(restore.score_command_match("", ["x"]), 0)


class PairAcrossProcesses(unittest.TestCase):
    """Two Brave processes on different profiles share a class. The reported
    bug: a window of the everyday browser, seen before its page had loaded,
    was paired with an automation browser's about:blank entry and sent to
    that entry's workspace."""

    DEFAULT = "/opt/brave-bin/brave --ozone-platform=wayland --restore-last-session"
    JOBBOT = (
        "/opt/brave-bin/brave --ozone-platform=wayland --user-data-dir=/home/alex/p --restore-last-session"
    )

    def setUp(self):
        bybit = "▲ 78000.5 | Trade BTCUSDT | Bybit Perpetual Contracts - Brave"
        self.bybit = dict(saved_window("brave-browser", ws=1, cmd=self.DEFAULT), title=bybit)
        self.blank = dict(saved_window("brave-browser", ws=3, cmd=self.JOBBOT), title="about:blank - Brave")

    def pair(self, title, ws, argv):
        client = {"class": "brave-browser", "workspace": {"id": ws}, "pid": 7, "title": title, "at": [0, 0]}
        with mock.patch.object(proc, "read_cmdline", return_value=argv):
            return restore.match_saved_entry([self.bybit, self.blank], client)

    def test_a_loading_window_is_not_given_to_the_other_process(self):
        self.assertIs(self.pair("Untitled - Brave", 3, shlex.split(self.DEFAULT)), self.bybit)

    def test_the_other_process_gets_its_own_entry(self):
        self.assertIs(self.pair("about:blank - Brave", 1, shlex.split(self.JOBBOT)), self.blank)

    def test_a_flattened_command_line_is_read_back(self):
        """Chromium reports its argv as one string."""
        with pretend_runnable():
            self.assertIs(self.pair("Untitled - Brave", 3, [self.DEFAULT]), self.bybit)


class PlaceWindow(unittest.TestCase):
    def emit(self, win, address="0xaa", origins=None, workspace_on="eDP-1", floating=False):
        """`workspace_on` is the monitor the saved workspace lives on now."""
        live = [{"id": win["workspace"]["id"], "monitor": workspace_on}]
        with (
            mock.patch.object(hypr, "dispatch") as dispatched,
            mock.patch.object(hypr, "query", return_value=live),
        ):
            restore.place_window(win, address, origins or {}, floating=floating)
        return [c.args[0] for c in dispatched.call_args_list]

    def test_a_tiled_window_that_floats_now_is_tiled_again(self):
        emitted = self.emit(saved_window("steam"), floating=True)
        self.assertEqual(len(emitted), 2)
        self.assertIn("window.float({ action = 'off'", emitted[1])

    def test_floating_window_regains_position_and_size(self):
        emitted = self.emit(saved_window("x", floating=True, at=(10, 20), size=(300, 400)))
        joined = " ".join(emitted)
        self.assertIn("window.float", joined)
        self.assertIn("x = 10, y = 20", joined)
        self.assertIn("x = 300, y = 400", joined)

    def test_resize_comes_before_move(self):
        """Resizing re-centres a floating window, so a move before it is lost."""
        emitted = self.emit(saved_window("x", floating=True, at=(10, 20), size=(300, 400)))
        resize = next(i for i, e in enumerate(emitted) if "window.resize" in e)
        move = next(i for i, e in enumerate(emitted) if "window.move" in e and "x = 10" in e)
        self.assertLess(resize, move)

    def test_position_is_rebased_onto_the_monitor_its_workspace_is_on(self):
        """Saved 330px into DP-9; the workspace sits on eDP-1 for now, and
        carries the offset along when it is moved to DP-9 later."""
        emitted = self.emit(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9"),
            origins={"DP-9": (1920, 0), 1: (1920, 0), "eDP-1": (0, 0), 0: (0, 0)},
            workspace_on="eDP-1",
        )
        self.assertTrue(any("x = 330, y = 485" in e for e in emitted), emitted)

    def test_position_follows_the_workspace_once_it_is_on_its_monitor(self):
        """After the monitor pass a pinned window is placed again; the
        workspace is on DP-9 by then even if the window still reports eDP-1."""
        emitted = self.emit(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9"),
            origins={"DP-9": (1920, 0), 1: (1920, 0), "eDP-1": (0, 0), 0: (0, 0)},
            workspace_on="DP-9",
        )
        self.assertTrue(any("x = 2250, y = 485" in e for e in emitted), emitted)

    def test_pinned_window_is_repinned(self):
        self.assertTrue(any("window.pin" in e for e in self.emit(saved_window("x", pinned=True))))

    def test_pinned_window_is_placed_by_monitor_not_by_workspace(self):
        """No workspace move carries a pinned window: it belongs to a monitor,
        and the monitor pass that follows would leave it behind."""
        emitted = self.emit(
            saved_window("x", ws=17, floating=True, pinned=True, at=(2250, 485), monitor_name="DP-9"),
            origins={"DP-9": (1920, 0), "eDP-1": (0, 0)},
        )
        self.assertIn("hl.dsp.window.move({ monitor = 'DP-9', window = 'address:0xaa' })", emitted)
        self.assertFalse(any("workspace = '17'" in e for e in emitted), emitted)

    def test_pinned_window_keeps_its_offset_into_that_monitor(self):
        emitted = self.emit(
            saved_window("x", floating=True, pinned=True, at=(2250, 485), monitor_name="DP-9"),
            origins={"DP-9": (1920, 0), "eDP-1": (0, 0)},
        )
        self.assertTrue(any("x = 2250, y = 485" in e for e in emitted), emitted)

    def test_pinned_window_whose_monitor_is_gone_lands_on_the_one_that_is_left(self):
        """Undocked since the snapshot: the offset is kept, but measured into
        the monitor its workspace is on, so it comes back on screen."""
        win = dict(
            saved_window("x", floating=True, pinned=True, at=(2250, 485), monitor_name="unplugged"),
            monitor_at=[1920, 0],
        )
        emitted = self.emit(win, origins={"eDP-1": (0, 0)}, workspace_on="eDP-1")
        self.assertFalse(any("monitor =" in e for e in emitted), emitted)
        self.assertTrue(any("x = 330, y = 485" in e for e in emitted), emitted)

    def test_fullscreen_bit_selects_fullscreen_mode(self):
        emitted = " ".join(self.emit(saved_window("x", fullscreen=2)))
        self.assertIn("mode = 'fullscreen'", emitted)

    def test_maximized_state_is_not_reported_as_fullscreen(self):
        emitted = " ".join(self.emit(saved_window("x", fullscreen=1)))
        self.assertIn("mode = 'maximized'", emitted)

    def test_tiled_plain_window_only_moves(self):
        emitted = self.emit(saved_window("x"))
        self.assertEqual(len(emitted), 1)
        self.assertIn("window.move", emitted[0])


class OutOfPlace(unittest.TestCase):
    ORIGINS = {"eDP-1": (0, 0), 0: (0, 0), "DP-9": (1920, 0), 1: (1920, 0)}

    def test_same_offset_on_another_monitor_is_not_geometry_drift(self):
        saved = saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9")
        landed = {"workspace": {"id": 2}, "floating": True, "at": [330, 485], "monitor": 0}
        self.assertFalse(restore.is_out_of_place(saved, landed, self.ORIGINS))

    def test_a_window_saved_tiled_that_floats_now_is_out_of_place(self):
        landed = {"workspace": {"id": 2}, "floating": True, "at": [0, 0], "monitor": 0}
        self.assertTrue(restore.is_out_of_place(saved_window("steam"), landed, self.ORIGINS))

    def test_a_different_offset_is(self):
        saved = saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9")
        landed = {"workspace": {"id": 2}, "floating": True, "at": [2250, 485], "monitor": 0}
        self.assertTrue(restore.is_out_of_place(saved, landed, self.ORIGINS))

    def test_saved_corner_wins_over_where_that_monitor_sits_now(self):
        """The offset is what restore replays, so a monitor that has been
        moved since the snapshot must not shift every window on it."""
        win = dict(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9"), monitor_at=[1920, 0]
        )
        self.assertEqual(restore.get_saved_offset(win, {"DP-9": (3840, 0)}), (330, 485))


class WorkspaceMonitorPlacement(unittest.TestCase):
    """Workspaces bind to no monitor here, so a restored workspace is moved
    onto the monitor it was saved on. One move per workspace."""

    TWO = [{"id": 0, "name": "eDP-1"}, {"id": 1, "name": "DP-9"}]

    def emit(self, windows, monitors, live_ws):
        view = {f"0x{i}": {"class": "x", "workspace": {"id": ws}} for i, ws in enumerate(live_ws)}
        with (
            mock.patch.object(hypr, "query", return_value=monitors),
            mock.patch.object(hypr, "get_managed_clients", return_value=view),
            mock.patch.object(hypr, "dispatch") as dispatched,
        ):
            moved = restore.place_workspaces_on_monitors(windows)
        return moved, [c.args[0] for c in dispatched.call_args_list]

    def test_each_workspace_moves_once_to_its_saved_monitor(self):
        wins = [
            saved_window("x", ws=17, monitor_name="eDP-1"),
            saved_window("x", ws=19, monitor_name="DP-9"),
            saved_window("x", ws=17, monitor_name="eDP-1"),
        ]
        moved, emitted = self.emit(wins, self.TWO, [17, 19])
        self.assertEqual(moved, 2)
        self.assertIn("hl.dsp.workspace.move({ workspace = '17', monitor = 'eDP-1' })", emitted)
        self.assertIn("hl.dsp.workspace.move({ workspace = '19', monitor = 'DP-9' })", emitted)

    def test_single_monitor_is_a_no_op(self):
        wins = [saved_window("x", ws=17, monitor_name="eDP-1")]
        self.assertEqual(self.emit(wins, [{"id": 0, "name": "eDP-1"}], [17]), (0, []))

    def test_window_with_no_saved_monitor_is_skipped(self):
        self.assertEqual(self.emit([saved_window("x", ws=17)], self.TWO, [17]), (0, []))

    def test_workspace_with_no_live_window_is_skipped(self):
        self.assertEqual(self.emit([saved_window("x", ws=17, monitor_name="eDP-1")], self.TWO, []), (0, []))

    def test_special_workspace_is_skipped(self):
        wins = [saved_window("x", ws=-99, monitor_name="eDP-1")]
        self.assertEqual(self.emit(wins, self.TWO, [-99]), (0, []))


class MonitorPlacementInRestore(RestoreHarness):
    """The pass is wired into restore after everything has been placed: a
    workspace with no window yet on it would move back on its own."""

    TWO = [{"id": 0, "name": "eDP-1"}, {"id": 1, "name": "DP-9"}]

    def test_workspaces_are_moved_onto_their_monitors_last(self):
        self.write_session([saved_window("code", ws=17, monitor_name="eDP-1")])
        with mock.patch.object(hypr, "query", return_value=self.TWO):
            emitted = self.run_restore(
                [{}, {"0xa": {"class": "code", "workspace": {"id": 17}}}], sweep_timeout=5
            )
        self.assertEqual(emitted[-1], "hl.dsp.workspace.move({ workspace = '17', monitor = 'eDP-1' })")


class BuildGroups(unittest.TestCase):
    """group:add puts an existing window into an existing group outright, so
    the groups are assembled at the end from whatever the sweep placed. No
    focus, no direction, and no lock holding a half-built group shut."""

    def emit(self, placed):
        with (
            mock.patch.object(hypr, "dispatch") as dispatched,
            mock.patch.object(hypr, "eval_lua") as evaluated,
        ):
            built = restore.build_groups(placed)
        return (
            built,
            [c.args[0] for c in dispatched.call_args_list],
            [c.args[0] for c in evaluated.call_args_list],
        )

    @staticmethod
    def member(group, ws=2):
        return dict(saved_window("x", ws=ws), group=group)

    def test_the_first_member_anchors_and_the_rest_are_added_to_it(self):
        built, dispatched, evaluated = self.emit(
            [(self.member(0), "0xa"), (self.member(0), "0xb"), (self.member(0), "0xc")]
        )
        self.assertEqual(built, 1)
        self.assertEqual(dispatched, ["hl.dsp.group.toggle({ window = 'address:0xa' })"])
        self.assertEqual(
            evaluated,
            [
                "hl.get_window('address:0xa').group:add(hl.get_window('address:0xb'))",
                "hl.get_window('address:0xa').group:add(hl.get_window('address:0xc'))",
            ],
        )

    def test_a_group_down_to_one_window_is_not_built(self):
        """Nothing to tab it with. A group of one is also what used to be
        left locked, because the unlock pass walked straight past it."""
        self.assertEqual(self.emit([(self.member(0), "0xa")]), (0, [], []))

    def test_windows_that_were_in_no_group_are_ignored(self):
        self.assertEqual(self.emit([(saved_window("x"), "0xa"), (saved_window("y"), "0xb")]), (0, [], []))

    def test_each_saved_group_is_built(self):
        built, dispatched, evaluated = self.emit(
            [
                (self.member(0), "0xa"),
                (self.member(0), "0xb"),
                (self.member(1), "0xc"),
                (self.member(1), "0xd"),
            ]
        )
        self.assertEqual((built, len(dispatched), len(evaluated)), (2, 2, 2))

    def test_members_are_tabbed_in_saved_left_to_right_order(self):
        """Tabs follow where the windows sat, not the order they turned up."""
        right = dict(saved_window("x", at=(900, 0)), group=0)
        left = dict(saved_window("y", at=(0, 0)), group=0)
        _, dispatched, evaluated = self.emit([(right, "0xr"), (left, "0xl")])
        self.assertEqual(dispatched, ["hl.dsp.group.toggle({ window = 'address:0xl' })"])
        self.assertEqual(evaluated, ["hl.get_window('address:0xl').group:add(hl.get_window('address:0xr'))"])

    def test_no_window_is_focused_and_no_group_is_locked(self):
        """Both were mechanisms of the old build, and both caused bugs: a
        lock left behind refused every window for the rest of the session."""
        _, dispatched, evaluated = self.emit([(self.member(0), "0xa"), (self.member(0), "0xb")])
        self.assertFalse(any("lock" in lua or "focus" in lua for lua in dispatched + evaluated))


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


class ReportedBug(RestoreHarness):
    """Two Brave windows, one in each of two groups. Brave reopens them from
    its own session, so they arrive loose and in no particular order, which
    is no different from any other window now that groups are built from
    what the sweep placed rather than from what was launched into them."""

    def test_both_browser_windows_end_up_in_their_group(self):
        self.write_session(
            [
                dict(saved_window("code", ws=2), group=0),
                dict(saved_window("brave-browser", ws=2), group=0),
                dict(saved_window("foot", ws=5), group=1),
                dict(saved_window("brave-browser", ws=5, spawn=False), group=1),
            ]
        )
        settled = {
            "0xc": live_window("code", ws=2),
            "0xb1": live_window("brave-browser", ws=2),
            "0xf": live_window("foot", ws=5),
            "0xb2": live_window("brave-browser", ws=5),
        }
        emitted = self.run_restore([{}, settled], sweep_timeout=5)
        adds = [e for e in emitted if "group:add" in e]
        self.assertEqual(len(adds), 2)
        self.assertTrue(any("0xb1" in e for e in adds))
        self.assertTrue(any("0xb2" in e for e in adds))

    def test_a_member_that_never_arrives_does_not_stop_the_group(self):
        """Workspace 19 on the laptop: kdenlive never turned up, and what was
        left was locked and unjoinable. Now the rest still tab together."""
        self.write_session(
            [
                dict(saved_window("org.gnome.Mines", ws=19), group=1),
                dict(saved_window("chromium-browser", ws=19), group=1),
                dict(saved_window("org.kde.kdenlive", ws=19), group=1),
            ]
        )
        settled = {
            "0xm": live_window("org.gnome.Mines", ws=19),
            "0xc": live_window("chromium-browser", ws=19),
        }
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            emitted = self.run_restore([{}, settled], sweep_timeout=5)
        self.assertEqual(len([e for e in emitted if "group:add" in e]), 1)
        self.assertIn("no window turned up for org.kde.kdenlive", err.getvalue())
        self.assertFalse(any("lock" in e for e in emitted))
