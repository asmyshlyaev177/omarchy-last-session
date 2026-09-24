import io
import os
import socket
import subprocess
import sys
import time
import unittest
from unittest import mock

from omarchy_last_session import config, hypr


class WorkspaceSelector(unittest.TestCase):
    def test_named_workspace(self):
        self.assertEqual(hypr.format_workspace_selector({"id": -1337, "name": "code"}), "name:code")

    def test_renamed_numbered_workspace_goes_by_its_number(self):
        # name:code would open a new named workspace, numbered below zero
        self.assertEqual(hypr.format_workspace_selector({"id": 4, "name": "code"}), "4")

    def test_numbered_workspace(self):
        self.assertEqual(hypr.format_workspace_selector({"id": 4, "name": "4"}), "4")

    def test_special_workspace(self):
        self.assertEqual(
            hypr.format_workspace_selector({"id": -98, "name": "special:magic"}), "special:magic"
        )


class LuaQuoting(unittest.TestCase):
    def test_long_string_escapes_its_own_closer(self):
        emitted = hypr.quote_lua_long("echo ]]")
        self.assertTrue(emitted.startswith("[=["))
        self.assertTrue(emitted.endswith("]=]"))

    def test_quote_in_workspace_name_is_escaped(self):
        self.assertEqual(hypr.quote_lua("it's"), "'it\\'s'")

    def test_window_is_selected_by_address(self):
        self.assertEqual(hypr.quote_window("0xaa"), "'address:0xaa'")


def reply_with(stdout, request):
    """Run one hypr request against a hyprctl that answers `stdout`."""
    done = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    with (
        mock.patch.object(subprocess, "run", return_value=done),
        mock.patch.object(sys, "stderr", io.StringIO()) as err,
    ):
        return request(), err.getvalue()


class DispatchReporting(unittest.TestCase):
    """Every placement is a dispatch, so a dispatcher missing on the running
    Hyprland must not fail silently."""

    def test_accepted_dispatch_is_quiet(self):
        self.assertEqual(reply_with("ok\n", lambda: hypr.dispatch("hl.dsp.whatever()")), (True, ""))

    def test_rejected_dispatch_is_reported(self):
        ok, err = reply_with(
            "error: attempt to call a table value\n", lambda: hypr.dispatch("hl.dsp.whatever()")
        )
        self.assertFalse(ok)
        self.assertIn("dispatch rejected", err)
        self.assertIn("hl.dsp.whatever()", err)

    def test_empty_reply_is_reported(self):
        ok, err = reply_with("", lambda: hypr.dispatch("hl.dsp.whatever()"))
        self.assertFalse(ok)
        self.assertIn("no reply", err)


class EvalReporting(unittest.TestCase):
    """Groups are built through the Lua API, so a rejected eval must not pass
    silently any more than a rejected dispatch."""

    LUA = "hl.get_window('address:0xa')"

    def test_accepted_eval_is_quiet(self):
        self.assertEqual(reply_with("ok\n", lambda: hypr.eval_lua(self.LUA)), (True, ""))

    def test_rejected_eval_is_reported(self):
        ok, err = reply_with("error: attempt to index a nil value\n", lambda: hypr.eval_lua(self.LUA))
        self.assertFalse(ok)
        self.assertIn("eval rejected", err)
        self.assertIn(self.LUA, err)

    def test_empty_reply_is_reported(self):
        ok, err = reply_with("", lambda: hypr.eval_lua(self.LUA))
        self.assertFalse(ok)
        self.assertIn("no reply", err)


class MonitorOrigins(unittest.TestCase):
    def test_keyed_by_name_and_id(self):
        monitors = [{"id": 0, "name": "eDP-1", "x": 0, "y": 0}, {"id": 1, "name": "DP-9", "x": 1920, "y": 0}]
        with mock.patch.object(hypr, "query", return_value=monitors):
            origins = hypr.get_monitor_origins()
        self.assertEqual(origins["DP-9"], (1920, 0))
        self.assertEqual(origins[1], (1920, 0))
        self.assertEqual(origins["eDP-1"], (0, 0))


