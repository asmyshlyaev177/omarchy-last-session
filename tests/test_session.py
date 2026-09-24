import io
import json
import os
import sys
import time
import unittest
from unittest import mock

from omarchy_last_session import config, hypr, kitty, proc, session
from tests.helpers import (
    BRAVE_BLOB,
    StateDirCase,
    client,
    grouped_clients,
    mode_of,
    pretend_runnable,
    saved_window,
)


class Save(StateDirCase):
    def test_an_empty_desktop_is_saved_as_empty(self):
        """Guarding against the power menu's close-all is the daemon's job."""
        self.write_session([{"class": "code"}])
        self.assertEqual(self.save_with([]), 0)
        self.assertEqual(self.read_session(), [])

    def test_second_window_of_one_process_is_not_respawned(self):
        two = [
            client("brave-browser", pid=7, workspace={"id": 1, "name": "1"}),
            client("brave-browser", pid=7, workspace={"id": 2, "name": "2"}),
        ]
        with pretend_runnable():
            self.assertEqual(self.save_with(two, cmdline=(BRAVE_BLOB,)), 2)
        self.assertEqual([w["spawn"] for w in self.read_session()], [True, False])

    def test_hyprlands_own_dialog_is_not_saved(self):
        """Restoring the compositor's "not responding" prompt would ask again
        about a process that is gone."""
        self.save_with([client("hyprland-dialog"), client("foot")])
        self.assertEqual([w["class"] for w in self.read_session()], ["foot"])

    def test_excluded_class_is_not_saved(self):
        with mock.patch.object(config, "EXCLUDE_CLASSES", {"jobbot-search"}):
            self.save_with([client("jobbot-search")])
        self.assertEqual(self.read_session(), [])


class SecondWindowOfOneProcess(StateDirCase):
    """Nautilus reopens nothing by itself, so every window of its has to be
    launched. A browser and Code reopen their own, and one process serves all
    of their windows: launching again opens a spare empty one instead."""

    def spawn_flags(self, clients):
        self.save_with(clients)
        return [w["spawn"] for w in self.read_session()]

    def test_every_window_of_an_ordinary_app_is_launched(self):
        two = [client("com.mitchellh.ghostty", pid=7), client("com.mitchellh.ghostty", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, True])

    def test_code_is_launched_once_per_process(self):
        """One process serves every Code window and it reopens them itself, so
        a second launch only adds an empty window with no project in it."""
        two = [client("code", pid=7), client("code", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, False])

    def test_nautilus_second_window_is_launched(self):
        two = [client("org.gnome.Nautilus", pid=7), client("org.gnome.Nautilus", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, True])

    def test_browser_is_still_launched_once_per_process(self):
        """Brave restores its own windows; launching again would duplicate."""
        two = [client("brave-browser", pid=7), client("brave-browser", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, False])

    def test_separate_processes_are_each_launched(self):
        two = [client("brave-browser", pid=7), client("brave-browser", pid=8)]
        self.assertEqual(self.spawn_flags(two), [True, True])

    def test_libreoffice_is_launched_once_per_process(self):
        """The start centre, every document window and every dialog belong to
        one soffice process and report its command line."""
        three = [client("libreoffice-writer", pid=7), client("soffice", pid=7), client("soffice", pid=7)]
        self.assertEqual(self.spawn_flags(three), [True, False, False])

    def test_gimp_is_launched_once_per_process(self):
        """A second image joins the running GIMP rather than starting one."""
        two = [client("gimp", pid=7), client("gimp", pid=7)]
        self.assertEqual(self.spawn_flags(two), [True, False])


