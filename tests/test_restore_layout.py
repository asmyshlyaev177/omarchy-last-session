import io
import sys
import unittest
from unittest import mock

from omarchy_last_session import hypr
from omarchy_last_session.restore import layout
from tests.helpers import RestoreHarness, live_window, saved_window


class WorkspaceMonitorPlacement(unittest.TestCase):
    """Workspaces bind to no monitor here, so a restored workspace is moved
    onto the monitor it was saved on. One move per workspace."""

    TWO = [{"id": 0, "name": "eDP-1"}, {"id": 1, "name": "DP-9"}]

    def emit(self, windows, monitors, live_ws):
        """live_ws: the workspace of each live window, a number or a workspace dict."""
        view = {
            f"0x{i}": {"class": "x", "workspace": ws if isinstance(ws, dict) else {"id": ws, "name": str(ws)}}
            for i, ws in enumerate(live_ws)
        }
        with (
            mock.patch.object(hypr, "query", return_value=monitors),
            mock.patch.object(hypr, "get_managed_clients", return_value=view),
            mock.patch.object(hypr, "dispatch") as dispatched,
        ):
            moved = layout.place_workspaces_on_monitors(windows)
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
        special = {"id": -98, "name": "special:scratchpad"}
        wins = [dict(saved_window("x", monitor_name="eDP-1"), workspace=special)]
        self.assertEqual(self.emit(wins, self.TWO, [special]), (0, []))

    def test_a_named_workspace_moves_by_its_name_whatever_number_it_came_back_with(self):
        wins = [dict(saved_window("x", monitor_name="DP-9"), workspace={"id": -1337, "name": "Home"})]
        moved, emitted = self.emit(wins, self.TWO, [{"id": -1338, "name": "Home"}])
        self.assertEqual(
            (moved, emitted), (1, ["hl.dsp.workspace.move({ workspace = 'name:Home', monitor = 'DP-9' })"])
        )


class ShownWorkspaces(unittest.TestCase):
    """A workspace moved onto a monitor is not the one it shows, and the monitor it
    left shows a new empty workspace. Every login gave eDP-1 workspace 1 and DP-9
    workspace 2; saved the other way round, the laptop screen came back empty."""

    def emit(self, windows, monitors):
        with (
            mock.patch.object(hypr, "query", return_value=monitors),
            mock.patch.object(hypr, "dispatch") as dispatched,
        ):
            switched = layout.show_saved_workspaces(windows)
        return switched, [c.args[0] for c in dispatched.call_args_list]

    @staticmethod
    def monitor(name, shows, focused=False):
        number = int(shows) if shows.isdigit() else -1
        return {"name": name, "activeWorkspace": {"id": number, "name": shows}, "focused": focused}

    @staticmethod
    def shown(ws, monitor_name):
        return dict(saved_window("x", ws=ws, monitor_name=monitor_name), workspace_shown=True)

    def test_a_monitor_left_on_an_empty_workspace_is_switched_to_the_one_it_showed(self):
        wins = [self.shown(1, "DP-9"), self.shown(2, "eDP-1")]
        monitors = [self.monitor("eDP-1", "4", focused=True), self.monitor("DP-9", "1")]
        self.assertEqual(self.emit(wins, monitors), (1, ["hl.dsp.focus({ workspace = '2' })"]))

    def test_a_workspace_that_was_behind_the_shown_one_stays_behind(self):
        behind = dict(saved_window("x", ws=4, monitor_name="DP-9"), workspace_shown=False)
        monitors = [self.monitor("DP-9", "2", focused=True)]
        self.assertEqual(self.emit([behind, self.shown(2, "DP-9")], monitors), (0, []))

    def test_the_focused_monitor_is_switched_last_so_the_focus_stays_on_it(self):
        wins = [self.shown(2, "eDP-1"), self.shown(1, "DP-9")]
        monitors = [self.monitor("eDP-1", "5", focused=True), self.monitor("DP-9", "6")]
        self.assertEqual(
            self.emit(wins, monitors),
            (2, ["hl.dsp.focus({ workspace = '1' })", "hl.dsp.focus({ workspace = '2' })"]),
        )

    def test_the_focus_goes_back_to_a_focused_monitor_that_needed_no_switch(self):
        wins = [self.shown(2, "eDP-1"), self.shown(1, "DP-9")]
        monitors = [self.monitor("eDP-1", "2", focused=True), self.monitor("DP-9", "6")]
        self.assertEqual(
            self.emit(wins, monitors),
            (1, ["hl.dsp.focus({ workspace = '1' })", "hl.dsp.focus({ monitor = 'eDP-1' })"]),
        )

    def test_a_named_workspace_is_shown_by_its_name(self):
        home = dict(self.shown(0, "DP-9"), workspace={"id": -1337, "name": "Home"})
        monitors = [self.monitor("DP-9", "5", focused=True)]
        self.assertEqual(self.emit([home], monitors), (1, ["hl.dsp.focus({ workspace = 'name:Home' })"]))

    def test_a_monitor_that_is_gone_is_skipped(self):
        monitors = [self.monitor("eDP-1", "1", focused=True)]
        self.assertEqual(self.emit([self.shown(2, "DP-9")], monitors), (0, []))

    def test_a_snapshot_saved_before_shown_workspaces_were_recorded_changes_nothing(self):
        monitors = [self.monitor("eDP-1", "1", focused=True)]
        self.assertEqual(self.emit([saved_window("x", ws=2, monitor_name="eDP-1")], monitors), (0, []))


