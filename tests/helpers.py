"""Fixtures shared by the unit tests.

/proc and hyprctl are read through small named functions, so every test patches
those at the module that owns them (proc, hypr, config) and the code under
test sees the fake through the module attribute. Nothing mocks Hyprland itself.
"""

import contextlib
import io
import itertools
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

from omarchy_last_session import chromium, config, hypr, notification, proc, relaunch, restore, session
from omarchy_last_session.restore import layout

# Brave's real command line, as Chromium reports it: one argument holding the
# whole thing, because it rewrites argv to set the process title.
BRAVE_BLOB = (
    "/opt/brave-bin/brave --ozone-platform=wayland "
    '--password-store=gnome-libsecret --enable-hardware-overlays=""'
)

_addresses = itertools.count(1)


def client(cls, pid=1234, **overrides):
    """A client as hyprctl reports it, with a fresh address."""
    base = {
        "class": cls,
        "pid": pid,
        "mapped": True,
        "title": "",
        "address": f"0x{next(_addresses):x}",
        "grouped": [],
        "workspace": {"id": 2, "name": "2"},
        "at": [0, 0],
        "size": [800, 600],
        "floating": False,
        "pinned": False,
        "fullscreen": 0,
        "monitor": 0,
    }
    base.update(overrides)
    return base


def saved_window(
    cls,
    ws=2,
    spawn=True,
    floating=False,
    at=(0, 0),
    size=(800, 600),
    pinned=False,
    fullscreen=0,
    cmd=None,
    monitor_name="",
):
    """A window entry as save writes it."""
    return {
        "class": cls,
        "title": "",
        "workspace": {"id": ws, "name": str(ws)},
        "at": list(at),
        "size": list(size),
        "floating": floating,
        "pinned": pinned,
        "fullscreen": fullscreen,
        "monitor": 0,
        "monitor_name": monitor_name,
        "cmd": cmd or f"/usr/bin/{cls}",
        "spawn": spawn,
    }


def live_window(cls, ws=2, at=(0, 0), size=(800, 600), members=0):
    """A client as the sweep sees it once restore has launched everything."""
    return {
        "class": cls,
        "workspace": {"id": ws},
        "at": list(at),
        "size": list(size),
        "grouped": ["0x0"] * members,
    }


def grouped_clients(*specs):
    """specs: (class, pid, group_key). Windows sharing a group_key are one
    Hyprland group, which reports every member's address on each member."""
    made = [client(cls, pid=pid) for cls, pid, _ in specs]
    by_group = {}
    for win, (_, _, key) in zip(made, specs):
        if key is not None:
            by_group.setdefault(key, []).append(win["address"])
    for win, (_, _, key) in zip(made, specs):
        win["grouped"] = list(by_group.get(key, []))
    return made


def pretend_runnable():
    """Brave's binary is not on every machine the suite runs on."""
    return mock.patch.object(relaunch, "is_runnable", return_value=True)