class SingleInstanceApps(unittest.TestCase):
    """Shutdown SIGTERMs the session keeping apps so they write their windows
    down before the power menu closes anything."""

    def test_libreoffice_and_gimp_are_launched_once_but_never_signalled(self):
        """Both answer a SIGTERM with a save prompt, then come back offering
        document recovery instead of the document."""
        for cls in ("soffice", "libreoffice-writer", "libreoffice-calc", "gimp"):
            with self.subTest(cls=cls):
                self.assertIn(cls, config.SINGLE_INSTANCE_CLASSES)
                self.assertNotIn(cls, config.SESSION_KEEPING_CLASSES)

    def test_a_session_keeping_app_is_also_launched_once(self):
        self.assertLessEqual(config.SESSION_KEEPING_CLASSES, config.SINGLE_INSTANCE_CLASSES)


class GroupCapture(StateDirCase):
    def saved(self, clients):
        self.save_with(clients)
        return self.read_session()

    def test_group_membership_is_recorded(self):
        made = grouped_clients(("code", 1, "a"), ("brave-browser", 2, "a"), ("org.gnome.Mines", 3, None))
        groups = [w["group"] for w in self.saved(made)]
        self.assertEqual(groups[0], groups[1])
        self.assertIsNone(groups[2])

    def test_two_groups_get_distinct_ids(self):
        made = grouped_clients(
            ("code", 1, "a"), ("brave-browser", 2, "a"), ("org.kde.kdenlive", 3, "b"), ("foot", 4, "b")
        )
        groups = [w["group"] for w in self.saved(made)]
        self.assertEqual(groups[0], groups[1])
        self.assertEqual(groups[2], groups[3])
        self.assertNotEqual(groups[0], groups[2])

    def test_a_group_of_one_is_not_a_group(self):
        made = grouped_clients(("code", 1, "a"))
        self.assertIsNone(self.saved(made)[0]["group"])


class MonitorCapture(StateDirCase):
    def save_with_monitors(self, clients, monitors):
        def fake_query(cmd):
            return {"clients": clients, "monitors": monitors}[cmd]

        with (
            mock.patch.object(hypr, "query", side_effect=fake_query),
            mock.patch.object(proc, "read_cmdline", return_value=["/bin/sh"]),
        ):
            session.save_session()
        return self.read_session()[0]

    def test_monitor_name_is_recorded_from_the_id_map(self):
        monitors = [{"id": 0, "name": "eDP-1"}, {"id": 1, "name": "DP-9", "x": 1920, "y": 0}]
        saved = self.save_with_monitors([client("code", monitor=1)], monitors)
        self.assertEqual(saved["monitor_name"], "DP-9")
        self.assertEqual(saved["monitor_at"], [1920, 0])

    def test_unknown_monitor_id_records_an_empty_name(self):
        saved = self.save_with_monitors([client("code", monitor=9)], [{"id": 0, "name": "eDP-1"}])
        self.assertEqual(saved["monitor_name"], "")


