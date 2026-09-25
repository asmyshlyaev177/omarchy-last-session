"""The toast goes through Omarchy's own commands, played here by stand-ins that
log how they were called and print into both streams."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from omarchy_last_session import notification

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAND_IN = """#!{python}
import json
import os
import sys
import time

time.sleep({delay})
with open({log!r}, "a") as log:
    print(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]), file=log)
print("stand-in output")
print("stand-in error", file=sys.stderr)
"""

CHILD = """
from omarchy_last_session import notification

with notification.showing("Restoring", "Reopening 2 windows", "G", 1):
    print("restore's own line")
"""

SEND, DISMISS = "omarchy-notification-send", "omarchy-notification-dismiss"


class ToastCommands(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.bin = scratch.name
        self.log = os.path.join(self.bin, "calls")
        # The stand-ins alone: the suite must never raise a real toast on the
        # machine it runs on.
        patcher = mock.patch.dict(os.environ, {"PATH": self.bin})
        patcher.start()
        self.addCleanup(patcher.stop)

    def stand_in(self, name, delay=0):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write(STAND_IN.format(python=sys.executable, delay=delay, log=self.log))
        os.chmod(path, 0o755)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def test_the_toast_goes_up_then_is_taken_down_by_its_summary(self):
        self.stand_in(SEND)
        self.stand_in(DISMISS)
        with notification.showing("Restoring", "Reopening 2 windows", "G", 12.5):
            pass
        sent = [SEND, "--app-name", "omarchy-last-session", "-g", "G", "-t", "12500"]
        self.assertEqual(self.calls(), [sent + ["Restoring", "Reopening 2 windows"], [DISMISS, "Restoring"]])

    def test_the_toast_is_taken_down_only_once_it_is_up(self):
        """The shell takes down what is on screen under that summary, so a
        dismissal that overtook a slow toast would find nothing and leave it up."""
        self.stand_in(SEND, delay=0.5)
        self.stand_in(DISMISS)
        with notification.showing("Restoring", "", "G", 1):
            pass
        self.assertEqual([call[0] for call in self.calls()], [SEND, DISMISS])

    def test_the_toast_is_taken_down_when_the_block_fails(self):
        self.stand_in(SEND)
        self.stand_in(DISMISS)
        with self.assertRaises(RuntimeError):
            with notification.showing("Restoring", "", "G", 1):
                raise RuntimeError("hyprctl is gone")
        self.assertEqual([call[0] for call in self.calls()], [SEND, DISMISS])

    def test_a_hung_shell_holds_restore_up_for_moments_only(self):
        self.stand_in(SEND, delay=30)
        self.stand_in(DISMISS, delay=30)
        with (
            mock.patch.object(notification, "SEND_WAIT", 0.2),
            mock.patch.object(notification, "DISMISS_WAIT", 0.2),
        ):
            started = time.monotonic()
            with notification.showing("Restoring", "", "G", 1):
                entered = time.monotonic()
            ended = time.monotonic()
        self.assertLess(entered - started, 0.5)
        self.assertLess(ended - entered, 1.5)
        # Both were stopped before they got as far as logging.
        self.assertEqual(self.calls(), [])

    def test_without_the_commands_the_block_runs_all_the_same(self):
        """Outside Omarchy, and in the live suite's container, neither exists."""
        ran = []
        with notification.showing("Restoring", "", "G", 1):
            ran.append("block")
        self.assertEqual(ran, ["block"])

    def test_what_the_commands_print_stays_out_of_restore_s_output(self):
        """Restore's streams go to the journal, and the live suite holds its stderr empty."""
        self.stand_in(SEND)
        self.stand_in(DISMISS)
        child = subprocess.run(
            [sys.executable, "-c", CHILD],
            env={"PATH": self.bin, "PYTHONPATH": REPO},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual((child.stdout.splitlines(), child.stderr), (["restore's own line"], ""))
        self.assertEqual([call[0] for call in self.calls()], [SEND, DISMISS])
