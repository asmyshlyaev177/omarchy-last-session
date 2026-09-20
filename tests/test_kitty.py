"""Kitty's tabs and splits: what the instance reports, and the session file
that replays it.

Every expected session file here was replayed into a real kitty 0.48 and the
instance it built compared against the one it came from, so the shapes below
are what kitty actually does, not what its documentation suggests.
"""

import io
import json
import subprocess
import sys
import unittest
from unittest import mock

from omarchy_last_session import kitty, proc


def pane(pane_id, cwd="/home/alex", cmdline=("/usr/bin/bash",), title="", named=False, active=False):
    """A pane as `kitten @ ls` reports it."""
    return {
        "id": pane_id,
        "cwd": cwd,
        "title": title,
        "title_overridden": named,
        "is_active": active,
        "foreground_processes": [{"pid": 900 + pane_id, "cwd": cwd, "cmdline": list(cmdline)}],
    }


def tab(panes, pairs=None, title="", named=False, layout="splits", active=False, layouts=("splits", "tall")):
    return {
        "title": title,
        "title_overridden": named,
        "layout": layout,
        "is_active": active,
        "enabled_layouts": list(layouts),
        "layout_state": {"pairs": pairs} if pairs else {},
        "windows": panes,
    }


def render(*os_windows):
    return kitty.render_session([{"tabs": list(tabs)} for tabs in os_windows]).splitlines()


class OnePane(unittest.TestCase):
    def test_a_tab_of_one_pane_is_one_launch(self):
        self.assertEqual(
            render([tab([pane(1, "/srv")], pairs={"one": 1})]),
            ["new_tab", "enabled_layouts splits,tall", "layout splits", "launch --var ols_pane=1 --cwd=/srv"],
        )

    def test_the_file_ends_with_a_newline(self):
        self.assertTrue(kitty.render_session([{"tabs": [tab([pane(1)])]}]).endswith("\n"))


class SplitTree(unittest.TestCase):
    """Kitty splits the focused pane, so a tree is rebuilt by focusing the pane
    each pair grew from and splitting it again. A pair is rebuilt before its
    children, so every pane exists by the time it is split."""

    # What a real kitty reported for four panes: one vsplit off the first, one
    # hsplit off each side. Both sides of the root are pairs, which a walk that
    # only ever splits the newest pane gets wrong.
    NESTED = {
        "one": {"horizontal": False, "one": 1, "two": 4},
        "two": {"horizontal": False, "one": 2, "two": 3},
    }

    def render_panes(self, pairs, count):
        panes = [pane(i, f"/pane{i}") for i in range(1, count + 1)]
        return render([tab(panes, pairs=pairs)])[3:]

    def test_a_nested_tree_is_rebuilt_pane_by_pane(self):
        self.assertEqual(
            self.render_panes(self.NESTED, 4),
            [
                "launch --var ols_pane=1 --cwd=/pane1",
                "focus_matching_window var:ols_pane=1",
                "launch --location=vsplit --var ols_pane=2 --cwd=/pane2",
                "focus_matching_window var:ols_pane=1",
                "launch --location=hsplit --var ols_pane=4 --cwd=/pane4",
                "focus_matching_window var:ols_pane=2",
                "launch --location=hsplit --var ols_pane=3 --cwd=/pane3",
            ],
        )

    def test_panes_side_by_side_are_split_vertically(self):
        """Kitty calls a pair horizontal when its panes sit side by side, and
        the split that puts one beside another is vsplit."""
        lines = self.render_panes({"horizontal": True, "one": 1, "two": 2}, 2)
        self.assertIn("--location=vsplit", lines[2])

    def test_panes_above_each_other_are_split_horizontally(self):
        lines = self.render_panes({"horizontal": False, "one": 1, "two": 2}, 2)
        self.assertIn("--location=hsplit", lines[2])

    def test_a_pair_without_a_side_is_side_by_side(self):
        lines = self.render_panes({"one": 1, "two": 2}, 2)
        self.assertIn("--location=vsplit", lines[2])

    def test_a_tab_that_was_never_split_has_one_pair_and_no_split(self):
        """Kitty reports a lone pane as a pair holding only its `one` side."""
        self.assertEqual(self.render_panes({"one": 1}, 1), ["launch --var ols_pane=1 --cwd=/pane1"])

    def test_a_layout_that_is_not_splits_just_lists_its_panes(self):
        """Tall, grid and stack arrange the panes themselves, so the order they
        were opened in is the whole of it."""
        panes = [pane(1, "/one"), pane(2, "/two")]
        lines = render([tab(panes, layout="tall")])
        self.assertEqual(
            lines[2:],
            ["layout tall", "launch --var ols_pane=1 --cwd=/one", "launch --var ols_pane=2 --cwd=/two"],
        )