class PrivateState(StateDirCase):
    """A snapshot holds every window's command line and title, so the state
    directory and everything in it must be readable by this user alone,
    whatever the umask and whatever an older version left behind."""

    def setUp(self):
        super().setUp()
        self.state = os.path.join(self.dir.name, "state")
        self.use_state_dir(self.state)

    def with_umask(self, mask):
        self.addCleanup(os.umask, os.umask(mask))

    def load_quietly(self):
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            return session.load_session(), err.getvalue()

    def test_the_state_directory_is_created_for_this_user_only(self):
        self.with_umask(0o000)
        self.save_with([client("code")])
        self.assertEqual(mode_of(self.state), 0o700)

    def test_the_snapshot_is_readable_by_this_user_only(self):
        self.with_umask(0o000)
        self.save_with([client("code")])
        self.assertEqual(mode_of(self.session), 0o600)

    def test_a_token_on_a_command_line_is_saved_and_hidden_from_other_users(self):
        """The command line is what relaunches the window, so nothing in it can
        be left out; the modes on the way to it are what keep it private."""
        self.save_with([client("code")], cmdline=("/bin/sh", "--token=hunter2"))
        with open(self.session) as f:
            self.assertIn("hunter2", f.read())
        for path in (self.state, self.session):
            self.assertEqual(mode_of(path) & 0o077, 0, f"{path} is open to other users")

    def test_a_directory_left_open_by_an_older_version_is_closed(self):
        os.mkdir(self.state, 0o755)
        self.save_with([client("code")])
        self.assertEqual(mode_of(self.state), 0o700)

    def test_a_snapshot_left_open_by_an_older_version_is_closed_when_read(self):
        os.mkdir(self.state, 0o700)
        self.write_session([saved_window("code")])
        os.chmod(self.session, 0o644)
        windows, err = self.load_quietly()
        self.assertEqual([w["class"] for w in windows], ["code"])
        self.assertEqual(err, "")
        self.assertEqual(mode_of(self.session), 0o600)

    def test_a_missing_snapshot_is_not_an_error(self):
        self.assertEqual(self.load_quietly(), ([], ""))
        self.assertEqual(mode_of(self.state), 0o700, "the directory is set up on the first read")

    def test_a_symlink_in_place_of_the_state_directory_is_refused(self):
        os.symlink(self.dir.name, self.state)
        with self.assertRaises(PermissionError):
            self.save_with([client("code")])
        self.assertEqual(os.listdir(self.dir.name), ["state"], "nothing was written through the link")

    def test_a_directory_owned_by_another_user_is_refused(self):
        os.mkdir(self.state, 0o700)
        with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(PermissionError):
                self.save_with([client("code")])
            self.assertEqual(self.load_quietly()[0], [])

    def test_a_symlink_in_place_of_the_snapshot_is_neither_read_nor_written_through(self):
        os.mkdir(self.state, 0o700)
        elsewhere = os.path.join(self.dir.name, "elsewhere.json")
        with open(elsewhere, "w") as f:
            json.dump({"windows": [saved_window("code")]}, f)
        os.symlink(elsewhere, self.session)
        windows, err = self.load_quietly()
        self.assertEqual(windows, [])
        self.assertIn("could not read", err)
        self.save_with([client("foot")])
        self.assertFalse(os.path.islink(self.session))
        self.assertEqual(mode_of(self.session), 0o600)
        with open(elsewhere) as f:
            self.assertEqual(json.load(f)["windows"][0]["class"], "code")

    def test_the_restore_copy_is_readable_by_this_user_only(self):
        self.with_umask(0o000)
        self.save_with([client("code")])
        session.keep_restore_copy()
        self.assertEqual(mode_of(self.restored), 0o600)
        self.assertEqual(self.read_session(self.restored), self.read_session())

    def test_a_failed_write_leaves_no_temporary_file_behind(self):
        with self.assertRaises(TypeError):
            session.write_private(self.session, b"not text")
        self.assertEqual(os.listdir(self.state), [])


