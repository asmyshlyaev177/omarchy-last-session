import io
import os
import sys
import unittest
from unittest import mock

from omarchy_last_session import config, hypr, session
from tests.helpers import ConfigFileCase


class NoFile(ConfigFileCase):
    def test_every_knob_is_at_its_default(self):
        self.assertEqual(config.load_file(), config.DEFAULTS)
        self.assertEqual(config.STATE_DIR, config.DEFAULT_STATE_DIR)
        self.assertEqual(config.EXCLUDE_CLASSES, config.BUILTIN_EXCLUDE_CLASSES)
        self.assertEqual((config.SETTLE_DELAY, config.SAVE_INTERVAL), (10, 60))
        self.assertEqual(config.TERMINALS["com.mitchellh.ghostty"], ("ghostty", "--working-directory=", "-e"))
        self.assertEqual(config.CHROMIUM_BROWSERS["brave-browser"], "BraveSoftware/Brave-Browser")


class Template(ConfigFileCase):
    """What the plugin writes on its first run: every default, so the file
    says what is in force and what to change."""

    def test_reads_back_as_no_overrides(self):
        self.assertEqual(self.reload_with(config.render_template()), "")
        self.assertEqual(config.load_file(), config.DEFAULTS)

    def test_every_setting_and_table_is_named_and_explained(self):
        template = config.render_template()
        for key, (_, doc) in config.SETTINGS.items():
            self.assertIn(f"\n{key} =", template)
            self.assertIn("# " + doc.split("\n")[0], template)
        for name, (rows, doc, _) in config.TABLES.items():
            self.assertIn(f"\n[{name}]\n# {doc.split(chr(10))[0]}", template)
            for key in rows:
                self.assertIn(f"\n{key} = ", template)

    def test_the_app_tables_ship_with_their_rows(self):
        template = config.render_template()
        self.assertIn("\nAlacritty = alacritty, --working-directory, -e\n", template)
        self.assertIn("\nkitty = kitty, -d\n", template)
        self.assertIn("\nbrave-browser = BraveSoftware/Brave-Browser\n", template)
        self.assertIn("\nsession_keeping = code, code-oss, code-insiders, codium\n", template)
        self.assertIn("gimp\n", template)
        self.assertIn("Steam", template)

    def test_write_defaults_leaves_an_existing_file_alone(self):
        self.write_config({"exclude": ["mine"]})
        with self.assertRaises(FileExistsError):
            config.write_defaults()
        self.assertEqual(config.load_file()["exclude"], ["mine"])

    def test_ensure_file_writes_it_once(self):
        config.ensure_file()
        with open(config.CONFIG_FILE) as f:
            self.assertEqual(f.read(), config.render_template())
        self.write_config({"exclude": ["mine"]})
        config.ensure_file()
        self.assertEqual(config.load_file()["exclude"], ["mine"])