class WorkspaceNaming(unittest.TestCase):
    """A renamed numbered workspace is restored by its number, which Hyprland
    recreates unnamed, so its saved name is put back afterwards."""

    def emit(self, windows, live):
        with (
            mock.patch.object(hypr, "query", return_value=live),
            mock.patch.object(hypr, "dispatch") as dispatched,
        ):
            renamed = layout.name_workspaces(windows)
        return renamed, [c.args[0] for c in dispatched.call_args_list]

    @staticmethod
    def named(ws, name):
        win = saved_window("x", ws=ws)
        win["workspace"]["name"] = name
        return win

    def test_each_renamed_workspace_gets_its_name_back_once(self):
        wins = [self.named(1, "Home"), self.named(2, "it's"), self.named(1, "Home")]
        renamed, emitted = self.emit(wins, [{"id": 1, "name": "1"}, {"id": 2, "name": "2"}])
        self.assertEqual(renamed, 2)
        self.assertEqual(
            emitted,
            [
                "hl.dsp.workspace.rename({ workspace = '1', name = 'Home' })",
                "hl.dsp.workspace.rename({ workspace = '2', name = 'it\\'s' })",
            ],
        )

    def test_workspace_named_after_its_number_is_left_alone(self):
        self.assertEqual(self.emit([saved_window("x", ws=3)], [{"id": 3, "name": "3"}]), (0, []))

    def test_workspace_already_carrying_its_name_is_left_alone(self):
        self.assertEqual(self.emit([self.named(3, "web")], [{"id": 3, "name": "web"}]), (0, []))

    def test_workspace_carrying_another_name_is_renamed_to_the_saved_one(self):
        self.assertEqual(
            self.emit([self.named(1, "Home1")], [{"id": 1, "name": "Home"}]),
            (1, ["hl.dsp.workspace.rename({ workspace = '1', name = 'Home1' })"]),
        )

    def test_workspace_that_never_came_back_is_skipped(self):
        self.assertEqual(self.emit([self.named(3, "web")], [{"id": 1, "name": "1"}]), (0, []))

    def test_named_and_special_workspaces_are_skipped(self):
        wins = [self.named(-1337, "scratch"), self.named(-98, "special:magic")]
        live = [{"id": -1337, "name": "scratch"}, {"id": -98, "name": "special:magic"}]
        self.assertEqual(self.emit(wins, live), (0, []))