class KittySessions(StateDirCase):
    """A kitty that describes itself is relaunched from a session file holding
    its whole instance: every OS window, tab, split and working directory. The
    one launch brings all of them back, so the others are not launched again."""

    TEXT = "new_tab\nlayout splits\nlaunch --cwd=/srv\n"

    def save_kitty(self, clients, text=TEXT):
        with mock.patch.object(kitty, "build_session_text", return_value=text):
            self.save_with(clients)
        return self.read_session()

    def path_for(self, pid):
        return os.path.join(self.dir.name, f"kitty-{pid}.session")

    def test_the_session_file_is_written_and_named_in_the_command(self):
        saved = self.save_kitty([client("kitty", pid=7)])
        self.assertEqual(saved[0]["cmd"], f"kitty --session {self.path_for(7)}")
        with open(self.path_for(7)) as f:
            self.assertEqual(f.read(), self.TEXT)

    def test_the_session_file_is_readable_by_this_user_only(self):
        """It holds every directory the user had open, as the snapshot does."""
        self.addCleanup(os.umask, os.umask(0o000))
        self.save_kitty([client("kitty", pid=7)])
        self.assertEqual(mode_of(self.path_for(7)), 0o600)

    def test_a_second_window_of_one_kitty_is_not_launched_again(self):
        two = [client("kitty", pid=7), client("kitty", pid=7)]
        self.assertEqual([w["spawn"] for w in self.save_kitty(two)], [True, False])

    def test_each_kitty_instance_is_launched_on_its_own(self):
        two = [client("kitty", pid=7), client("kitty", pid=8)]
        saved = self.save_kitty(two)
        self.assertEqual([w["spawn"] for w in saved], [True, True])
        self.assertEqual(
            [w["cmd"] for w in saved],
            [f"kitty --session {self.path_for(7)}", f"kitty --session {self.path_for(8)}"],
        )

    def test_a_kitty_that_does_not_answer_keeps_a_launch_per_window(self):
        """Remote control is off: there is no session file, so a second window
        still has to be opened by a second launch."""
        two = [client("kitty", pid=7), client("kitty", pid=7)]
        with (
            mock.patch.object(proc, "find_descendant", return_value=None),
            mock.patch.object(proc, "read_cwd", return_value="/srv"),
        ):
            saved = self.save_kitty(two, text=None)
        self.assertEqual([w["spawn"] for w in saved], [True, True])
        self.assertEqual(saved[0]["cmd"], "kitty -d /srv")
        self.assertEqual(os.listdir(self.dir.name), ["session.json"])

    def test_files_of_instances_that_are_gone_are_dropped(self):
        """One file per kitty running now, however many logins came before."""
        stale = self.path_for(999)
        with open(stale, "w") as f:
            f.write("old\n")
        self.save_kitty([client("kitty", pid=7)])
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(self.path_for(7)))

    def test_a_kitty_taken_out_of_the_terminals_table_gets_no_session_file(self):
        """Without the table entry there is no binary to replay the file with,
        so each of its windows is relaunched from its command line instead."""
        with (
            pretend_runnable(),
            mock.patch.object(config, "TERMINALS", {}),
            mock.patch.object(kitty, "build_session_text") as read,
        ):
            self.save_with([client("kitty", pid=7)], cmdline=("/usr/bin/kitty",))
        read.assert_not_called()
        self.assertEqual([w["cmd"] for w in self.read_session()], ["/usr/bin/kitty"])

    def test_another_terminal_is_left_alone(self):
        with mock.patch.object(kitty, "build_session_text") as read:
            self.save_with([client("com.mitchellh.ghostty", pid=7)])
        read.assert_not_called()