class Values(ConfigFileCase):
    def test_exclude_adds_to_the_shells_own(self):
        self.assertEqual(self.reload_with({"exclude": ["jobbot-search"]}), "")
        self.assertEqual(config.EXCLUDE_CLASSES, config.BUILTIN_EXCLUDE_CLASSES | {"jobbot-search"})

    def test_a_list_in_the_file_replaces_the_default_list(self):
        self.reload_with({"tui_programs": ["lazygit"], "shells": ["elvish"]})
        self.assertEqual(config.TUI_PROGRAMS, {"lazygit"})
        self.assertEqual(config.SHELLS, {"elvish"})

    def test_a_list_may_be_comma_separated_or_one_per_line(self):
        self.reload_with("[general]\nexclude = a, b ,c\ntui_programs =\n    d\n    e\n")
        self.assertTrue({"a", "b", "c"} <= config.EXCLUDE_CLASSES)
        self.assertEqual(config.TUI_PROGRAMS, {"d", "e"})

    def test_session_keeping_apps_are_the_browsers_plus_the_list(self):
        self.reload_with({"session_keeping": ["dev.zed.Zed"]})
        self.assertEqual(
            config.SESSION_KEEPING_CLASSES, frozenset(config.CHROMIUM_BROWSERS) | {"dev.zed.Zed"}
        )
        self.assertNotIn("code", config.SESSION_KEEPING_CLASSES)

    def test_single_instance_apps_include_every_session_keeping_one(self):
        self.reload_with({"session_keeping": ["dev.zed.Zed"], "single_instance": ["gimp"]})
        self.assertLessEqual(config.SESSION_KEEPING_CLASSES, config.SINGLE_INSTANCE_CLASSES)
        self.assertIn("gimp", config.SINGLE_INSTANCE_CLASSES)
        self.assertNotIn("soffice", config.SINGLE_INSTANCE_CLASSES)

    def test_state_dir_moves_every_state_file(self):
        self.reload_with({"state_dir": "~/elsewhere"})
        self.assertEqual(config.STATE_DIR, os.path.expanduser("~/elsewhere"))
        for path in (
            config.SESSION_FILE,
            config.DISABLE_FLAG,
            config.LAST_SHUTDOWN_FILE,
            config.LAST_RESTORE_FILE,
            config.KITTY_SESSION_FILE,
            config.KITTY_SESSION_GLOB,
        ):
            self.assertEqual(os.path.dirname(path), config.STATE_DIR, path)

    def test_an_empty_state_dir_means_the_default(self):
        self.reload_with({"state_dir": ""})
        self.assertEqual(config.SESSION_FILE, os.path.join(config.DEFAULT_STATE_DIR, "session.json"))

    def test_timings_come_from_the_file(self):
        timings = {
            "settle_delay": "2.5",
            "save_interval": "20",
            "sweep_timeout": "45.0",
            "title_settle": "1",
            "max_preexisting_windows": "9",
        }
        self.assertEqual(self.reload_with(timings), "")
        values = (
            config.SETTLE_DELAY,
            config.SAVE_INTERVAL,
            config.SWEEP_TIMEOUT,
            config.TITLE_SETTLE,
            config.MAX_PREEXISTING_WINDOWS,
        )
        self.assertEqual(values, (2.5, 20, 45, 1, 9))
        self.assertEqual([type(v) for v in values], [float, int, int, int, int])


class Tables(ConfigFileCase):
    """[terminals] and [chromium-browsers]: a section in the file is the whole table."""

    def test_a_terminals_section_replaces_the_table(self):
        self.assertEqual(self.reload_with({"terminals": {"org.wezfurlong.wezterm": "wezterm, --cwd"}}), "")
        self.assertEqual(config.TERMINALS, {"org.wezfurlong.wezterm": ("wezterm", "--cwd", None)})

    def test_a_terminal_may_name_its_exec_flag(self):
        self.reload_with({"terminals": {"Alacritty": "alacritty, --working-directory, -e"}})
        self.assertEqual(config.TERMINALS["Alacritty"], ("alacritty", "--working-directory", "-e"))

    def test_class_case_is_kept(self):
        self.reload_with({"terminals": {"Alacritty": "alacritty, --working-directory"}})
        self.assertIn("Alacritty", config.TERMINALS)
        self.assertNotIn("alacritty", config.TERMINALS)

    def test_a_browser_row_sets_the_profile_dir_flag_and_session_keeping(self):
        self.reload_with({"chromium-browsers": {"vivaldi-stable": "vivaldi"}})
        self.assertEqual(config.CHROMIUM_BROWSERS, {"vivaldi-stable": "vivaldi"})
        self.assertEqual(config.RESTORE_FLAGS, {"vivaldi-stable": config.RESTORE_FLAG})
        self.assertIn("vivaldi-stable", config.SESSION_KEEPING_CLASSES)
        self.assertNotIn("brave-browser", config.SESSION_KEEPING_CLASSES)

    def test_an_empty_section_is_an_empty_table(self):
        self.reload_with("[chromium-browsers]\n")
        self.assertEqual(config.CHROMIUM_BROWSERS, {})
        self.assertEqual(config.TERMINALS, config.DEFAULTS["terminals"])

    def test_a_row_that_cannot_be_used_is_reported_and_skipped(self):
        warned = self.reload_with(
            {"terminals": {"foot": "foot", "kitty": "kitty, -d"}, "chromium-browsers": {"x": ""}}
        )
        self.assertIn("[terminals] foot = 'foot' is not a usable entry", warned)
        self.assertIn("[chromium-browsers] x = '' is not a usable entry", warned)
        self.assertEqual(config.TERMINALS, {"kitty": ("kitty", "-d", None)})
        self.assertEqual(config.CHROMIUM_BROWSERS, {})


