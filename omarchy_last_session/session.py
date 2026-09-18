"""The snapshot: what is open now, written to the state directory."""

import json
import os
import signal
import time

from omarchy_last_session import config, hypr, proc, relaunch


def save_session(keep_previous_when_empty=True):
    """Snapshot every mapped window. Returns how many were saved, or None when
    nothing is open and the previous snapshot was kept: the power menu closes
    every window before powering off, and a daemon tick in that gap must not
    blank the snapshot it just took."""
    windows = snapshot_windows()
    if not windows and keep_previous_when_empty and os.path.exists(config.SESSION_FILE):
        return None
    write_session(windows)
    return len(windows)


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
