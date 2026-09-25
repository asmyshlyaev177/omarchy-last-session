"""The restore pass as a whole: when it runs, the snapshot copy it keeps, and what it leaves out."""

import io
import os
import sys
from unittest import mock

from omarchy_last_session import config, hypr, restore
from omarchy_last_session.restore import sweep
from tests.helpers import RestoreHarness, live_window, saved_window


class ToastWhileRestoring(RestoreHarness):
    """Windows open and move about for a while after login, and a toast says
    why for as long as they do."""

    TWO_MONITORS = [{"id": 0, "name": "eDP-1"}, {"id": 1, "name": "DP-9"}]

    def test_the_toast_is_up_from_before_the_first_launch_until_the_last_move(self):
        self.write_session([saved_window("code", ws=17, monitor_name="DP-9"), saved_window("foot", ws=2)])
        landed = {"0xc": live_window("code", ws=17), "0xf": live_window("foot", ws=1)}
        with mock.patch.object(hypr, "query", return_value=self.TWO_MONITORS):
            emitted = self.run_restore([{}, landed], sweep_timeout=5)
        self.assertEqual(self.timeline[0], f"toast up: {restore.TOAST_SUMMARY} Reopening 2 windows")
        self.assertEqual(self.timeline[1:-1], emitted)
        self.assertEqual(self.timeline[-1], f"toast down: {restore.TOAST_SUMMARY}")
        self.assertIn("exec_cmd", emitted[0])
        self.assertIn("workspace.move", emitted[-1])

    def test_no_toast_when_restore_has_nothing_to_do(self):
        """Every hot reload of the plugin runs restore into a populated desktop."""
        self.write_session([saved_window("code")])
        with self.subTest("desktop already populated"):
            self.run_restore([{f"0x{i}": {"class": "x", "workspace": {"id": 1}} for i in range(4)}])
            self.assertEqual(self.timeline, [])
        open(self.disabled, "w").close()
        with self.subTest("disabled"):
            self.run_restore([{}])
            self.assertEqual(self.timeline, [])
        os.remove(self.disabled)
        os.remove(self.session)
        with self.subTest("no snapshot"):
            self.run_restore([{}])
            self.assertEqual(self.timeline, [])

    def test_the_toast_goes_when_the_pass_fails(self):
        self.write_session([saved_window("code")])
        self.patch(sweep, "sweep", mock.Mock(side_effect=RuntimeError("hyprctl is gone")))
        with self.assertRaises(RuntimeError):
            self.run_restore([{}])
        self.assertEqual(self.timeline[0], f"toast up: {restore.TOAST_SUMMARY} Reopening 1 window")
        self.assertEqual(self.timeline[-1], f"toast down: {restore.TOAST_SUMMARY}")

    def test_the_toast_asks_to_stay_longer_than_the_sweep_can_run(self):
        """It is taken down when the pass ends. One that expired sooner would
        leave windows moving with nothing on screen to say why."""
        self.write_session([saved_window("code")])
        self.run_restore([{}], sweep_timeout=40)
        self.assertGreater(self.toast_seconds, 40)


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
