"""The snapshot: what is open now, written to the state directory."""

import glob
import json
import os
import signal
import stat
import tempfile
import time

from omarchy_last_session import config, hypr, kitty, proc, relaunch, warn


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
    sessions = write_kitty_sessions(clients)
    windows, seen_pids = [], set()
    for client in clients:
        pid = client.get("pid", -1)
        if pid <= 0:
            continue
        cmd = relaunch.build_relaunch_command(client, sessions.get(pid))
        if not cmd:
            continue
        spawn = pid not in seen_pids or not does_reopen_every_window(client, pid in sessions)
        seen_pids.add(pid)
        windows.append(build_window_entry(client, cmd, spawn, group_of.get(client.get("address")), layout))
    return windows


def does_reopen_every_window(client, has_session_file):
    """One process serves every window and brings them all back itself, so a
    second launch would only add a spare. A kitty session file holds every OS
    window of its instance, which makes that kitty one of them."""
    return has_session_file or client["class"] in config.SESSION_KEEPING_CLASSES


def write_kitty_sessions(clients):
    """A session file per kitty that describes itself; pid -> path. Files for
    instances that are gone are dropped, so the directory holds no more than
    the kitty instances now running."""
    written = {}
    pids = sorted({c["pid"] for c in clients if c.get("class") == config.KITTY_CLASS and c.get("pid")})
    for pid in pids:
        text = kitty.build_session_text(pid)
        if text:
            written[pid] = config.KITTY_SESSION_FILE.format(pid=pid)
            write_private(written[pid], text)
    for path in glob.glob(config.KITTY_SESSION_GLOB):
        if path not in written.values():
            os.unlink(path)
    return written


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
    write_private(config.SESSION_FILE, json.dumps({"saved_at": time.time(), "windows": windows}, indent=2))


def keep_restore_copy():
    """What restore is about to bring back, kept where the daemon will not
    overwrite it."""
    try:
        copy_session_to(config.LAST_RESTORE_FILE)
    except OSError as e:
        warn(f"could not keep a copy of the restored session: {e}")


def copy_session_to(path):
    with open_private(config.SESSION_FILE) as f:
        write_private(path, f.read())


def load_session():
    """The saved windows still worth restoring; empty without a usable snapshot."""
    try:
        with open_private(config.SESSION_FILE) as f:
            snapshot = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        warn(f"could not read {config.SESSION_FILE}: {e}")
        return []
    return [w for w in snapshot.get("windows", []) if w["class"] not in config.EXCLUDE_CLASSES]


# A snapshot holds every window's command line and title, so the state
# directory and its files are this user's alone, whatever the umask.
def ensure_private_state_dir():
    """Creates the directory 0700, and takes group and other bits off one an
    older version left open. A symlink or another user's directory is refused."""
    os.makedirs(config.STATE_DIR, mode=0o700, exist_ok=True)
    st = os.lstat(config.STATE_DIR)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
        raise PermissionError(f"{config.STATE_DIR} is not a directory owned by this user")
    if st.st_mode & 0o077:
        os.chmod(config.STATE_DIR, 0o700)


def write_private(path, text):
    """Replaces a state file atomically with one of mode 0600. The temporary
    file is created exclusively, so a symlink planted under its name is not
    followed, and a symlink at path is replaced rather than written through."""
    ensure_private_state_dir()
    fd, tmp = tempfile.mkstemp(dir=config.STATE_DIR, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def open_private(path):
    """Opens a state file for reading without following a symlink, and takes
    group and other bits off one an older version left open."""
    ensure_private_state_dir()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    if os.fstat(fd).st_mode & 0o077:
        os.fchmod(fd, 0o600)
    return os.fdopen(fd)


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
