import io
import itertools
import re
import sys
from unittest import mock

from omarchy_last_session import config
from tests.helpers import RestoreHarness, saved_window


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

    def test_a_window_that_stays_blank_is_paired_all_the_same(self):
        self.write_session([dict(saved_window("brave-browser", ws=3), title="about:blank - Brave")])
        emitted = self.run_restore([{}, {"0xb": self.brave("about:blank - Brave")}], sweep_timeout=10)
        self.assertTrue(any("window.move" in e and "0xb" in e and "workspace = '3'" in e for e in emitted))

    def test_the_wait_ends_with_the_sweep(self):
        """A window still loading when time runs out is placed by what it has.
        The second entry gives it something to be told apart from, or it
        would not wait at all."""
        self.patch(config, "TITLE_SETTLE", 100)
        self.write_session(
            [
                dict(saved_window("brave-browser", ws=3), title="Trade - Brave"),
                dict(saved_window("brave-browser", ws=4, spawn=False), title="Home / X - Brave"),
            ]
        )
        emitted = self.run_restore([{}, {"0xb": self.brave("Untitled - Brave")}], sweep_timeout=5)
        self.assertTrue(any("window.move" in e and "0xb" in e for e in emitted))


def brave_window(title, ws):
    """A Brave window as the sweep sees it, titled the way Brave titles one."""
    return {
        "class": "brave-browser",
        "workspace": {"id": ws},
        "title": f"{title} - Brave",
        "at": [0, 0],
        "floating": False,
    }


def saved_brave(title, ws, spawn=False):
    return dict(saved_window("brave-browser", ws=ws, spawn=spawn), title=f"{title} - Brave")


def get_moves(emitted):
    """Address -> the workspace the sweep moved that window to."""
    found = (
        re.search(r"workspace = '([^']+)', follow = false, window = 'address:(\w+)'", e) for e in emitted
    )
    return {m[2]: m[1] for m in found if m}


