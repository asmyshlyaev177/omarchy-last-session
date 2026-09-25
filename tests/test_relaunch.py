"""Command recovery. Every case here is a window that failed to come back on a
real machine."""

import os
import tempfile
import unittest
from unittest import mock

from omarchy_last_session import config, proc, relaunch
from tests.helpers import BRAVE_BLOB, client, pretend_runnable, write_executable


class UnflattenArgv(unittest.TestCase):
    def test_splits_chromium_blob(self):
        with pretend_runnable():
            argv = relaunch.unflatten_argv([BRAVE_BLOB])
        self.assertEqual(argv[0], "/opt/brave-bin/brave")
        self.assertIn("--ozone-platform=wayland", argv)

    def test_leaves_normal_argv_alone(self):
        argv = ["/usr/bin/nautilus", "--new-window"]
        self.assertEqual(relaunch.unflatten_argv(argv), argv)

    def test_leaves_unrunnable_path_with_spaces_alone(self):
        """A real path containing spaces must not be split into pieces."""
        argv = ["/home/alex/My Apps/thing"]
        self.assertEqual(relaunch.unflatten_argv(argv), argv)

    def test_handles_empty(self):
        self.assertEqual(relaunch.unflatten_argv([]), [])


class TerminalArgv(unittest.TestCase):
    def test_joins_flag_ending_in_equals(self):
        """ghostty parses only --key=value; a separated argument is ignored."""
        self.assertEqual(
            relaunch.build_terminal_argv("ghostty", "--working-directory=", "/tmp"),
            ["ghostty", "--working-directory=/tmp"],
        )

    def test_keeps_separated_flag_separate(self):
        self.assertEqual(relaunch.build_terminal_argv("kitty", "-d", "/tmp"), ["kitty", "-d", "/tmp"])


class IsReplayable(unittest.TestCase):
    def setUp(self):
        """The mount path must be a real executable, or the test would pass
        merely because the file is missing rather than because of the rule."""
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        prefix = os.path.join(self.dir.name, ".mount_")
        patcher = mock.patch.object(config, "APPIMAGE_MOUNT_PREFIX", prefix)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mounted = os.path.join(prefix + "JoplinbhOjmd", "joplin")
        self.elsewhere = os.path.join(self.dir.name, "joplin")
        write_executable(self.mounted)
        write_executable(self.elsewhere)

    def test_rejects_appimage_mount_path(self):
        self.assertTrue(os.access(self.mounted, os.X_OK))
        self.assertFalse(relaunch.is_replayable(self.mounted))

    def test_accepts_the_same_binary_outside_the_mount(self):
        self.assertTrue(relaunch.is_replayable(self.elsewhere))

    def test_rejects_dbus_service_activation(self):
        self.assertFalse(relaunch.is_replayable("/usr/bin/gnome-mines --gapplication-service"))

    def test_rejects_missing_binary(self):
        self.assertFalse(relaunch.is_replayable("/nonexistent/binary --flag"))

    def test_accepts_real_binary(self):
        self.assertTrue(relaunch.is_replayable("/bin/sh -c true"))

    def test_rejects_unparseable(self):
        self.assertFalse(relaunch.is_replayable('unbalanced "quote'))