def write_executable(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(path, 0o755)


def mode_of(path):
    """Permission bits of the path itself, a symlink included."""
    return stat.S_IMODE(os.lstat(path).st_mode)


def render_ini(sections):
    """Lists become comma separated values."""
    text = ""
    for name, entries in sections.items():
        text += f"[{name}]\n"
        for key, value in entries.items():
            text += f"{key} = {', '.join(value) if isinstance(value, list) else value}\n"
    return text


class ConfigFileCase(unittest.TestCase):
    """A config file of the test's own, absent until written, with the
    defaults back in force afterwards."""

    def setUp(self):
        self.config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_dir.cleanup)
        # Registered before the patch so it runs after the patch is undone.
        self.addCleanup(config.reload)
        path = os.path.join(self.config_dir.name, "last-session.ini")
        patcher = mock.patch.object(config, "CONFIG_FILE", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        config.reload()

    def write_config(self, content):
        """A string is written as it is. In a dict, a dict value is a table
        section of its own and everything else goes under [general]."""
        if not isinstance(content, str):
            general = {key: value for key, value in content.items() if not isinstance(value, dict)}
            tables = {key: value for key, value in content.items() if isinstance(value, dict)}
            content = render_ini({config.SECTION: general, **tables})
        with open(config.CONFIG_FILE, "w") as f:
            f.write(content)

    def reload_with(self, content):
        """Writes and reloads; returns what was warned."""
        self.write_config(content)
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            config.reload()
        return err.getvalue()


class StateDirCase(unittest.TestCase):
    """A temporary state directory, patched into config for the test."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.use_state_dir(self.dir.name)

    def use_state_dir(self, path):
        """Point every state path at path, which need not exist yet."""
        self.session = os.path.join(path, "session.json")
        self.disabled = os.path.join(path, "disabled")
        self.copy = os.path.join(path, "last-shutdown.json")
        self.restored = os.path.join(path, "last-restore.json")
        self.patch(config, "STATE_DIR", path)
        self.patch(config, "SESSION_FILE", self.session)
        self.patch(config, "DISABLE_FLAG", self.disabled)
        self.patch(config, "LAST_SHUTDOWN_FILE", self.copy)
        self.patch(config, "LAST_RESTORE_FILE", self.restored)
        self.patch(config, "KITTY_SESSION_FILE", os.path.join(path, "kitty-{pid}.session"))
        self.patch(config, "KITTY_SESSION_GLOB", os.path.join(path, "kitty-*.session"))

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_session(self, windows):
        with open(self.session, "w") as f:
            json.dump({"saved_at": 1, "windows": windows}, f)

    def read_session(self, path=None):
        with open(path or self.session) as f:
            return json.load(f)["windows"]

    def save_with(self, clients, cmdline=("/bin/sh",)):
        """save_session with hyprctl and /proc faked; returns its result."""
        with (
            mock.patch.object(hypr, "query", return_value=clients),
            mock.patch.object(proc, "read_cmdline", return_value=list(cmdline)),
        ):
            return session.save_session()


class RestoreHarness(StateDirCase):
    """restore reaches the compositor through hypr.dispatch, hypr.eval_lua and
    hypr.get_managed_clients, so the whole pass is observable. The first two
    both send Lua and are recorded in one list, so their order is too."""

    def setUp(self):
        super().setUp()
        self.patch(time, "sleep", mock.Mock())
        # restore reports what it did on stdout and what it could not do on
        # stderr; a test that cares captures them itself, the rest keep both
        # out of the run
        self.patch(sys, "stdout", io.StringIO())
        self.patch(sys, "stderr", io.StringIO())
        # one monitor by default: the placement pass stays a no-op
        self.patch(hypr, "query", mock.Mock(return_value=[]))
        # never touch a real browser profile
        self.patch(chromium, "mark_clean_exit", mock.Mock(return_value=0))
        # held workspaces have tests of their own; here they would only shift
        # every other line of Lua along
        self.patch(layout, "hold_workspaces", mock.Mock(return_value=0))
        self.patch(layout, "release_workspaces", mock.Mock())

    def run_restore(self, clients_sequence, sweep_timeout=0):
        """Every line of Lua restore sent, dispatched or evaluated, and each
        browser profile it marked as cleanly exited, in order. `timeline` has
        the toast going up and down among them, and `toast_seconds` how long
        the toast asked to stay.

        The sweep polls until it runs out of time, so the clock is faked (one
        second per reading) and the last client view repeats forever. Without
        both, a window that never turns up hangs or exhausts the mock.
        """
        views = list(clients_sequence)

        def next_view():
            return views.pop(0) if len(views) > 1 else views[0]

        sent = self.timeline = []

        # Stands in for the real one, which would raise a toast on the machine
        # the suite runs on.
        @contextlib.contextmanager
        def toast(summary, body, glyph, seconds):
            sent.append(f"toast up: {summary} {body}")
            self.toast_seconds = seconds
            try:
                yield
            finally:
                sent.append(f"toast down: {summary}")

        with (
            mock.patch.object(notification, "showing", toast),
            mock.patch.object(config, "SWEEP_TIMEOUT", sweep_timeout),
            mock.patch.object(config, "SPAWN_STAGGER", 0),
            mock.patch.object(time, "time", side_effect=itertools.count(1001)),
            mock.patch.object(hypr, "get_managed_clients", side_effect=next_view),
            mock.patch.object(hypr, "dispatch", side_effect=sent.append),
            mock.patch.object(hypr, "eval_lua", side_effect=sent.append),
            mock.patch.object(
                chromium,
                "mark_clean_exit",
                side_effect=lambda cls, cmd: sent.append(f"mark_clean_exit {cls} {cmd}"),
            ),
        ):
            restore.restore_session()
        return [line for line in sent if not line.startswith("toast ")]