class SaveSchedule(unittest.TestCase):
    """The daemon polls the desktop; this decides which polls write."""

    A = {"0xa": {"class": "code", "workspace": {"id": 1}}}
    AB = {"0xa": {"class": "code", "workspace": {"id": 1}}, "0xb": {"class": "foot", "workspace": {"id": 2}}}

    def setUp(self):
        self.scheduler = session.SaveScheduler()
        for name, value in (("SETTLE_DELAY", 10), ("SAVE_INTERVAL", 60)):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def observe(self, layout, now):
        due = self.scheduler.is_due(layout, now)
        if due:
            self.scheduler.mark_saved(now)
        return due

    def test_the_first_look_is_saved(self):
        self.assertTrue(self.observe(self.A, 0))

    def test_nothing_changed_is_not_saved(self):
        self.observe(self.A, 0)
        self.assertFalse(self.observe(self.A, 5))

    def test_a_new_window_is_saved_at_once(self):
        self.observe(self.A, 0)
        self.assertTrue(self.observe(self.AB, 5))

    def test_a_moved_window_is_saved_at_once(self):
        self.observe(self.A, 0)
        self.assertTrue(self.observe({"0xa": {"class": "code", "workspace": {"id": 3}}}, 5))

    def test_vanished_windows_wait_until_the_desktop_has_been_still(self):
        """The power menu closes every window two seconds before the poweroff;
        a poll landing in that gap must not write the half-closed desktop."""
        self.observe(self.AB, 0)
        self.assertFalse(self.observe(self.A, 5))
        self.assertFalse(self.observe(self.A, 10))
        self.assertTrue(self.observe(self.A, 15))

    def test_a_desktop_closed_down_entirely_is_saved_once_it_is_still(self):
        self.observe(self.AB, 0)
        self.assertFalse(self.observe({}, 5))
        self.assertTrue(self.observe({}, 16))

    def test_the_wait_restarts_when_the_desktop_changes_again(self):
        self.observe(self.AB, 0)
        self.assertFalse(self.observe(self.A, 5))
        self.assertFalse(self.observe({}, 12))
        self.assertFalse(self.observe({}, 20))
        self.assertTrue(self.observe({}, 23))

    def test_a_periodic_save_catches_what_the_layout_does_not_show(self):
        """Titles and floating geometry change without a window coming or going."""
        self.observe(self.A, 0)
        self.assertFalse(self.observe(self.A, 55))
        self.assertTrue(self.observe(self.A, 60))

    def test_a_failed_save_is_tried_again_on_the_next_look(self):
        self.assertTrue(self.scheduler.is_due(self.A, 0))
        self.assertTrue(self.scheduler.is_due(self.A, 5))

    def test_an_unchanged_desktop_sleeps_until_the_periodic_save(self):
        self.observe(self.A, 0)
        self.assertEqual(self.scheduler.seconds_until_recheck(0), 60)
        self.scheduler.is_due(self.A, 20)
        self.assertEqual(self.scheduler.seconds_until_recheck(20), 40)

    def test_vanished_windows_shorten_the_sleep_to_their_settle(self):
        """The daemon has to come back for them; no event will say they are
        still gone."""
        self.observe(self.AB, 0)
        self.assertFalse(self.observe(self.A, 5))
        self.assertEqual(self.scheduler.seconds_until_recheck(5), 10)
        self.assertEqual(self.scheduler.seconds_until_recheck(12), 3)

    def test_the_sleep_never_goes_negative(self):
        self.observe(self.A, 0)
        self.assertEqual(self.scheduler.seconds_until_recheck(999), 0)


class QuitSessionKeepingApps(unittest.TestCase):
    """Chromium only keeps its tabs if it exits cleanly, so shutdown has to
    signal it and wait rather than let the compositor kill it."""

    def quit(self, clients, timeout, alive, kill=None):
        """Returns (pids still alive at the end, pids signalled)."""
        with (
            mock.patch.object(hypr, "query", return_value=clients),
            mock.patch.object(os, "kill", side_effect=kill) as killed,
            mock.patch.object(os.path, "exists", return_value=alive),
            mock.patch.object(time, "sleep"),
            mock.patch.object(time, "time", side_effect=[0, 1, 2]),
        ):
            stubborn = session.quit_session_keeping_apps(timeout=timeout)
        return stubborn, sorted(c.args[0] for c in killed.call_args_list)

    def test_signals_only_session_keeping_apps(self):
        clients = [
            client("brave-browser", pid=11),
            client("com.mitchellh.ghostty", pid=22),
            client("org.gnome.Nautilus", pid=33),
            client("code", pid=44),
        ]
        _, signalled = self.quit(clients, timeout=0, alive=False)
        self.assertEqual(
            signalled,
            [11, 44],
            "Code has to be asked to quit too, or the folder opened last is never written",
        )

    def test_reports_apps_that_outlive_the_timeout(self):
        stubborn, _ = self.quit([client("brave-browser", pid=11)], timeout=1, alive=True)
        self.assertEqual(stubborn, {11})

    def test_reports_nothing_when_everything_exits(self):
        stubborn, _ = self.quit([client("brave-browser", pid=11)], timeout=5, alive=False)
        self.assertEqual(stubborn, set())

    def test_a_process_that_is_already_gone_is_not_reported(self):
        stubborn, _ = self.quit(
            [client("brave-browser", pid=11)], timeout=5, alive=True, kill=ProcessLookupError
        )
        self.assertEqual(stubborn, set())
