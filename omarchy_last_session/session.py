"""The snapshot: what is open now, written to the state directory."""

from __future__ import annotations

import glob
import json
import os
import signal
import stat
import time
from typing import Optional, TextIO, TypedDict

from omarchy_last_session import config, files, hypr, kitty, proc, relaunch, warn

# "class" is a keyword, so the functional form. Every key here has been written
# since the first release.
_SavedWindowKeys = TypedDict(
    "_SavedWindowKeys",
    {
        "class": str,
        "title": str,
        "workspace": hypr.Workspace,
        "at": list[int],
        "size": list[int],
        "floating": bool,
        "pinned": bool,
        "fullscreen": int,
        "monitor": int,
        "monitor_name": str,
        "monitor_at": list[int],
        "cmd": str,
        "spawn": bool,
        "group": Optional[int],
    },
)


class SavedWindow(_SavedWindowKeys, total=False):
    # Written since 2026-09-26, so a snapshot saved before then has none.
    workspace_shown: bool


class Snapshot(TypedDict, total=False):
    saved_at: float
    windows: list[SavedWindow]


def save_session() -> int:
    windows = snapshot_windows()
    write_session(windows)
    return len(windows)


class SaveScheduler:
    """When the daemon writes: at once when windows appear or move, only after
    SETTLE_DELAY of quiet when they vanish, and every SAVE_INTERVAL regardless,
    since titles and floating geometry change without a window event."""

    def __init__(self) -> None:
        self.saved: hypr.Layout | None = None
        self.saved_at = 0.0
        self.current: hypr.Layout | None = None
        self.still_since = 0.0

    def is_due(self, layout: hypr.Layout, now: float) -> bool:
        if layout != self.current:
            self.current, self.still_since = layout, now
        if self.saved is None:
            return True
        only_vanished = set(layout) < set(self.saved)
        if layout != self.saved and not only_vanished:
            return True
        if now - self.still_since < config.SETTLE_DELAY:
            return False
        return layout != self.saved or now - self.saved_at >= config.SAVE_INTERVAL

    def mark_saved(self, now: float) -> None:
        self.saved, self.saved_at = self.current, now

    def seconds_until_recheck(self, now: float) -> float:
        """How long the daemon may sleep if nothing happens."""
        deadlines = [self.saved_at + config.SAVE_INTERVAL]
        if self.current != self.saved:
            deadlines.append(self.still_since + config.SETTLE_DELAY)
        return max(0.0, min(deadlines) - now)


def snapshot_windows() -> list[SavedWindow]:
    clients = list(hypr.get_managed_clients().values())
    group_of = assign_group_ids(clients)
    layout = hypr.get_monitor_layout()
    sessions = write_kitty_sessions(clients)
    windows: list[SavedWindow] = []
    launched: set[int] = set()
    for client in clients:
        pid = client.get("pid", -1)
        if pid <= 0:
            continue
        cmd = relaunch.build_relaunch_command(client, sessions.get(pid))
        if not cmd:
            continue
        once = does_launch_once(client, pid in sessions)
        spawn = pid not in launched or not once
        # Only a launch that brings back the whole process counts for it: a web
        # app window shares its browser's process, and listed first it must not
        # leave the browser unlaunched.
        if once:
            launched.add(pid)
        windows.append(
            build_window_entry(client, cmd, spawn, group_of.get(client.get("address", "")), layout)
        )
    return windows


def does_launch_once(client: hypr.Client, has_session_file: bool) -> bool:
    """Whether one launch of this process serves all of its windows. A kitty
    session file holds every OS window of its instance, so it counts."""
    return has_session_file or client["class"] in config.SINGLE_INSTANCE_CLASSES


def write_kitty_sessions(clients: list[hypr.Client]) -> dict[int, str]:
    """pid -> session file, for each kitty that describes itself. Files left by
    instances that are gone are dropped."""
    written: dict[int, str] = {}
    # A kitty taken out of [terminals] is relaunched like any other window.
    known = config.KITTY_CLASS in config.TERMINALS
    pids = sorted(
        {c["pid"] for c in clients if known and c.get("class") == config.KITTY_CLASS and c.get("pid")}
    )
    for pid in pids:
        text = kitty.build_session_text(pid)
        if text:
            written[pid] = config.KITTY_SESSION_FILE.format(pid=pid)
            write_private(written[pid], text)
    for path in glob.glob(config.KITTY_SESSION_GLOB):
        if path not in written.values():
            os.unlink(path)
    return written