class WorkspaceNamingInRestore(RestoreHarness):
    def test_a_renamed_workspace_is_launched_by_number_and_renamed(self):
        win = saved_window("code", ws=1)
        win["workspace"]["name"] = "Home"
        self.write_session([win])
        with mock.patch.object(hypr, "query", return_value=[{"id": 1, "name": "1"}]):
            emitted = self.run_restore(
                [{}, {"0xa": {"class": "code", "workspace": {"id": 1}}}], sweep_timeout=5
            )
        self.assertIn("workspace = '1 silent'", emitted[0])
        self.assertNotIn("name:", emitted[0])
        self.assertEqual(emitted[-1], "hl.dsp.workspace.rename({ workspace = '1', name = 'Home' })")


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

    def emit(self, placed, refused=()):
        """How many groups were built, and every line of Lua sent, in order. An
        eval in `refused` fails, as Hyprland refuses a window it cannot group."""
        sent = []

        def evaluate(lua):
            sent.append(lua)
            return lua not in refused

        with (
            mock.patch.object(hypr, "dispatch", side_effect=sent.append),
            mock.patch.object(hypr, "eval_lua", side_effect=evaluate),
        ):
            built = layout.build_groups(placed)
        return built, sent

    @staticmethod
    def member(group, ws=2, **saved):
        """A group member as saved. Without group_position it is one saved before 2026-10-17."""
        return dict(saved_window("x", ws=ws), group=group, **saved)

    def tabs(self, count, shown=None, **saved):
        """A saved group of `count` tabs at addresses 0x0, 0x1, ..., in their saved order."""
        return [
            (self.member(0, group_position=i, shown_in_group=i == shown, **saved), f"0x{i}")
            for i in range(count)
        ]

    @staticmethod
    def toggle(address):
        return f"hl.dsp.group.toggle({{ window = 'address:{address}' }})"

    @staticmethod
    def add(anchor, address):
        return (
            f"local group = hl.get_window('address:{anchor}').group"
            f" group:add(hl.get_window('address:{address}'), group.size + 1)"
        )

    @staticmethod
    def show(index, anchor):
        return f"hl.dsp.group.active({{ index = {index}, window = 'address:{anchor}' }})"

    @staticmethod
    def fullscreen(mode, address):
        return (
            f"hl.dsp.window.fullscreen({{ mode = '{mode}', action = 'set', window = 'address:{address}' }})"
        )

    def test_the_first_member_anchors_and_the_rest_join_it_at_the_end(self):
        """At the end, not after the current tab: a window that takes focus
        becomes the current tab while the group is being built."""
        built, sent = self.emit([(self.member(0), "0xa"), (self.member(0), "0xb"), (self.member(0), "0xc")])
        self.assertEqual(built, 1)
        self.assertEqual(sent, [self.toggle("0xa"), self.add("0xa", "0xb"), self.add("0xa", "0xc")])

    def test_a_group_down_to_one_window_is_not_built(self):
        """Nothing to tab it with. A group of one is also what used to be
        left locked, because the unlock pass walked straight past it."""
        self.assertEqual(self.emit([(self.member(0), "0xa")]), (0, []))

    def test_windows_that_were_in_no_group_are_ignored(self):
        self.assertEqual(self.emit([(saved_window("x"), "0xa"), (saved_window("y"), "0xb")]), (0, []))

    def test_each_saved_group_is_built(self):
        built, sent = self.emit(
            [
                (self.member(0), "0xa"),
                (self.member(0), "0xb"),
                (self.member(1), "0xc"),
                (self.member(1), "0xd"),
            ]
        )
        self.assertEqual(built, 2)
        self.assertEqual(
            sent, [self.toggle("0xa"), self.add("0xa", "0xb"), self.toggle("0xc"), self.add("0xc", "0xd")]
        )

    def test_members_are_tabbed_in_their_saved_order(self):
        """Not in the order they turned up in, and not by where they sat."""
        last, first, middle = (
            self.member(0, group_position=position, at=[x, 0]) for position, x in ((2, 400), (0, 900), (1, 0))
        )
        _, sent = self.emit([(last, "0xc"), (first, "0xa"), (middle, "0xb")])
        self.assertEqual(sent, [self.toggle("0xa"), self.add("0xa", "0xb"), self.add("0xa", "0xc")])

    def test_a_group_saved_without_its_tab_order_keeps_the_order_the_sweep_paired_it_in(self):
        """Snapshots written before 2026-10-17 have no group_position. Paired first,
        0xb is neither the first address nor the leftmost window."""
        _, sent = self.emit([(self.member(0, at=[900, 0]), "0xb"), (self.member(0, at=[0, 0]), "0xa")])
        self.assertEqual(sent, [self.toggle("0xb"), self.add("0xb", "0xa")])

    def test_the_tab_the_group_showed_is_shown_again(self):
        """Each window that joins becomes the shown tab, so the last would be."""
        for shown in range(3):
            with self.subTest(shown=shown):
                _, sent = self.emit(self.tabs(3, shown=shown))
                self.assertEqual(sent[-1], self.show(shown + 1, "0x0"))

    def test_a_shown_tab_that_was_maximized_or_fullscreen_is_so_again(self):
        """Joining a group clears a window's fullscreen, which the sweep had put back."""
        for value, mode in ((1, "maximized"), (2, "fullscreen")):
            with self.subTest(mode=mode):
                tabs = self.tabs(2, shown=1)
                tabs[1][0]["fullscreen"] = value
                _, sent = self.emit(tabs)
                self.assertEqual(sent[-2:], [self.show(2, "0x0"), self.fullscreen(mode, "0x1")])

    def test_a_group_whose_shown_tab_did_not_come_back_is_left_showing_its_last(self):
        """No tab to show again, and no fullscreen to put back."""
        tabs = self.tabs(3, shown=1)
        del tabs[1]
        _, sent = self.emit(tabs)
        self.assertEqual(sent, [self.toggle("0x0"), self.add("0x0", "0x2")])

    def test_a_tab_hyprland_refuses_does_not_move_the_shown_one(self):
        """The tabs after a refused window move up one, so the last of three is the second."""
        _, sent = self.emit(self.tabs(3, shown=2), refused={self.add("0x0", "0x1")})
        self.assertEqual(sent[-1], self.show(2, "0x0"))

    def test_a_shown_tab_left_on_its_own_still_gets_its_fullscreen_back(self):
        """Every other tab was refused, so there is no tab to switch to, but making
        the group cleared its fullscreen all the same."""
        tabs = self.tabs(2, shown=0)
        tabs[0][0]["fullscreen"] = 1
        _, sent = self.emit(tabs, refused={self.add("0x0", "0x1")})
        self.assertEqual(
            sent, [self.toggle("0x0"), self.add("0x0", "0x1"), self.fullscreen("maximized", "0x0")]
        )

    def test_no_window_is_focused_and_no_group_is_locked(self):
        """Both were mechanisms of the old build, and both caused bugs: a
        lock left behind refused every window for the rest of the session."""
        tabs = self.tabs(2, shown=0)
        tabs[0][0]["fullscreen"] = 1
        _, sent = self.emit(tabs)
        self.assertFalse(any("lock" in lua or "focus" in lua for lua in sent))


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


