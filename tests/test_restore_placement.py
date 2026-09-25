import unittest
from unittest import mock

from omarchy_last_session import hypr
from omarchy_last_session.restore import placement
from tests.helpers import saved_window


class PlaceWindow(unittest.TestCase):
    def emit(self, win, address="0xaa", origins=None, workspace_on="eDP-1", floating=False):
        """`workspace_on` is the monitor the saved workspace lives on now."""
        live = [{"id": win["workspace"]["id"], "monitor": workspace_on}]
        with (
            mock.patch.object(hypr, "dispatch") as dispatched,
            mock.patch.object(hypr, "query", return_value=live),
        ):
            placement.place_window(win, address, origins or {}, floating=floating)
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
        self.assertFalse(placement.is_out_of_place(saved, landed, self.ORIGINS))

    def test_a_window_saved_tiled_that_floats_now_is_out_of_place(self):
        landed = {"workspace": {"id": 2}, "floating": True, "at": [0, 0], "monitor": 0}
        self.assertTrue(placement.is_out_of_place(saved_window("steam"), landed, self.ORIGINS))

    def test_a_different_offset_is(self):
        saved = saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9")
        landed = {"workspace": {"id": 2}, "floating": True, "at": [2250, 485], "monitor": 0}
        self.assertTrue(placement.is_out_of_place(saved, landed, self.ORIGINS))

    def test_saved_corner_wins_over_where_that_monitor_sits_now(self):
        """The offset is what restore replays, so a monitor that has been
        moved since the snapshot must not shift every window on it."""
        win = dict(
            saved_window("x", floating=True, at=(2250, 485), monitor_name="DP-9"), monitor_at=[1920, 0]
        )
        self.assertEqual(placement.get_saved_offset(win, {"DP-9": (3840, 0)}), (330, 485))