class TabsAndWindows(unittest.TestCase):
    def test_every_tab_is_asked_for_including_the_first(self):
        lines = render([tab([pane(1)]), tab([pane(2)])])
        self.assertEqual([line for line in lines if line.startswith("new_tab")], ["new_tab", "new_tab"])

    def test_a_tab_the_user_named_keeps_its_name(self):
        lines = render([tab([pane(1)], title="build logs", named=True)])
        self.assertEqual(lines[0], "new_tab build logs")

    def test_a_title_the_shell_wrote_is_not_kept(self):
        """It says where the shell was, which the new one will say again."""
        self.assertEqual(render([tab([pane(1)], title="~/Projects")])[0], "new_tab")

    def test_a_second_os_window_is_asked_for(self):
        lines = render([tab([pane(1)])], [tab([pane(2)])])
        self.assertEqual(lines.count("new_os_window"), 1)
        self.assertLess(lines.index("new_tab"), lines.index("new_os_window"))

    def test_the_pane_and_tab_that_had_the_keyboard_come_back_focused(self):
        lines = render(
            [
                tab([pane(1), pane(2, active=True)], pairs={"one": 1, "two": 2}),
                tab([pane(3, active=True)], active=True),
            ]
        )
        self.assertIn("focus_matching_window var:ols_pane=2", lines)
        self.assertEqual(lines[-1], "focus_tab 1")

    def test_the_first_tab_needs_no_asking_for(self):
        lines = render([tab([pane(1, active=True)], active=True)])
        self.assertNotIn("focus_tab 0", lines)


class PaneContents(unittest.TestCase):
    def test_a_tui_is_started_again(self):
        line = render([tab([pane(1, "/repo", cmdline=("/usr/bin/nvim", "README.md"))])])[-1]
        self.assertEqual(line, "launch --var ols_pane=1 --cwd=/repo /usr/bin/nvim README.md")

    def test_a_shell_prompt_is_left_to_kitty(self):
        """Kitty opens the user's shell in the pane's directory by itself."""
        self.assertEqual(
            render([tab([pane(1, "/repo", cmdline=("/usr/bin/zsh",))])])[-1],
            "launch --var ols_pane=1 --cwd=/repo",
        )

    def test_a_per_run_temporary_file_is_not_replayed(self):
        line = render([tab([pane(1, cmdline=("yazi", "--cwd-file=/tmp/yazi-8831"))])])[-1]
        self.assertTrue(line.endswith("yazi"), line)

    def test_a_directory_with_a_space_is_quoted(self):
        line = render([tab([pane(1, "/home/alex/my notes")])])[-1]
        self.assertEqual(line, "launch --var ols_pane=1 '--cwd=/home/alex/my notes'")

    def test_a_pane_the_user_titled_keeps_its_title(self):
        line = render([tab([pane(1, title="server", named=True)])])[-1]
        self.assertIn("--title=server", line)