class HeldWorkspaces(unittest.TestCase):
    """A workspace exists only once a window lands on it, and apps come up in
    no particular order. A setup that closes gaps in the numbering moved
    workspace 3 into 2 while 2's browser windows were still loading, and every
    window and name after that went to the wrong workspace (2026-09-25)."""

    # A login with three monitors: Hyprland has given each a workspace, and those are all there is.
    LOGIN = [{"id": 1, "name": "1"}, {"id": 2, "name": "2"}, {"id": 3, "name": "3"}]

    def sent_by(self, action):
        with (
            mock.patch.object(hypr, "query", return_value=self.LOGIN),
            mock.patch.object(hypr, "eval_lua") as eval_lua,
        ):
            action()
        return [c.args[0] for c in eval_lua.call_args_list]

    def test_workspaces_yet_to_exist_are_held_numbered_first_in_one_call(self):
        windows = [
            saved_window("a", ws=10, monitor_name="DP-9"),
            dict(saved_window("b", monitor_name="HDMI-A-1"), workspace={"id": -1337, "name": "Home"}),
            saved_window("c", ws=4, monitor_name="DP-9"),
            saved_window("d", ws=10, monitor_name="DP-9"),
        ]
        self.assertEqual(
            self.sent_by(lambda: layout.hold_workspaces(windows)),
            [
                f"{layout.HOLDS} = {{"
                " hl.workspace_rule({ workspace = '4', persistent = true }),"
                " hl.workspace_rule({ workspace = '10', persistent = true }),"
                " hl.workspace_rule({ workspace = 'name:Home', persistent = true }) }"
            ],
        )

    def test_a_workspace_that_already_exists_is_not_held(self):
        windows = [
            saved_window("a", ws=1, monitor_name="eDP-1"),
            saved_window("b", ws=2, monitor_name="HDMI-A-1"),
            dict(saved_window("c", monitor_name="HDMI-A-1"), workspace={"id": 3, "name": "Work"}),
            dict(saved_window("d"), workspace={"id": -98, "name": "special:scratchpad"}),
        ]
        self.assertEqual(self.sent_by(lambda: layout.hold_workspaces(windows)), [])

    def test_release_switches_off_every_rule_it_holds(self):
        (sent,) = self.sent_by(layout.release_workspaces)
        self.assertIn(f"ipairs({layout.HOLDS} or {{}})", sent)
        self.assertIn("rule:set_enabled(false)", sent)