class Events(unittest.TestCase):
    """A real socket pair stands in for Hyprland's event socket: the daemon
    sleeps on it, so what matters is which events wake it."""

    def setUp(self):
        self.compositor, ours = socket.socketpair(socket.AF_UNIX)
        self.addCleanup(self.compositor.close)
        self.addCleanup(ours.close)
        self.stream = hypr.EventStream(ours)

    def wait(self, timeout=0.2):
        """(returned, whether it woke before the timeout)."""
        started = time.monotonic()
        returned = self.stream.wait(timeout)
        return returned, time.monotonic() - started < timeout

    def send(self, *lines):
        self.compositor.send(("".join(f"{line}\n" for line in lines)).encode())

    def test_a_window_opening_wakes_it(self):
        self.send("openwindow>>0xa,1,foot,foot")
        self.assertEqual(self.wait(), (True, True))

    def test_a_window_closing_wakes_it(self):
        self.send("closewindow>>0xa")
        self.assertEqual(self.wait(), (True, True))

    def test_a_window_changing_workspace_wakes_it(self):
        self.send("movewindowv2>>0xa,3,3")
        self.assertEqual(self.wait(), (True, True))

    def test_floating_and_fullscreen_and_group_changes_wake_it(self):
        for event in ("changefloatingmode>>0xa,1", "fullscreen>>1", "moveintogroup>>0xa"):
            with self.subTest(event=event):
                self.send(event)
                self.assertEqual(self.wait(), (True, True))

    def test_a_title_change_does_not_wake_it(self):
        """A page with a live ticker retitles several times a second, and a
        title is not placement: waking on one would be worse than a timer."""
        self.send("windowtitle>>0xa", "windowtitlev2>>0xa,BTC 80156.6", "activewindow>>foot,x")
        self.assertEqual(self.wait(), (True, False))

    def test_focus_and_layout_changes_do_not_wake_it(self):
        self.send("activewindowv2>>0xa", "focusedmon>>DP-9,3", "activelayout>>kbd,us")
        self.assertEqual(self.wait(), (True, False))

    def test_an_event_split_across_two_reads_is_still_seen(self):
        self.compositor.send(b"windowtitle>>0xa\nmovewin")
        self.compositor.send(b"dowv2>>0xa,3,3\n")
        self.assertEqual(self.wait(), (True, True))

    def test_a_partial_line_alone_does_not_wake_it(self):
        self.compositor.send(b"openwindo")
        self.assertEqual(self.wait(), (True, False))

    def test_the_compositor_going_away_stops_the_wait(self):
        """A session being torn down must not be snapshotted half closed."""
        self.compositor.close()
        self.assertEqual(self.wait(), (False, True))


class OpenEventStream(unittest.TestCase):
    def test_without_an_instance_it_falls_back_to_the_timer(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            self.assertIsNone(hypr.open_event_stream())
        self.assertIn("saving on a timer instead", err.getvalue())

    def test_a_missing_socket_falls_back_to_the_timer(self):
        environ = {"HYPRLAND_INSTANCE_SIGNATURE": "nosuchsession", "XDG_RUNTIME_DIR": "/nonexistent"}
        with (
            mock.patch.dict(os.environ, environ, clear=True),
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            self.assertIsNone(hypr.open_event_stream())
        self.assertIn("saving on a timer instead", err.getvalue())

    def test_without_a_stream_the_wait_is_a_plain_sleep(self):
        with mock.patch.object(time, "sleep") as slept:
            self.assertTrue(hypr.wait_for_placement_change(None, 42))
        slept.assert_called_once_with(42)


class Layout(unittest.TestCase):
    def test_placement_without_titles_keyed_by_address(self):
        clients = [
            {
                "address": "0x1",
                "class": "code",
                "mapped": True,
                "title": "drifts",
                "focusHistoryID": 3,
                "workspace": {"id": 2},
                "at": [0, 0],
                "size": [1, 1],
                "floating": False,
                "pinned": False,
                "fullscreen": 0,
                "monitor": 0,
                "grouped": [],
            }
        ]
        with mock.patch.object(hypr, "query", return_value=clients):
            layout = hypr.get_layout()
        self.assertEqual(list(layout), ["0x1"])
        self.assertNotIn("title", layout["0x1"])
        self.assertNotIn("focusHistoryID", layout["0x1"])
        self.assertEqual(layout["0x1"]["workspace"], {"id": 2})


class ManagedClients(unittest.TestCase):
    def test_skips_unmapped_and_classless_and_excluded(self):
        clients = [
            {"address": "0x1", "class": "keep", "mapped": True},
            {"address": "0x2", "class": "gone", "mapped": False},
            {"address": "0x3", "class": "", "mapped": True},
            {"address": "0x4", "class": "skipme", "mapped": True},
        ]
        with (
            mock.patch.object(config, "EXCLUDE_CLASSES", {"skipme"}),
            mock.patch.object(hypr, "query", return_value=clients),
        ):
            self.assertEqual(list(hypr.get_managed_clients()), ["0x1"])
