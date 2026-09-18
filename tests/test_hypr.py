import io
import subprocess
import sys
import unittest
from unittest import mock

from omarchy_last_session import config, hypr


class WorkspaceSelector(unittest.TestCase):
    def test_named_workspace(self):
        self.assertEqual(hypr.format_workspace_selector({"id": 4, "name": "code"}), "name:code")

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