class BadInput(ConfigFileCase):
    """Nothing in the file can stop the plugin: what it cannot use it reports
    in the journal and leaves at the default."""

    def test_an_unknown_key_is_reported_and_ignored(self):
        warned = self.reload_with({"exclud": ["foot"]})
        self.assertIn("no setting named 'exclud'", warned)
        self.assertEqual(config.EXCLUDE_CLASSES, config.BUILTIN_EXCLUDE_CLASSES)

    def test_an_unknown_section_is_reported_and_ignored(self):
        warned = self.reload_with("[general]\nsave_interval = 7\n[other]\nsave_interval = 9\n")
        self.assertIn("no section named [other]", warned)
        self.assertEqual(config.SAVE_INTERVAL, 7)

    def test_a_number_that_is_not_one_keeps_the_default(self):
        wrong = {"save_interval": "soon", "settle_delay": "-1", "sweep_timeout": "nan", "title_settle": "inf"}
        warned = self.reload_with(wrong)
        for key in wrong:
            self.assertIn(f"{key} must be a number from", warned)
        self.assertEqual(
            (config.SAVE_INTERVAL, config.SETTLE_DELAY, config.SWEEP_TIMEOUT, config.TITLE_SETTLE),
            (60, 10, 30, 5),
        )

    def test_a_good_key_beside_a_bad_one_still_applies(self):
        self.reload_with({"exclude": ["mine"], "save_interval": "soon"})
        self.assertIn("mine", config.EXCLUDE_CLASSES)
        self.assertEqual(config.SAVE_INTERVAL, 60)

    def test_a_file_without_a_section_header_is_reported_and_ignored(self):
        warned = self.reload_with("save_interval = 7\n")
        self.assertIn(f"ignoring {config.CONFIG_FILE}", warned)
        self.assertEqual(config.SAVE_INTERVAL, 60)

    def test_a_key_repeated_is_reported_and_the_file_ignored(self):
        warned = self.reload_with("[general]\nsave_interval = 7\nsave_interval = 9\n")
        self.assertIn(f"ignoring {config.CONFIG_FILE}", warned)
        self.assertEqual(config.SAVE_INTERVAL, 60)


class FollowingEdits(ConfigFileCase):
    """The daemon runs for the whole session, so it asks each time it wakes."""

    def test_nothing_to_reload_until_the_file_appears(self):
        self.assertFalse(config.reload_if_changed())
        self.write_config({"save_interval": "7"})
        self.assertTrue(config.reload_if_changed())
        self.assertEqual(config.SAVE_INTERVAL, 7)
        self.assertFalse(config.reload_if_changed())

    def test_an_edit_is_picked_up(self):
        self.reload_with({"save_interval": "7"})
        self.write_config({"save_interval": "15", "exclude": ["mine"]})
        self.assertTrue(config.reload_if_changed())
        self.assertEqual(config.SAVE_INTERVAL, 15)
        self.assertIn("mine", config.EXCLUDE_CLASSES)

    def test_deleting_the_file_restores_the_defaults(self):
        self.reload_with({"save_interval": "7"})
        os.unlink(config.CONFIG_FILE)
        self.assertTrue(config.reload_if_changed())
        self.assertEqual(config.SAVE_INTERVAL, 60)


