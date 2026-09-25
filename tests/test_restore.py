"""The restore pass as a whole: when it runs, the snapshot copy it keeps, and what it leaves out."""

import io
import os
import sys
from unittest import mock

from omarchy_last_session import config
from tests.helpers import RestoreHarness, saved_window


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