class DesktopDirCase(unittest.TestCase):
    """A temporary applications directory in place of the real ones."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(config, "DESKTOP_DIRS", (self.dir.name,))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_desktop(self, name, body):
        with open(os.path.join(self.dir.name, name), "w") as f:
            f.write(body)


class DesktopLookup(DesktopDirCase):
    def test_matches_startupwmclass_through_appimage_prefix(self):
        self.write_desktop(
            "Joplin.desktop",
            "[Desktop Entry]\nExec=/home/alex/Applications/Joplin.AppImage %u\nStartupWMClass=Joplin\n",
        )
        self.assertEqual(
            relaunch.find_desktop_command("appimagekit-joplin"), "/home/alex/Applications/Joplin.AppImage"
        )

    def test_matches_on_filename(self):
        self.write_desktop("org.gnome.Mines.desktop", "[Desktop Entry]\nExec=gnome-mines\n")
        self.assertEqual(relaunch.find_desktop_command("org.gnome.Mines"), "gnome-mines")

    def test_ignores_keys_outside_desktop_entry_section(self):
        self.write_desktop(
            "thing.desktop", "[Desktop Entry]\nExec=real-binary\n[Desktop Action new]\nExec=wrong-binary\n"
        )
        self.assertEqual(relaunch.find_desktop_command("thing"), "real-binary")

    def test_returns_none_when_nothing_matches(self):
        self.write_desktop("other.desktop", "[Desktop Entry]\nExec=other\n")
        self.assertIsNone(relaunch.find_desktop_command("nosuchclass"))

    def test_falls_back_to_the_program_exec_runs(self):
        self.write_desktop("gimp.desktop", "[Desktop Entry]\nExec=gimp-2.10 %U\n")
        self.assertEqual(relaunch.find_desktop_command("gimp-2.10"), "gimp-2.10")

    def test_an_entry_named_after_the_class_beats_a_shortcut_that_runs_it(self):
        """Steam writes a shortcut per game into the user's applications
        directory, which is searched first, and every one of them runs steam
        with the game's URL. Restoring through it would start the game."""
        self.write_desktop(
            "Warhammer 40,000 Boltgun.desktop", "[Desktop Entry]\nExec=steam steam://rungameid/2005010\n"
        )
        self.write_desktop("steam.desktop", "[Desktop Entry]\nExec=/usr/bin/steam %U\n")
        self.assertEqual(relaunch.find_desktop_command("steam"), "/usr/bin/steam")