class HeldWorkspacesInRestore(RestoreHarness):
    def hold_and_release_into_timeline(self):
        self.patch(layout, "hold_workspaces", mock.Mock(side_effect=lambda w: self.timeline.append("hold")))
        self.patch(
            layout, "release_workspaces", mock.Mock(side_effect=lambda: self.timeline.append("release"))
        )

    def test_workspaces_are_held_from_before_the_first_launch_until_after_the_last_move(self):
        self.hold_and_release_into_timeline()
        self.patch(hypr, "query", mock.Mock(return_value=[{"id": 1, "name": "1"}, {"id": 2, "name": "2"}]))
        self.write_session([saved_window("code", ws=2, monitor_name="DP-2")])
        emitted = self.run_restore([{}, {"0xa": live_window("code", ws=2)}])
        self.assertEqual(emitted[0], "hold")
        self.assertIn("exec_cmd", emitted[1])
        self.assertIn("workspace.move", emitted[-2])
        self.assertEqual(emitted[-1], "release")

    def test_a_monitor_is_switched_to_its_saved_workspace_after_the_moves(self):
        self.hold_and_release_into_timeline()
        monitors = [
            {"id": 0, "name": "DP-2", "activeWorkspace": {"id": 7, "name": "7"}, "focused": True},
            {"id": 1, "name": "eDP-1", "activeWorkspace": {"id": 1, "name": "1"}},
        ]
        self.patch(hypr, "query", mock.Mock(side_effect=lambda cmd: monitors if cmd == "monitors" else []))
        self.write_session([dict(saved_window("code", ws=2, monitor_name="DP-2"), workspace_shown=True)])
        emitted = self.run_restore([{}, {"0xa": live_window("code", ws=2)}])
        self.assertIn("workspace.move", emitted[-3])
        self.assertEqual(emitted[-2:], ["hl.dsp.focus({ workspace = '2' })", "release"])

    def test_workspaces_are_released_when_restore_fails_part_way(self):
        self.hold_and_release_into_timeline()
        self.write_session([saved_window("code", ws=2)])
        with mock.patch.object(layout, "build_groups", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_restore([{}])
        self.assertEqual(self.timeline[-2:], ["release", "toast down: Restoring last session…"])