class UntrustedText(unittest.TestCase):
    """A session file is a list of directives, one per line, and a title is
    whatever ran in the pane printed. A title carrying a newline would be read
    as another directive."""

    def test_a_title_holding_a_newline_is_dropped(self):
        line = render([tab([pane(1, title="ok\nlaunch rm -rf /", named=True)])])[-1]
        self.assertNotIn("rm -rf", line)

    def test_a_tab_title_holding_a_newline_is_dropped(self):
        lines = render([tab([pane(1)], title="ok\nlaunch rm -rf /", named=True)])
        self.assertEqual(lines[0], "new_tab")

    def test_a_directory_holding_a_newline_is_dropped(self):
        line = render([tab([pane(1, "/tmp/ok\nlaunch rm -rf /")])])[-1]
        self.assertEqual(line, "launch --var ols_pane=1")

    def test_a_program_holding_a_newline_is_dropped(self):
        line = render([tab([pane(1, "/repo", cmdline=("/usr/bin/nvim", "a\nlaunch rm -rf /"))])])[-1]
        self.assertEqual(line, "launch --var ols_pane=1 --cwd=/repo")


class ListenAddress(unittest.TestCase):
    """Kitty exports its socket to the processes it starts and to nothing else,
    so the address is read from a pane's shell rather than guessed."""

    def read_address(self, environs):
        with (
            mock.patch.object(proc, "list_children", return_value=sorted(environs)),
            mock.patch.object(proc, "read_environ", side_effect=lambda pid: environs[pid]),
        ):
            return kitty.find_listen_address(7)

    def test_the_socket_is_read_from_a_pane(self):
        address = self.read_address({11: {}, 12: {"KITTY_LISTEN_ON": "unix:@mykitty-7"}})
        self.assertEqual(address, "unix:@mykitty-7")

    def test_a_kitty_without_remote_control_has_no_socket(self):
        self.assertIsNone(self.read_address({11: {"PATH": "/usr/bin"}}))

    def test_a_kitty_with_no_panes_left_has_no_socket(self):
        self.assertIsNone(self.read_address({}))


class ReadLayout(unittest.TestCase):
    def answer(self, returncode=0, stdout="[]", error=None):
        done = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with (
            mock.patch.object(subprocess, "run", side_effect=error, return_value=done),
            mock.patch.object(sys, "stderr", io.StringIO()),
        ):
            return kitty.read_layout("unix:@mykitty-7")

    def test_what_kitty_reports_is_decoded(self):
        self.assertEqual(self.answer(stdout=json.dumps([{"tabs": []}])), [{"tabs": []}])

    def test_a_refused_request_reads_as_nothing(self):
        """Remote control is off, so there is nothing to read and nothing to
        report: the fallback is the plain terminal relaunch."""
        self.assertIsNone(self.answer(returncode=1, stdout=""))

    def test_an_unreadable_answer_reads_as_nothing(self):
        self.assertIsNone(self.answer(stdout="not json"))

    def test_a_missing_kitten_reads_as_nothing(self):
        self.assertIsNone(self.answer(error=FileNotFoundError("kitten")))

    def test_a_kitty_that_does_not_answer_reads_as_nothing(self):
        self.assertIsNone(self.answer(error=subprocess.TimeoutExpired("kitten", 5)))


class SessionText(unittest.TestCase):
    def test_nothing_without_a_socket(self):
        with mock.patch.object(kitty, "find_listen_address", return_value=None):
            self.assertIsNone(kitty.build_session_text(7))

    def test_nothing_when_the_instance_says_nothing(self):
        with (
            mock.patch.object(kitty, "find_listen_address", return_value="unix:@k-7"),
            mock.patch.object(kitty, "read_layout", return_value=[]),
        ):
            self.assertIsNone(kitty.build_session_text(7))

    def test_the_layout_becomes_a_session_file(self):
        with (
            mock.patch.object(kitty, "find_listen_address", return_value="unix:@k-7"),
            mock.patch.object(kitty, "read_layout", return_value=[{"tabs": [tab([pane(1, "/srv")])]}]),
        ):
            self.assertIn("--cwd=/srv", kitty.build_session_text(7))
