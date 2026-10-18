"""The restore pass as a whole: when it runs, the snapshot copy it keeps, and what it leaves out."""

import io
import os
import stat
import sys
from unittest import mock

from omarchy_last_session import config, hypr, restore
from omarchy_last_session.restore import sweep
from tests.helpers import RestoreHarness, live_window, mode_of, saved_window


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

    def test_a_second_run_in_the_same_hyprland_launches_nothing(self):
        """What a shell restart, such as the one omarchy update ends with, does."""
        self.write_session([saved_window("code")])
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2"}):
            self.assertTrue(self.run_restore([{}], is_login=True))
            with mock.patch.object(sys, "stderr", io.StringIO()) as err:
                self.assertEqual(self.run_restore([{}], is_login=True), [])
        self.assertIn("already ran", err.getvalue())
        self.assertIn("without --login", err.getvalue())

    def test_a_new_hyprland_restores_and_records_its_signature(self):
        self.write_session([saved_window("code")])
        with open(self.restored_instance, "w") as f:
            f.write("old_1_2\n")
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "new_3_4"}):
            self.assertTrue(self.run_restore([{}], is_login=True))
        with open(self.restored_instance) as f:
            self.assertEqual(f.read(), "new_3_4")

    def test_without_a_signature_restore_runs_and_records_nothing(self):
        self.write_session([saved_window("code")])
        self.assertNotIn("HYPRLAND_INSTANCE_SIGNATURE", os.environ)
        self.assertTrue(self.run_restore([{}], is_login=True))
        self.assertTrue(self.run_restore([{}], is_login=True))
        self.assertFalse(os.path.exists(self.restored_instance))

    def test_a_run_into_a_populated_desktop_still_counts_as_the_login(self):
        """The populated desktop is the login; a shell restart after the user closes windows is not."""
        self.write_session([saved_window("code")])
        populated = {f"0x{i}": live_window("x") for i in range(4)}
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2"}):
            self.assertEqual(self.run_restore([populated], is_login=True), [])
            self.assertEqual(self.run_restore([{}], is_login=True), [])

    def test_a_manual_restore_runs_whatever_the_login_run_recorded(self):
        """The once-per-session rule is for the shell's own run; a restore asked for by hand still runs."""
        self.write_session([saved_window("code")])
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2"}):
            self.assertTrue(self.run_restore([{}], is_login=True))
            self.assertTrue(self.run_restore([{}]))
            self.assertEqual(self.run_restore([{}], is_login=True), [])

    def test_a_manual_restore_before_the_login_run_leaves_it_to_run(self):
        self.write_session([saved_window("code")])
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2"}):
            self.assertTrue(self.run_restore([{}]))
            self.assertTrue(self.run_restore([{}], is_login=True))

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

    def test_a_login_run_stopped_by_the_disabled_flag_still_counts_as_the_login(self):
        """Removing the flag later in the session must not turn the next shell restart into a login."""
        self.write_session([saved_window("code")])
        open(self.disabled, "w").close()
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2"}):
            self.assertEqual(self.run_restore([{}], is_login=True), [])
            os.remove(self.disabled)
            with mock.patch.object(sys, "stderr", io.StringIO()) as err:
                self.assertEqual(self.run_restore([{}], is_login=True), [])
        self.assertIn("already ran", err.getvalue())

    def test_an_empty_signature_is_no_signature(self):
        self.write_session([saved_window("code")])
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": ""}):
            self.assertTrue(self.run_restore([{}], is_login=True))
            self.assertTrue(self.run_restore([{}], is_login=True))
        self.assertFalse(os.path.exists(self.restored_instance))

    def test_whitespace_around_the_signature_is_ignored(self):
        self.write_session([saved_window("code")])
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": " abc_1_2\n"}):
            self.assertTrue(self.run_restore([{}], is_login=True))
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2"}):
            self.assertEqual(self.run_restore([{}], is_login=True), [])
        with open(self.restored_instance) as f:
            self.assertEqual(f.read(), "abc_1_2")


class RestoredInstanceFile(RestoreHarness):
    """A marker restore cannot read or write costs a repeat restore, never the login's."""

    SIGNATURE = {"HYPRLAND_INSTANCE_SIGNATURE": "abc_1_2"}

    def login_restores(self):
        with (
            mock.patch.dict(os.environ, self.SIGNATURE),
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            restored = bool(self.run_restore([{}], is_login=True))
        return restored, err.getvalue()

    def test_a_garbled_marker_is_warned_about_and_replaced(self):
        self.write_session([saved_window("code")])
        with open(self.restored_instance, "wb") as f:
            f.write(b"\xff\xfe")
        restored, err = self.login_restores()
        self.assertTrue(restored)
        self.assertIn(self.restored_instance, err)
        with open(self.restored_instance) as f:
            self.assertEqual(f.read(), "abc_1_2")

    def test_a_directory_in_the_markers_place_restores_at_every_login_run(self):
        self.write_session([saved_window("code")])
        os.mkdir(self.restored_instance)
        for run in (1, 2):
            with self.subTest(run=run):
                restored, err = self.login_restores()
                self.assertTrue(restored)
                self.assertIn(self.restored_instance, err)

    def test_a_marker_that_cannot_be_written_does_not_stop_restore(self):
        self.write_session([saved_window("code")])
        with mock.patch.object(restore.session, "write_private", side_effect=PermissionError("read-only")):
            restored, err = self.login_restores()
        self.assertTrue(restored)
        self.assertIn("read-only", err)

    def test_the_marker_is_this_users_alone(self):
        self.write_session([saved_window("code")])
        self.login_restores()
        self.assertEqual(mode_of(self.restored_instance), 0o600)

    def test_a_symlink_in_the_markers_place_is_replaced_not_followed(self):
        self.write_session([saved_window("code")])
        victim = os.path.join(self.state_dir_for_victim(), "victim")
        with open(victim, "w") as f:
            f.write("abc_1_2")
        os.symlink(victim, self.restored_instance)
        restored, _ = self.login_restores()
        self.assertTrue(restored, "a planted symlink holding the signature must not skip the login run")
        self.assertTrue(stat.S_ISREG(os.lstat(self.restored_instance).st_mode))
        with open(victim) as f:
            self.assertEqual(f.read(), "abc_1_2")

    def state_dir_for_victim(self):
        path = os.path.join(os.path.dirname(self.restored_instance), "elsewhere")
        os.makedirs(path)
        return path


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