class RivalBrowserWindows(RestoreHarness):
    """Brave reopens the windows of its own session all at once and in no set
    order, each titled with whatever its page shows so far. Paired one at a
    time in the order Hyprland listed them, and by letters rather than words, a
    window whose page had changed since the save took the entry of one still
    showing its saved title, and the two came back on each other's monitors:
    four boots in twelve, September 2026."""

    BYBIT = "▲ 86236.8 | Trade BTCUSDT | Bybit Perpetual Contracts"
    CHANGED = "Трудно быть богом (1 сезон) смотреть онлайн бесплатно"

    def test_a_window_showing_its_saved_title_keeps_its_entry(self):
        """The boot of 2026-09-23, in every order Hyprland could list the windows."""
        saved = [
            saved_brave(self.BYBIT, 1, spawn=True),
            saved_brave("JobBot Dashboard", 3),
            saved_brave("Home / X", 2),
        ]
        landed = [
            ("0xt", brave_window(self.CHANGED, 1)),
            ("0xb", brave_window("Bybit", 1)),
            ("0xj", brave_window("JobBot Dashboard", 1)),
        ]
        for order in itertools.permutations(landed):
            with self.subTest(order=[address for address, _ in order]):
                self.write_session(saved)
                emitted = self.run_restore([{}, dict(order)], sweep_timeout=5)
                self.assertEqual(get_moves(emitted), {"0xt": "2", "0xj": "3"})

    def test_a_window_showing_its_site_name_finds_its_page(self):
        """The boot of 2026-09-18: Bybit titles its page 'Bybit' until the prices load."""
        self.write_session(
            [
                saved_brave("▼ 81236.1 | Trade BTCUSDT | Bybit Perpetual Contracts", 1, spawn=True),
                saved_brave("Home / X", 2),
            ]
        )
        landed = {
            "0xo": brave_window("Brent oil - Price - Chart - Historical Data - News", 1),
            "0xb": brave_window("Bybit", 1),
        }
        self.assertEqual(get_moves(self.run_restore([{}, landed], sweep_timeout=5)), {"0xo": "2"})

    def test_windows_are_told_apart_by_the_titles_their_pages_settle_on(self):
        """Both first show only the site's name, which fits either entry."""
        rust, python = "Rust (programming language) - Wikipedia", "Python (programming language) - Wikipedia"
        self.write_session([saved_brave(rust, 1, spawn=True), saved_brave(python, 2)])
        loading = {"0xa": brave_window("Wikipedia", 1), "0xb": brave_window("Wikipedia", 1)}
        loaded = {"0xa": brave_window(python, 1), "0xb": brave_window(rust, 1)}
        self.assertEqual(get_moves(self.run_restore([{}, loading, loaded], sweep_timeout=8)), {"0xa": "2"})

    def test_a_window_that_turns_up_later_is_not_robbed_of_its_entry(self):
        """The first window's page changed since the save, so only elimination
        can place it, and that has to wait for its rival to turn up."""
        self.write_session([saved_brave(self.BYBIT, 1, spawn=True), saved_brave("Home / X", 2)])
        alone = {"0xt": brave_window(self.CHANGED, 1)}
        both = dict(alone, **{"0xb": brave_window("Bybit", 1)})
        emitted = self.run_restore([{}, alone, alone, both], sweep_timeout=10)
        self.assertEqual(get_moves(emitted), {"0xt": "2"})

    def test_a_window_that_never_turns_up_holds_the_rest_only_briefly(self):
        self.patch(config, "TITLE_SETTLE", 3)
        self.write_session(
            [
                saved_brave(self.BYBIT, 1, spawn=True),
                saved_brave("JobBot Dashboard", 3),
                saved_brave("Home / X", 2),
            ]
        )
        landed = {"0xj": brave_window("JobBot Dashboard", 1), "0xx": brave_window("Home / X", 1)}
        # Gone after the fourth pass: only a wait that ends TITLE_SETTLE after
        # they turned up, not at the end of the sweep, places them.
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            emitted = self.run_restore([{}] + [landed] * 4 + [{}], sweep_timeout=15)
        self.assertEqual(get_moves(emitted), {"0xj": "3", "0xx": "2"})
        self.assertIn(
            f"no window turned up for brave-browser '{self.BYBIT} - Brave' from workspace 1", err.getvalue()
        )

    def test_a_title_that_keeps_changing_is_paired_when_the_wait_runs_out(self):
        """Gmail swaps its title back and forth while a message is new."""
        self.patch(config, "TITLE_SETTLE", 3)
        self.write_session([saved_brave("Inbox - Gmail", 1, spawn=True), saved_brave("YouTube", 2)])
        inbox = {"0xg": brave_window("Inbox - Gmail", 1), "0xy": brave_window("YouTube", 1)}
        news = {"0xg": brave_window("New message from Ann - Gmail", 1), "0xy": brave_window("YouTube", 1)}
        emitted = self.run_restore([{}] + [inbox, news] * 3 + [{}], sweep_timeout=20)
        self.assertEqual(get_moves(emitted), {"0xy": "2"})

    def test_a_lone_window_is_paired_at_once_whatever_its_title(self):
        """With one entry to take there is nothing to tell apart."""
        self.write_session([saved_brave("Home / X", 2, spawn=True)])
        blank = {"0xb": brave_window("New Tab", 1)}
        # Gone by the second pass, so only a pairing in the first one places it.
        emitted = self.run_restore([{}, blank, {}], sweep_timeout=10)
        self.assertEqual(get_moves(emitted), {"0xb": "2"})

    def test_a_spare_window_does_not_take_the_entry_of_one_that_fits_it(self):
        """More windows than entries: listed first and on the entry's own
        workspace, the spare used to be given it."""
        self.write_session([saved_brave("Home / X", 2, spawn=True)])
        landed = {"0xs": brave_window("Welcome to Brave", 2), "0xx": brave_window("Home / X", 1)}
        self.assertEqual(get_moves(self.run_restore([{}, landed], sweep_timeout=5)), {"0xx": "2"})


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
        self.assertIn("brave-browser 0xaa 'Trade - Brave' on workspace 1", line)
        self.assertIn("from workspace 4", line)
        self.assertIn("placing it", line)
        self.assertEqual(err.getvalue(), "")

    def restore_logging(self, views, sweep_timeout):
        with (
            mock.patch.object(sys, "stdout", io.StringIO()) as out,
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            self.run_restore(views, sweep_timeout=sweep_timeout)
        return out.getvalue(), err.getvalue()

    def test_each_launch_is_logged(self):
        self.write_session([saved_window("code", ws=3)])
        out, _ = self.restore_logging([{}], sweep_timeout=0)
        self.assertIn("launched code onto workspace 3", out)

    def test_every_title_a_window_shows_until_it_is_paired_is_logged(self):
        """In order, with the address that ties the lines of one window together."""
        full = "▲ 84388.9 | Trade BTCUSDT | Bybit Perpetual Contracts"
        self.write_session([saved_brave(full, 1, spawn=True), saved_brave("Home / X", 2)])
        views = [{}] + [
            {"0xb": brave_window(title, 1), "0xx": brave_window("Home / X", 1)}
            for title in ("New Tab", "Bybit", full)
        ]
        out, err = self.restore_logging(views, sweep_timeout=10)
        about_b = [line.split(": ", 1)[1] for line in out.splitlines() if " 0xb " in line]
        self.assertEqual(
            about_b[:3],
            [
                "brave-browser 0xb turned up on workspace 1 titled 'New Tab - Brave'",
                "brave-browser 0xb is now titled 'Bybit - Brave'",
                f"brave-browser 0xb is now titled '{full} - Brave'",
            ],
        )
        self.assertIn("is the saved", about_b[3])
        self.assertEqual(err, "")

    def test_a_window_restore_has_no_entry_for_is_not_logged(self):
        self.write_session([saved_brave("Home / X", 2, spawn=True)])
        mines = {"class": "org.gnome.Mines", "workspace": {"id": 1}, "title": "Mines"}
        out, _ = self.restore_logging(
            [{}, {"0xx": brave_window("Home / X", 1), "0xm": mines}], sweep_timeout=3
        )
        self.assertNotIn("0xm", out)

    def test_a_wait_that_runs_out_is_logged(self):
        """Pairing on titles that may still be loading is worth knowing about."""
        self.patch(config, "TITLE_SETTLE", 2)
        self.write_session([saved_brave("Home / X", 2, spawn=True), saved_brave("JobBot Dashboard", 3)])
        out, err = self.restore_logging([{}, {"0xx": brave_window("Home / X", 1)}], sweep_timeout=6)
        self.assertIn("waited 2 s for brave-browser windows: 1 of 2 turned up, 0 still changing titles", out)
        self.assertIn(
            "no window turned up for brave-browser 'JobBot Dashboard - Brave' from workspace 3", err
        )

    def test_a_missing_window_says_whether_its_app_reopened_the_others(self):
        """Brave reopened two of three windows after a logout, 2026-09-25: its own
        session had recorded the third as closed. An app with no window at all
        never started, which is another fault, so its line stays plain."""
        self.write_session(
            [
                saved_brave("Home / X", 2, spawn=True),
                saved_brave("JobBot Dashboard", 3),
                saved_window("code", ws=5),
            ]
        )
        _, err = self.restore_logging([{}, {"0xx": brave_window("Home / X", 2)}], sweep_timeout=8)
        lines = [line.split(": ", 1)[1] for line in err.splitlines()]
        self.assertIn(
            "no window turned up for brave-browser 'JobBot Dashboard - Brave' from workspace 3;"
            " brave-browser reopened 1 of its 2 windows",
            lines,
        )
        self.assertIn("no window turned up for code '' from workspace 5", lines)