class RelaunchCommand(DesktopDirCase):
    """The end-to-end save path, with /proc and the desktop dirs faked."""

    def test_steam_relaunches_its_client_not_the_game_it_was_started_for(self):
        """The window belongs to steamwebhelper, whose relative path cannot be
        replayed, so the command comes from a .desktop entry."""
        self.write_desktop(
            "Warhammer 40,000 Boltgun.desktop", "[Desktop Entry]\nExec=steam steam://rungameid/2005010\n"
        )
        self.write_desktop("steam.desktop", "[Desktop Entry]\nExec=/usr/bin/steam %U\n")
        with mock.patch.object(proc, "read_cmdline", return_value=["./steamwebhelper", "-nocrashdialog"]):
            self.assertEqual(relaunch.build_relaunch_command(client("steam")), "/usr/bin/steam")

    def test_libreoffice_drops_the_splash_descriptor_it_was_handed(self):
        """--splash-pipe names a descriptor of the run being saved."""
        argv = ["/usr/lib/libreoffice/program/soffice.bin", "--writer", "/srv/notes.odt", "--splash-pipe=5"]
        with mock.patch.object(proc, "read_cmdline", return_value=argv), pretend_runnable():
            cmd = relaunch.build_relaunch_command(client("libreoffice-writer"))
        self.assertEqual(cmd, "/usr/lib/libreoffice/program/soffice.bin --writer /srv/notes.odt")

    def test_kitty_with_a_session_replays_the_whole_instance(self):
        """Its tabs and splits are in the file, so the relaunch points at it
        instead of opening one bare window in a directory."""
        cmd = relaunch.build_relaunch_command(client("kitty"), "/state/kitty-7.session")
        self.assertEqual(cmd, "kitty --session /state/kitty-7.session")

    def test_a_session_path_with_a_space_is_quoted(self):
        cmd = relaunch.build_relaunch_command(client("kitty"), "/my state/kitty-7.session")
        self.assertEqual(cmd, "kitty --session '/my state/kitty-7.session'")

    def test_kitty_without_a_session_reopens_in_its_directory(self):
        """Remote control is off, so there is nothing to replay and the
        terminal comes back the way every other terminal does."""
        with (
            mock.patch.object(proc, "find_descendant", return_value=None),
            mock.patch.object(proc, "read_cwd", return_value="/srv"),
        ):
            self.assertEqual(relaunch.build_relaunch_command(client("kitty")), "kitty -d /srv")

    def test_brave_is_unflattened_and_gets_restore_flag(self):
        with pretend_runnable(), mock.patch.object(proc, "read_cmdline", return_value=[BRAVE_BLOB]):
            cmd = relaunch.build_relaunch_command(client("brave-browser"))
        self.assertTrue(cmd.startswith("/opt/brave-bin/brave "))
        self.assertIn("--ozone-platform=wayland", cmd)
        self.assertTrue(cmd.endswith("--restore-last-session"))

    def test_appimage_falls_back_to_desktop_entry(self):
        """The running binary exists, so only the mount prefix can reject it."""
        prefix = os.path.join(self.dir.name, ".mount_")
        running = os.path.join(prefix + "JoplinXXXXXX", "joplin")
        write_executable(running)
        self.write_desktop(
            "Joplin.desktop",
            "[Desktop Entry]\nExec=/home/alex/Applications/Joplin.AppImage %u\nStartupWMClass=Joplin\n",
        )
        with (
            mock.patch.object(config, "APPIMAGE_MOUNT_PREFIX", prefix),
            mock.patch.object(proc, "read_cmdline", return_value=[running]),
        ):
            cmd = relaunch.build_relaunch_command(client("appimagekit-joplin"))
        self.assertEqual(cmd, "/home/alex/Applications/Joplin.AppImage")

    def test_dbus_activation_falls_back_to_desktop_entry(self):
        self.write_desktop("org.gnome.Mines.desktop", "[Desktop Entry]\nExec=gnome-mines\n")
        with mock.patch.object(
            proc, "read_cmdline", return_value=["/usr/bin/gnome-mines", "--gapplication-service"]
        ):
            cmd = relaunch.build_relaunch_command(client("org.gnome.Mines"))
        self.assertEqual(cmd, "gnome-mines")

    def test_unrecoverable_window_is_dropped(self):
        with mock.patch.object(proc, "read_cmdline", return_value=None):
            self.assertIsNone(relaunch.build_relaunch_command(client("mystery")))

    def test_terminal_reopens_in_shell_cwd(self):
        with (
            mock.patch.object(proc, "find_descendant", side_effect=[None, 99]),
            mock.patch.object(proc, "read_cwd", return_value="/home/alex/src"),
        ):
            cmd = relaunch.build_relaunch_command(client("com.mitchellh.ghostty"))
        self.assertEqual(cmd, "ghostty --working-directory=/home/alex/src")

    def test_terminal_relaunches_its_tui(self):
        with (
            mock.patch.object(proc, "find_descendant", return_value=77),
            mock.patch.object(proc, "read_cmdline", return_value=["nvim"]),
            mock.patch.object(proc, "read_cwd", return_value="/etc"),
        ):
            cmd = relaunch.build_relaunch_command(client("com.mitchellh.ghostty"))
        self.assertEqual(cmd, "ghostty --working-directory=/etc -e nvim")

    def test_a_tui_that_exited_mid_save_falls_back_to_the_shell(self):
        """The process tree is read in several steps. A TUI that exits between
        them leaves no cmdline and no comm to read, and used to crash the save."""
        with (
            mock.patch.object(proc, "find_descendant", return_value=77),
            mock.patch.object(proc, "read_cmdline", return_value=None),
            mock.patch.object(proc, "read_comm", return_value=None),
            mock.patch.object(proc, "read_cwd", return_value="/home/alex/src"),
        ):
            cmd = relaunch.build_relaunch_command(client("com.mitchellh.ghostty"))
        self.assertEqual(cmd, "ghostty --working-directory=/home/alex/src")


class AddRestoreFlag(unittest.TestCase):
    def test_not_added_twice(self):
        once = relaunch.add_restore_flag("brave", "brave-browser")
        self.assertEqual(relaunch.add_restore_flag(once, "brave-browser"), once)

    def test_left_alone_for_other_apps(self):
        self.assertEqual(relaunch.add_restore_flag("nautilus", "org.gnome.Nautilus"), "nautilus")