def build_window_entry(
    client: hypr.Client, cmd: str, spawn: bool, group: int | None, layout: dict[int, hypr.MonitorPlace]
) -> SavedWindow:
    monitor = layout.get(client.get("monitor", -1), hypr.MonitorPlace("", [0, 0], None))
    return {
        "class": client["class"],
        "title": client.get("title", ""),
        "workspace": client["workspace"],
        "at": client["at"],
        "size": client["size"],
        "floating": client.get("floating", False),
        "pinned": client.get("pinned", False),
        "fullscreen": client.get("fullscreen", 0),
        "monitor": client.get("monitor", 0),
        "monitor_name": monitor.name,
        "monitor_at": monitor.at,
        "workspace_shown": client["workspace"].get("id") == monitor.shown_workspace,
        "cmd": cmd,
        "spawn": spawn,
        "group": group,
    }


def assign_group_ids(clients: list[hypr.Client]) -> dict[str, int]:
    """address -> a stable id per Hyprland group of two or more."""
    ids: dict[str, int] = {}
    seen: dict[tuple[str, ...], int] = {}
    for client in clients:
        members = tuple(sorted(client.get("grouped") or []))
        if len(members) >= 2:
            ids[client["address"]] = seen.setdefault(members, len(seen))
    return ids


def write_session(windows: list[SavedWindow]) -> None:
    write_private(config.SESSION_FILE, json.dumps({"saved_at": time.time(), "windows": windows}, indent=2))


def keep_restore_copy() -> None:
    """Keeps what restore is about to use, which the daemon soon overwrites."""
    try:
        copy_session_to(config.LAST_RESTORE_FILE)
    except OSError as e:
        warn(f"could not keep a copy of the restored session: {e}")


def copy_session_to(path: str) -> None:
    with open_private(config.SESSION_FILE) as f:
        write_private(path, f.read())


def load_session() -> list[SavedWindow]:
    """The saved windows still worth restoring; empty without a usable snapshot."""
    try:
        with open_private(config.SESSION_FILE) as f:
            snapshot: Snapshot = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        warn(f"could not read {config.SESSION_FILE}: {e}")
        return []
    return [w for w in snapshot.get("windows", []) if w["class"] not in config.EXCLUDE_CLASSES]


# A snapshot holds every window's command line and title, so the directory and
# its files stay this user's alone whatever the umask, and no symlink in the
# state directory's place is followed.
def ensure_private_state_dir() -> None:
    """Creates the directory 0700, repairing one an older version left open.
    A symlink or another user's directory is refused."""
    os.makedirs(config.STATE_DIR, mode=0o700, exist_ok=True)
    st = os.lstat(config.STATE_DIR)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise PermissionError(f"{config.STATE_DIR} is not a directory owned by this user")
    if st.st_mode & 0o077:
        os.chmod(config.STATE_DIR, 0o700)


def write_private(path: str, text: str) -> None:
    """Replaces a state file atomically with one of mode 0600."""
    ensure_private_state_dir()
    files.replace_file(path, text, 0o600)


def open_private(path: str) -> TextIO:
    """Opens a state file for reading, repairing one left readable by others."""
    ensure_private_state_dir()
    f = files.open_regular_file(path)
    if os.fstat(f.fileno()).st_mode & 0o077:
        os.fchmod(f.fileno(), 0o600)
    return f


def quit_session_keeping_apps(timeout: float = config.GRACEFUL_QUIT_TIMEOUT) -> set[int]:
    """SIGTERM them so they write their session out, rather than let the power
    menu close their windows. Returns the pids still alive at the timeout."""
    pids = {
        c["pid"]
        for c in hypr.query("clients")
        if c.get("class") in config.SESSION_KEEPING_CLASSES and c.get("pid", -1) > 0
    }
    signalled: set[int] = set()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            signalled.add(pid)
        except OSError:
            continue
    deadline = time.time() + timeout
    while signalled and time.time() < deadline:
        time.sleep(0.25)
        signalled = {pid for pid in signalled if proc.is_alive(pid)}
    return signalled
