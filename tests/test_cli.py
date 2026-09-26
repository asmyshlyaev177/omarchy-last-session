import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from omarchy_last_session import cli, config, hypr, log, proc, session
from tests.helpers import ConfigFileCase, StateDirCase, client, mode_of, write_executable


class Shutdown(StateDirCase):
    """session.json is overwritten shortly after the next login, so the
    shutdown copy is what a post-reboot comparison has to read."""

    def run_shutdown(self, clients):
        with (
            mock.patch.object(hypr, "query", return_value=clients),
            mock.patch.object(proc, "read_cmdline", return_value=["/bin/sh"]),
            mock.patch.object(session, "quit_session_keeping_apps", return_value=set()),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            return cli.main(["shutdown"])

    def test_shutdown_keeps_a_copy_of_the_snapshot(self):
        self.assertEqual(self.run_shutdown([client("code"), client("foot")]), 0)
        self.assertEqual(sorted(w["class"] for w in self.read_session(self.copy)), ["code", "foot"])

    def test_copy_matches_the_snapshot_exactly(self):
        self.run_shutdown([client("code")])
        with open(self.session) as a, open(self.copy) as b:
            self.assertEqual(a.read(), b.read())

    def test_the_copy_is_readable_by_this_user_only(self):
        self.addCleanup(os.umask, os.umask(0o000))
        self.run_shutdown([client("code")])
        self.assertEqual(mode_of(self.copy), 0o600)

    def test_plain_save_does_not_touch_the_copy(self):
        """Only a clean exit updates it; the 60-second daemon must not."""
        self.save_with([client("code")])
        self.assertFalse(os.path.exists(self.copy))

    def test_an_empty_desktop_at_shutdown_is_recorded_as_such(self):
        """Everything was closed before the power menu ran, so nothing should
        come back."""
        self.write_session([{"class": "code"}])
        self.assertEqual(self.run_shutdown([]), 0)
        self.assertEqual(self.read_session(), [])
        self.assertEqual(self.read_session(self.copy), [])


class Daemon(ConfigFileCase):
    """The loop sleeps on the event stream, looks at the desktop each time it
    wakes, and stops when the compositor goes away."""

    A = {"0xa": {"class": "code"}}
    AB = {"0xa": {"class": "code"}, "0xb": {"class": "foot"}}

    def run_wakes(self, layouts, save=None):
        """One wake per layout, then the compositor goes. Returns the saves
        made, the sleep the loop asked for each time, and stderr."""
        waits = []

        def wait(stream, timeout):
            waits.append(timeout)
            return len(waits) < len(layouts)

        with (
            mock.patch.object(time, "sleep"),
            mock.patch.object(time, "monotonic", side_effect=itertools.count(1000, 5)),
            mock.patch.object(hypr, "open_event_stream", return_value="stream"),
            mock.patch.object(hypr, "wait_for_placement_change", side_effect=wait),
            mock.patch.object(hypr, "get_layout", side_effect=layouts),
            mock.patch.object(session, "save_session", side_effect=save or itertools.count(1)) as saved,
            mock.patch.object(sys, "stdout", io.StringIO()) as out,
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            cli.run_daemon()
        self.logged = out.getvalue()
        return saved.call_count, waits, err.getvalue()

    def test_an_edited_config_file_is_read_at_the_next_wake(self):
        """Saved at 1000, asked at 1005 how long to sleep: the file's interval
        says 2, the default would say 55."""
        self.write_config({"save_interval": 7})
        _, waits, _ = self.run_wakes([self.A])
        self.assertEqual(waits, [2])
        self.assertIn(f"reloaded {config.CONFIG_FILE}", self.logged)

    def test_an_unchanged_file_is_not_announced(self):
        self.run_wakes([self.A, self.A])
        self.assertNotIn("reloaded", self.logged)

    def test_a_log_line_reaches_the_pipe_at_once(self):
        """The daemon runs for the whole session, so a buffered line would
        reach the journal only when the session ends."""
        with mock.patch.object(sys, "stdout", mock.Mock()) as out:
            log("something")
        out.flush.assert_called()

    def test_saves_on_the_first_look_and_then_only_on_a_change(self):
        saves, _, _ = self.run_wakes([self.A, self.A, self.AB])
        self.assertEqual(saves, 2)

    def test_it_waits_for_the_compositor_rather_than_looking_on_a_timer(self):
        _, waits, _ = self.run_wakes([self.A, self.A])
        self.assertEqual(len(waits), 2)
        self.assertTrue(all(wait > 0 for wait in waits), waits)

    def test_it_stops_when_the_compositor_goes_away(self):
        """Its windows are closing; saving now would record a teardown."""
        saves, waits, _ = self.run_wakes([self.A])
        self.assertEqual((saves, len(waits)), (1, 1))

    def test_a_failed_save_is_reported_and_tried_again(self):
        saves, _, err = self.run_wakes([self.A, self.A], save=[OSError("disk full"), 1])
        self.assertEqual(saves, 2)
        self.assertIn("save failed: disk full", err)


class ConfigFile(ConfigFileCase):
    """Written with every default and a comment on each the first time the
    plugin runs, so the menu entry always opens a file worth reading."""

    def run_save(self):
        with (
            mock.patch.object(session, "save_session", return_value=0),
            mock.patch.object(sys, "stdout", io.StringIO()),
        ):
            self.assertEqual(cli.main(["save"]), 0)

    def test_the_first_run_writes_the_template(self):
        self.run_save()
        with open(config.CONFIG_FILE) as f:
            self.assertEqual(f.read(), config.render_template())

    def test_the_plugins_own_write_is_not_taken_for_an_edit(self):
        self.run_save()
        self.assertFalse(config.reload_if_changed())

    def test_an_existing_file_is_left_as_it_is(self):
        self.write_config({"exclude": ["mine"]})
        self.run_save()
        self.assertEqual(config.load_file()["exclude"], ["mine"])

    def test_config_hands_the_file_to_the_config_editor(self):
        with mock.patch.object(os, "execvp") as execvp:
            self.assertEqual(cli.main(["config"]), 0)
        execvp.assert_called_once_with(cli.CONFIG_EDITOR, [cli.CONFIG_EDITOR, config.CONFIG_FILE])
        self.assertTrue(os.path.exists(config.CONFIG_FILE))

    def test_config_without_omarchys_editor_names_the_file_and_fails(self):
        """Off an Omarchy desktop, or from a stripped PATH: no traceback."""
        with (
            mock.patch.object(os, "execvp", side_effect=FileNotFoundError),
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            self.assertEqual(cli.main(["config"]), 1)
        self.assertIn(config.CONFIG_FILE, err.getvalue())

    def test_a_state_dir_that_cannot_be_made_fails_the_save_and_not_the_daemon(self):
        blocker = os.path.join(self.config_dir.name, "blocker")
        open(blocker, "w").close()
        self.write_config({"state_dir": os.path.join(blocker, "state")})
        config.reload()
        with (
            mock.patch.object(hypr, "query", return_value=[]),
            mock.patch.object(hypr, "get_layout", return_value={}),
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            self.assertEqual(cli.save_if_due(session.SaveScheduler()), config.SAVE_INTERVAL)
        self.assertIn("save failed", err.getvalue())


OMARCHY = os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy"
OMARCHY_MENU_MODEL = os.path.join(OMARCHY, "shell/plugins/menu/MenuModel.js")
# Loads the rows on stdin over Omarchy's own the way its menu does, and prints
# each row they replace as Omarchy has it and as it ends up.
MERGE_WITH_OMARCHYS_MENU = """
const fs = require("fs");
const model = require(process.argv[1]);
const own = model.parseMenuJsonc(fs.readFileSync(process.argv[2], "utf8"));
const rows = model.parseMenuJsonc(fs.readFileSync(0, "utf8"));
const before = model.mergeMenuSources(own, []).items;
const after = model.mergeMenuSources(own, rows).items;
const replaced = rows.filter((row) => before[row.id]).map((row) => [row.id, [before[row.id], after[row.id]]]);
console.log(JSON.stringify(Object.fromEntries(replaced)));
"""
# The power rows and their neighbours in Omarchy 4.0.4's menu, written the way it
# writes them, with comment lines and trailing commas.
OMARCHY_MENU = """{
  // Omarchy menu definition.
  "system": {"icon":"","label":"System","aliases":["power-menu"]},
  "system.lock": {"icon":"","label":"Lock","action":"omarchy-system-lock"},

  "system.logout": {"icon":"󰍃","label":"Logout","action":"omarchy-system-logout"},
  "system.reboot": {"icon":"󰜉","label":"Reboot","action":"omarchy-system-reboot"},
  "system.shutdown": {"icon":"󰐥","label":"Shutdown","action":"omarchy-system-shutdown"},
}
"""


class MenuRows(ConfigFileCase):
    """`menu` prints what to paste into the menu file, with the paths of the
    copy that printed it, so a moved plugin never leaves a stale row."""

    ROOT = "~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session"
    LAUNCHER = f"{ROOT}/bin/omarchy-last-session"

    def setUp(self):
        super().setUp()
        omarchy = os.path.join(self.config_dir.name, "omarchy")
        self.omarchy_menu = os.path.join(omarchy, cli.OMARCHY_MENU)
        self.write_omarchy_menu(OMARCHY_MENU)
        patcher = mock.patch.dict(os.environ, {"OMARCHY_PATH": omarchy})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_omarchy_menu(self, text):
        os.makedirs(os.path.dirname(self.omarchy_menu), exist_ok=True)
        with open(self.omarchy_menu, "w", encoding="utf-8") as f:
            f.write(text)

    def rows(self, root=ROOT):
        text = cli.render_menu_rows(root, cli.read_power_rows(self.omarchy_menu))
        return json.loads("{" + text.rstrip().rstrip(",") + "}")

    def power_action(self, command):
        return f"[[ -x {self.LAUNCHER} ]] && {self.LAUNCHER} shutdown; {command}"

    def print_menu(self):
        """(exit code, stdout, stderr) of the `menu` command."""
        with (
            mock.patch.object(sys, "stdout", io.StringIO()) as out,
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            code = cli.main(["menu"])
        return code, out.getvalue(), err.getvalue()

    def test_the_rows_are_the_config_row_and_omarchys_power_rows(self):
        self.assertEqual(
            set(self.rows()),
            {"setup.config.last-session", "system.logout", "system.reboot", "system.shutdown"},
        )

    def test_the_config_row_hides_with_the_plugin_directory(self):
        """The directory outlives any move of the launcher inside it."""
        row = self.rows()["setup.config.last-session"]
        self.assertEqual(row["when"], f"[[ -d {self.ROOT} ]]")
        self.assertEqual(row["action"], f"{self.LAUNCHER} config")
        self.assertEqual(row["label"], "Last Session")

    def test_the_power_rows_power_off_with_or_without_the_plugin(self):
        for power in ("logout", "reboot", "shutdown"):
            action = self.rows()[f"system.{power}"]["action"]
            self.assertEqual(action, self.power_action(f"omarchy-system-{power}"))

    def test_a_power_row_is_found_by_its_command_and_keeps_every_field_but_the_action(self):
        """A row that left out the icon and label showed as "system.shutdown" with no icon."""
        own = {"icon": "x", "label": "Power off", "description": "Ends the session", "when": "true"}
        self.write_omarchy_menu(json.dumps({"power.off": {**own, "action": "omarchy-system-shutdown"}}))
        row = self.rows()["power.off"]
        self.assertEqual(row.pop("action"), self.power_action("omarchy-system-shutdown"))
        self.assertEqual(row, own)

    @unittest.skipUnless(
        shutil.which("node") and os.path.isfile(OMARCHY_MENU_MODEL), "needs node and Omarchy's menu"
    )
    def test_omarchys_menu_takes_only_the_action_from_the_rows_that_replace_its_own(self):
        """Through Omarchy's own menu code and menu, which fill in whatever a row leaves out."""
        omarchy_menu = os.path.join(OMARCHY, cli.OMARCHY_MENU)
        done = subprocess.run(
            ["node", "-e", MERGE_WITH_OMARCHYS_MENU, OMARCHY_MENU_MODEL, omarchy_menu],
            input="{" + cli.render_menu_rows(self.ROOT, cli.read_power_rows(omarchy_menu)) + "}",
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        replaced = json.loads(done.stdout)
        self.assertEqual(len(replaced), len(cli.POWER_COMMANDS), replaced)
        for row_id, (own, merged) in replaced.items():
            del own["action"], merged["action"]
            self.assertEqual(merged, own, row_id)

    def test_the_guards_hold_when_bash_runs_them(self):
        root = os.path.join(self.config_dir.name, "plugin")
        launcher = os.path.join(root, "bin", "omarchy-last-session")
        write_executable(launcher)
        with open(launcher, "a") as f:
            f.write('echo "$1"\n')
        present, gone = self.rows(root), self.rows(root + "-gone")
        self.assertEqual(self.bash(present["setup.config.last-session"]["when"]).returncode, 0)
        self.assertNotEqual(self.bash(gone["setup.config.last-session"]["when"]).returncode, 0)
        for rows, expected in ((present, "shutdown\npowered off\n"), (gone, "powered off\n")):
            action = rows["system.logout"]["action"].replace("omarchy-system-logout", "echo powered off")
            done = self.bash(action)
            self.assertEqual((done.stdout, done.stderr), (expected, ""))

    def bash(self, script):
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_the_command_prints_the_rows_for_the_copy_it_runs_from(self):
        code, out, err = self.print_menu()
        self.assertEqual((code, err), (0, ""))
        power_rows = cli.read_power_rows(self.omarchy_menu)
        self.assertEqual(out, cli.render_menu_rows(cli.tilde(cli.PLUGIN_DIR), power_rows))
        self.assertTrue(os.path.isfile(os.path.join(cli.PLUGIN_DIR, "manifest.json")), cli.PLUGIN_DIR)

    def test_without_omarchys_power_rows_the_command_prints_nothing_and_fails(self):
        """Rows without them would be the config row alone, which reads as success."""
        broken = {
            "missing": None,
            "not JSON": "{",
            "not an object": "[]",
            "no power row": '{"system.lock": {"icon":"","label":"Lock","action":"omarchy-system-lock"}}',
        }
        for case, text in broken.items():
            with self.subTest(case):
                if text is None:
                    os.unlink(self.omarchy_menu)
                else:
                    self.write_omarchy_menu(text)
                code, out, err = self.print_menu()
                self.assertEqual((code, out), (1, ""))
                self.assertIn(self.omarchy_menu, err)

    def test_home_is_written_as_a_tilde(self):
        self.assertEqual(cli.tilde(os.path.expanduser("~/.config/x")), "~/.config/x")
        self.assertEqual(cli.tilde(os.path.expanduser("~")), "~")
        self.assertEqual(cli.tilde("/opt/x"), "/opt/x")
        self.assertEqual(cli.tilde(os.path.expanduser("~") + "2/x"), os.path.expanduser("~") + "2/x")


class Usage(unittest.TestCase):
    def test_unknown_command_prints_usage_and_fails(self):
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertEqual(cli.main(["bogus"]), 1)
        self.assertIn("restore", err.getvalue())

    def test_no_command_prints_usage_and_fails(self):
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(cli.main([]), 1)