class ChromiumClasses(unittest.TestCase):
    """XWayland reports Chromium; native Wayland chromium or chromium-browser.
    All of them keep their tabs only if quit cleanly and relaunched with the
    restore flag."""

    def test_every_chromium_class_is_session_keeping(self):
        for cls in ("Chromium", "chromium", "chromium-browser"):
            with self.subTest(cls=cls):
                cmd = relaunch.add_restore_flag("/usr/lib/chromium/chromium", cls)
                self.assertTrue(cmd.endswith("--restore-last-session"))
                self.assertIn(cls, config.SESSION_KEEPING_CLASSES)


class ChromiumWebApps(unittest.TestCase):
    """Chromium runs every window, web apps included, in one process, whose
    command line is that of the launch that started it. When a web app started
    it, the browser was saved as that web app and came back as a second copy
    of it, with none of its own windows (2026-09-25, WhatsApp in Chrome)."""

    CHROME = "/opt/google/chrome/chrome"
    WHATSAPP = "chrome-web.whatsapp.com__-Default"

    def relaunch(self, cls, argv):
        with pretend_runnable(), mock.patch.object(proc, "read_cmdline", return_value=list(argv)):
            return relaunch.build_relaunch_command(client(cls, pid=7))

    def test_a_browser_started_by_a_web_app_is_relaunched_as_the_browser(self):
        cmd = self.relaunch("google-chrome", [self.CHROME, "--app=https://web.whatsapp.com/"])
        self.assertEqual(cmd, f"{self.CHROME} --restore-last-session")

    def test_a_web_app_keeps_the_url_it_was_started_with(self):
        cmd = self.relaunch(self.WHATSAPP, [self.CHROME, "--app=https://web.whatsapp.com/"])
        self.assertEqual(cmd, f"{self.CHROME} --app=https://web.whatsapp.com/")

    def test_a_web_app_in_a_browser_started_normally_gets_its_url_from_its_class(self):
        cmd = self.relaunch(self.WHATSAPP, [self.CHROME])
        self.assertEqual(cmd, f"{self.CHROME} --app=https://web.whatsapp.com/")

    def test_a_web_app_does_not_take_another_web_apps_url(self):
        cmd = self.relaunch(
            "chrome-app.example.com__inbox-Default", [self.CHROME, "--app=https://web.whatsapp.com/"]
        )
        self.assertEqual(cmd, f"{self.CHROME} --app=https://app.example.com/inbox")

    def test_a_browser_started_by_an_installed_web_app_drops_its_app_id(self):
        cmd = self.relaunch(
            "brave-browser", ["/opt/brave-bin/brave", "--app-id=abcdefghijklmnopabcdefghijklmnop"]
        )
        self.assertEqual(cmd, "/opt/brave-bin/brave --restore-last-session")

    def test_an_app_that_is_not_a_browser_keeps_its_app_flag(self):
        self.assertEqual(
            self.relaunch("someapp", ["/usr/bin/someapp", "--app=x"]), "/usr/bin/someapp --app=x"
        )


class InstalledWebApps(unittest.TestCase):
    """An app installed with the browser's Install button opens with --app-id, and its
    window runs in the browser's process too. Saved with the browser's command line, Brave's
    WhatsApp Web came back as a blank browser window (2026-09-25)."""

    BRAVE = "/opt/brave-bin/brave"
    WHATSAPP = "brave-hnpfjngllnobngcgfapefoaidbinmjnm-Default"

    def relaunch(self, argv):
        with pretend_runnable(), mock.patch.object(proc, "read_cmdline", return_value=list(argv)):
            return relaunch.build_relaunch_command(client(self.WHATSAPP, pid=7))

    def test_an_installed_web_app_is_relaunched_by_its_app_id(self):
        self.assertEqual(
            self.relaunch([self.BRAVE, "--restore-last-session"]),
            f"{self.BRAVE} --restore-last-session --app-id=hnpfjngllnobngcgfapefoaidbinmjnm",
        )

    def test_an_installed_web_app_does_not_take_the_flag_of_the_app_that_started_the_browser(self):
        self.assertEqual(
            self.relaunch([self.BRAVE, "--app=https://web.whatsapp.com/"]),
            f"{self.BRAVE} --app-id=hnpfjngllnobngcgfapefoaidbinmjnm",
        )