class WhatUsersType(ConfigFileCase):
    """Forms INI does not need but people write, taken as meant."""

    def test_an_empty_file_or_only_comments_means_the_defaults(self):
        for text in ("", "# nothing here\n\n", "\n\n"):
            with self.subTest(text=text):
                self.assertEqual(self.reload_with(text), "")
                self.assertEqual(config.SAVE_INTERVAL, 60)

    def test_a_comment_after_a_value_is_not_part_of_it(self):
        self.reload_with("[general]\nexclude = a, b  # my autostarted apps\nsave_interval = 7 ; quick\n")
        self.assertEqual(config.EXCLUDE_CLASSES, config.BUILTIN_EXCLUDE_CLASSES | {"a", "b"})
        self.assertEqual(config.SAVE_INTERVAL, 7)

    def test_quotes_around_a_value_are_dropped(self):
        self.reload_with(
            '[general]\nexclude = "My App", \'other\'\nstate_dir = "~/state dir"\n'
            "[terminals]\nfoot = \"foot\", '-D'\n"
            "[chromium-browsers]\nbrave-browser = 'BraveSoftware/Brave-Browser'\n"
        )
        self.assertTrue({"My App", "other"} <= config.EXCLUDE_CLASSES)
        self.assertEqual(config.STATE_DIR, os.path.expanduser("~/state dir"))
        self.assertEqual(config.TERMINALS, {"foot": ("foot", "-D", None)})
        self.assertEqual(config.CHROMIUM_BROWSERS, {"brave-browser": "BraveSoftware/Brave-Browser"})

    def test_spaces_and_percent_signs_inside_an_item_survive(self):
        self.reload_with({"exclude": ["Microsoft Teams", "100%app"]})
        self.assertTrue({"Microsoft Teams", "100%app"} <= config.EXCLUDE_CLASSES)

    def test_section_and_general_keys_match_regardless_of_case(self):
        self.reload_with("[General]\nSave_Interval = 7\n[TERMINALS]\nfoot = foot, -D\n")
        self.assertEqual(config.SAVE_INTERVAL, 7)
        self.assertEqual(config.TERMINALS, {"foot": ("foot", "-D", None)})

    def test_windows_line_endings_and_a_byte_order_mark_are_fine(self):
        self.reload_with("﻿[general]\r\nexclude = a, b\r\nsave_interval = 7\r\n")
        self.assertTrue({"a", "b"} <= config.EXCLUDE_CLASSES)
        self.assertEqual(config.SAVE_INTERVAL, 7)

    def test_a_trailing_comma_on_a_terminal_row_is_fine(self):
        self.reload_with({"terminals": {"foot": "foot, -D,"}})
        self.assertEqual(config.TERMINALS, {"foot": ("foot", "-D", None)})

    def test_a_profile_dir_may_be_absolute_or_under_home(self):
        self.reload_with(
            {"chromium-browsers": {"vivaldi-stable": "/srv/vivaldi", "brave-browser": "~/brave"}}
        )
        self.assertEqual(config.CHROMIUM_BROWSERS["vivaldi-stable"], "/srv/vivaldi")
        self.assertEqual(config.CHROMIUM_BROWSERS["brave-browser"], os.path.expanduser("~/brave"))
        self.assertEqual(os.path.join(config.CONFIG_HOME, "/srv/vivaldi"), "/srv/vivaldi")

    def test_a_relative_state_dir_is_taken_from_home(self):
        self.reload_with({"state_dir": ".cache/sessions"})
        self.assertEqual(config.STATE_DIR, os.path.join(config.HOME, ".cache/sessions"))

    def test_an_empty_list_means_none(self):
        self.reload_with({"session_keeping": [], "tui_programs": []})
        self.assertEqual(config.SESSION_KEEPING_CLASSES, frozenset(config.CHROMIUM_BROWSERS))
        self.assertEqual(config.TUI_PROGRAMS, frozenset())


class NumberBounds(ConfigFileCase):
    def test_a_unit_suffix_is_not_a_number(self):
        warned = self.reload_with({"settle_delay": "10s", "save_interval": "1m"})
        self.assertIn("settle_delay must be a number from 0 to", warned)
        self.assertIn("save_interval must be a number from 1 to", warned)
        self.assertEqual((config.SETTLE_DELAY, config.SAVE_INTERVAL), (10, 60))

    def test_save_interval_is_at_least_a_second(self):
        """At 0 the daemon would wake, save and wake again without pause."""
        for text in ("0", "0.5"):
            with self.subTest(text=text):
                warned = self.reload_with({"save_interval": text})
                self.assertIn("save_interval must be a number from 1 to", warned)
                self.assertEqual(config.SAVE_INTERVAL, 60)
        self.assertEqual(self.reload_with({"save_interval": "1"}), "")
        self.assertEqual(config.SAVE_INTERVAL, 1)

    def test_zero_is_fine_for_the_other_timings(self):
        self.assertEqual(
            self.reload_with({"settle_delay": "0", "title_settle": "0", "max_preexisting_windows": "0"}), ""
        )
        self.assertEqual(
            (config.SETTLE_DELAY, config.TITLE_SETTLE, config.MAX_PREEXISTING_WINDOWS), (0, 0, 0)
        )

    def test_a_number_past_what_a_wait_can_hold_is_refused(self):
        """select and sleep overflow at 2**63 nanoseconds and the daemon would die."""
        warned = self.reload_with({"save_interval": "1e19", "settle_delay": str(config.MAX_SECONDS + 1)})
        self.assertIn("save_interval must be a number", warned)
        self.assertIn("settle_delay must be a number", warned)
        self.assertEqual((config.SAVE_INTERVAL, config.SETTLE_DELAY), (60, 10))
        self.assertEqual(self.reload_with({"save_interval": str(config.MAX_SECONDS)}), "")


