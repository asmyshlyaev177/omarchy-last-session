"""The snapshot: what is open now, written to the state directory."""

import json
import os
import shutil
import signal
import time

from omarchy_last_session import config, hypr, proc, relaunch, warn


def save_session():
    """Snapshot every mapped window; returns how many."""
    windows = snapshot_windows()
    write_session(windows)
    return len(windows)


class SaveScheduler:
    """When the daemon writes. At once when windows appear or move. Only after
    the desktop has been still for SETTLE_DELAY when windows vanish, so the
    power menu closing every window before a poweroff is never snapshotted
    half done. And at least every SAVE_INTERVAL, for titles and floating
    geometry, which change without a window coming or going."""

    def __init__(self):
        self.saved = None
        self.saved_at = 0.0
        self.current = None
        self.still_since = 0.0

    def is_due(self, layout, now):
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

    def mark_saved(self, now):
        self.saved, self.saved_at = self.current, now

    def seconds_until_recheck(self, now):
        """How long the daemon may sleep if nothing happens: until vanished
        windows stop being provisional, or until the periodic save."""
        deadlines = [self.saved_at + config.SAVE_INTERVAL]
        if self.current != self.saved:
            deadlines.append(self.still_since + config.SETTLE_DELAY)
        return max(0.0, min(deadlines) - now)


def snapshot_windows():
    clients = list(hypr.get_managed_clients().values())
    group_of = assign_group_ids(clients)
    layout = hypr.get_monitor_layout()
    windows, seen_pids = [], set()
    for client in clients:
        pid = client.get("pid", -1)
        if pid <= 0:
            continue
        cmd = relaunch.build_relaunch_command(client)
        if not cmd:
            continue
        # Once per window, except an app that reopens its own: a second launch
        # of one process adds an empty window instead.
        spawn = pid not in seen_pids or client["class"] not in config.SESSION_KEEPING_CLASSES
        seen_pids.add(pid)
        windows.append(build_window_entry(client, cmd, spawn, group_of.get(client.get("address")), layout))
    return windows


def build_window_entry(client, cmd, spawn, group, layout):
    monitor_name, monitor_at = layout.get(client.get("monitor"), ("", [0, 0]))
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
        "monitor_name": monitor_name,
        "monitor_at": monitor_at,
        "cmd": cmd,
        "spawn": spawn,
        "group": group,
    }


def assign_group_ids(clients):
    """address -> a stable id per Hyprland group of two or more."""
    ids, seen = {}, {}
    for client in clients:
        members = tuple(sorted(client.get("grouped") or []))
        if len(members) >= 2:
            ids[client["address"]] = seen.setdefault(members, len(seen))
    return ids


def write_session(windows):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    # Named per process: the daemon and a power-menu shutdown may save at once.
    tmp = f"{config.SESSION_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump({"saved_at": time.time(), "windows": windows}, f, indent=2)
    os.replace(tmp, config.SESSION_FILE)


def keep_restore_copy():
    """What restore is about to bring back, kept where the daemon will not
    overwrite it."""
    try:
        shutil.copyfile(config.SESSION_FILE, config.LAST_RESTORE_FILE)
    except OSError as e:
        warn(f"could not keep a copy of the restored session: {e}")


def load_session():
    """The saved windows still worth restoring; empty without a usable snapshot."""
    try:
        with open(config.SESSION_FILE) as f:
            snapshot = json.load(f)
    except (OSError, ValueError):
        return []
    return [w for w in snapshot.get("windows", []) if w["class"] not in config.EXCLUDE_CLASSES]


def quit_session_keeping_apps(timeout=config.GRACEFUL_QUIT_TIMEOUT):
    """SIGTERM the browsers and editors so they write their session out: a
    plain close hits the "close N tabs?" dialog and they record a crash.
    Returns the pids still alive when the wait ran out."""
    pids = {
        c["pid"]
        for c in hypr.query("clients")
        if c.get("class") in config.SESSION_KEEPING_CLASSES and c.get("pid", -1) > 0
    }
    signalled = set()
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
