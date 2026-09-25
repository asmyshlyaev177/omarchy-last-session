import itertools
import random
import shlex
import unittest
from unittest import mock

from omarchy_last_session import proc
from omarchy_last_session.restore import pairing
from tests.helpers import pretend_runnable, saved_window


def match_one(pending, client):
    """The entry the sweep gives a window that turned up on its own, or None."""
    pairs = pairing.pair_arrivals(pending, {"0xa": client})
    return pairs[0][0] if pairs else None


class PairAcrossAClassShift(unittest.TestCase):
    """Chromium reports chromium-browser when restore relaunches it from a
    command line rather than from its desktop entry. The window has to be
    recognised anyway, or it is never placed and never rejoins its group."""

    CHROMIUM = "/usr/lib/chromium/chromium --restore-last-session"

    def client(self, cls, ws=2):
        return {"class": cls, "workspace": {"id": ws}, "pid": 77, "at": [0, 0], "floating": False}

    def pair(self, saved, client, argv):
        with mock.patch.object(proc, "read_cmdline", return_value=argv):
            return match_one(saved, client)

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
        self.assertIs(match_one([bybit, kobeissi], landed), bybit)

    def test_each_window_takes_its_own_place(self):
        bybit, kobeissi = self.brave(1, self.BYBIT), self.brave(2, self.KOBEISSI)
        pending = [bybit, kobeissi]
        first = match_one(pending, self.client(self.KOBEISSI, ws=1))
        pending.remove(first)
        self.assertIs(first, kobeissi)
        self.assertIs(match_one(pending, self.client(self.BYBIT, ws=1)), bybit)

    def test_the_workspace_decides_when_there_are_no_titles(self):
        away, here = self.brave(9, ""), self.brave(2, "")
        self.assertIs(match_one([away, here], self.client("", ws=2)), here)

    def test_matching_titles_fall_back_to_the_workspace(self):
        away, here = self.brave(9, "fish"), self.brave(2, "fish")
        self.assertIs(match_one([away, here], self.client("fish", ws=2)), here)

    def test_a_title_the_window_no_longer_has_still_beats_a_stranger(self):
        """VS Code drops the project name when it reopens on its welcome tab."""
        study = dict(saved_window("code", ws=1), title="STUDY-PLAN.md - projects")
        other = dict(saved_window("code", ws=2), title="omarchy-last-session - Code")
        landed = self.client("STUDY-PLAN.md - projects (Workspace)", ws=2, cls="code")
        self.assertIs(match_one([study, other], landed), study)


class CommandMatch(unittest.TestCase):
    BRAVE = "/opt/brave-bin/brave --ozone-platform=wayland --restore-last-session"

    def test_the_same_command_line_is_an_exact_match(self):
        argv = ["/opt/brave-bin/brave", "--ozone-platform=wayland", "--restore-last-session"]
        self.assertEqual(pairing.score_command_match(self.BRAVE, argv), 2)

    def test_the_restore_flag_is_ignored(self):
        """A browser launched by something else lacks the plugin's flag."""
        argv = ["/opt/brave-bin/brave", "--ozone-platform=wayland"]
        self.assertEqual(pairing.score_command_match(self.BRAVE, argv), 2)

    def test_another_profile_is_another_command(self):
        argv = ["/opt/brave-bin/brave", "--ozone-platform=wayland", "--user-data-dir=/x"]
        self.assertEqual(pairing.score_command_match(self.BRAVE, argv), 1)

    def test_a_desktop_entry_command_matches_the_program(self):
        self.assertEqual(pairing.score_command_match("gnome-mines", ["/usr/bin/gnome-mines"]), 1)

    def test_another_program_does_not_match(self):
        self.assertEqual(pairing.score_command_match(self.BRAVE, ["/opt/zen/zen"]), 0)

    def test_nothing_known_scores_nothing(self):
        self.assertEqual(pairing.score_command_match(self.BRAVE, []), 0)
        self.assertEqual(pairing.score_command_match("", ["x"]), 0)


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
            return match_one([self.bybit, self.blank], client)

    def test_a_loading_window_is_not_given_to_the_other_process(self):
        self.assertIs(self.pair("Untitled - Brave", 3, shlex.split(self.DEFAULT)), self.bybit)

    def test_the_other_process_gets_its_own_entry(self):
        self.assertIs(self.pair("about:blank - Brave", 1, shlex.split(self.JOBBOT)), self.blank)

    def test_a_flattened_command_line_is_read_back(self):
        """Chromium reports its argv as one string."""
        with pretend_runnable():
            self.assertIs(self.pair("Untitled - Brave", 3, [self.DEFAULT]), self.bybit)


class PairingIsStable(unittest.TestCase):
    """Whatever the titles and workspaces, the windows paired in one pass leave
    no window and saved entry apart that each fit the other better than what
    they were given. Paired one at a time, the first window listed took the
    entry that a later one fitted exactly."""

    WORDS = ("bybit", "trade", "home", "x", "gmail", "inbox", "wikipedia", "rust", "1", "2")
    UNPAIRED = (-1,)  # below every score_fit

    def setUp(self):
        patcher = mock.patch.object(proc, "read_cmdline", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def title(self, rng):
        return " ".join(rng.sample(self.WORDS, rng.randint(0, 3))) + " - Brave"

    def test_no_window_and_entry_would_rather_have_each_other(self):
        rng = random.Random(20260925)
        for case in range(500):
            pending = [
                dict(saved_window("brave-browser", ws=rng.randint(1, 3)), title=self.title(rng))
                for _ in range(rng.randint(0, 5))
            ]
            arrivals = {
                f"0x{i}": {
                    "class": "brave-browser",
                    "workspace": {"id": rng.randint(1, 3)},
                    "title": self.title(rng),
                }
                for i in range(rng.randint(0, 5))
            }
            with self.subTest(case=case):
                self.assert_stable(pending, arrivals, pairing.pair_arrivals(pending, arrivals))

    def assert_stable(self, pending, arrivals, pairs):
        entry_of = {address: entry for entry, address in pairs}
        window_of = {id(entry): address for entry, address in pairs}
        self.assertEqual(len(pairs), min(len(pending), len(arrivals)))
        self.assertEqual((len(entry_of), len(window_of)), (len(pairs), len(pairs)))

        def fit(entry, address):
            return pairing.score_fit(entry, arrivals[address], [])

        for address, entry in itertools.product(arrivals, pending):
            mine = fit(entry_of[address], address) if address in entry_of else self.UNPAIRED
            theirs = fit(entry, window_of[id(entry)]) if id(entry) in window_of else self.UNPAIRED
            together = fit(entry, address)
            self.assertFalse(together > mine and together > theirs, (address, entry["title"], pairs))
