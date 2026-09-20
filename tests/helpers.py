"""Fixtures shared by the unit tests.

/proc and hyprctl are read through small named functions, so every test patches
those at the module that owns them (proc, hypr, config) and the code under
test sees the fake through the module attribute. Nothing mocks Hyprland itself.
"""

import itertools
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from omarchy_last_session import config, hypr, proc, relaunch, session

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