class Unreadable(ConfigFileCase):
    """What cannot be read at all is reported, and what was in force stays."""

    def test_bytes_that_are_not_utf8_are_reported_not_fatal(self):
        with open(config.CONFIG_FILE, "wb") as f:
            f.write(b"[general]\nexclude = caf\xe9\n")
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            config.reload()
        self.assertIn(f"ignoring {config.CONFIG_FILE}", err.getvalue())
        self.assertEqual(config.EXCLUDE_CLASSES, config.BUILTIN_EXCLUDE_CLASSES)

    def test_a_directory_in_the_files_place_is_reported_not_fatal(self):
        os.mkdir(config.CONFIG_FILE)
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            config.reload()
            config.ensure_file()
        self.assertIn(f"ignoring {config.CONFIG_FILE}", err.getvalue())
        self.assertNotIn("could not write", err.getvalue())

    def test_a_section_repeated_is_reported_and_the_file_ignored(self):
        warned = self.reload_with("[general]\nsave_interval = 7\n[general]\nexclude = a\n")
        self.assertIn(f"ignoring {config.CONFIG_FILE}", warned)
        self.assertEqual(config.SAVE_INTERVAL, 60)

    def test_an_edit_that_stops_parsing_keeps_what_was_in_force(self):
        """A half-saved or broken edit must not drop the daemon to the defaults
        for the next save, which could land in the wrong state directory."""
        self.reload_with({"save_interval": "7", "state_dir": "~/kept"})
        self.write_config("[general\nsave_interval = 9\n")
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertTrue(config.reload_if_changed())
        self.assertIn(f"ignoring {config.CONFIG_FILE}", err.getvalue())
        self.assertEqual((config.SAVE_INTERVAL, config.STATE_DIR), (7, os.path.expanduser("~/kept")))
        self.write_config({"save_interval": "9"})
        self.assertTrue(config.reload_if_changed())
        self.assertEqual((config.SAVE_INTERVAL, config.STATE_DIR), (9, config.DEFAULT_STATE_DIR))

    def test_a_file_unreadable_from_the_start_means_the_defaults(self):
        self.write_config("[general\n")
        with mock.patch.object(sys, "stderr", io.StringIO()):
            config.reload()
        self.assertEqual(config.load_file(), None)
        self.assertEqual(config.SAVE_INTERVAL, 60)


class FirstRun(ConfigFileCase):
    def test_a_missing_config_directory_is_created(self):
        nested = os.path.join(self.config_dir.name, "omarchy", "last-session.ini")
        with mock.patch.object(config, "CONFIG_FILE", nested):
            config.ensure_file()
            self.assertEqual(open(nested).read(), config.render_template())

    def test_a_directory_that_cannot_be_written_is_reported_not_fatal(self):
        os.chmod(self.config_dir.name, 0o500)
        self.addCleanup(os.chmod, self.config_dir.name, 0o700)
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            config.ensure_file()
        self.assertIn(f"could not write {config.CONFIG_FILE}", err.getvalue())
        self.assertFalse(os.path.exists(config.CONFIG_FILE))

    def test_two_commands_starting_at_once_do_not_complain(self):
        """Both find no file; the second one's write meets the first one's."""
        config.write_defaults()
        with (
            mock.patch.object(os.path, "exists", return_value=False),
            mock.patch.object(sys, "stderr", io.StringIO()) as err,
        ):
            config.ensure_file()
        self.assertEqual(err.getvalue(), "")
        self.assertFalse(config.reload_if_changed())

    def test_the_shipped_tables_agree_with_the_code(self):
        """kitty's session relaunch takes its binary from the terminals table."""
        self.assertIn(config.KITTY_CLASS, config.DEFAULTS["terminals"])
        self.assertTrue(
            all(
                cls in config.DEFAULTS["chromium-browsers"]
                for cls in ("Chromium", "chromium", "chromium-browser")
            )
        )


class StateDirFollowsTheFile(ConfigFileCase):
    def test_a_moved_state_dir_takes_the_next_save_with_it(self):
        first, second = (os.path.join(self.config_dir.name, name) for name in ("a", "b"))
        with mock.patch.object(hypr, "query", return_value=[]):
            self.reload_with({"state_dir": first})
            session.save_session()
            self.reload_with({"state_dir": second})
            session.save_session()
        self.assertTrue(os.path.exists(os.path.join(first, "session.json")))
        self.assertTrue(os.path.exists(os.path.join(second, "session.json")))


if __name__ == "__main__":
    unittest.main()
