"""The compositor: hyprctl requests, Lua encoding, and views of its state."""

import json
import os
import select
import socket
import subprocess
import time

from omarchy_last_session import config, warn

# Events that can move a window, or add or remove one. The rest are ignored:
# a title event fires several times a second on a page with a live ticker, and
# titles are picked up by the periodic save instead.
PLACEMENT_EVENTS = frozenset(
    (
        b"openwindow",
        b"closewindow",
        b"movewindow",
        b"movewindowv2",
        b"changefloatingmode",
        b"fullscreen",
        b"pin",
        b"togglegroup",
        b"moveintogroup",
        b"moveoutofgroup",
        b"moveworkspace",
        b"moveworkspacev2",
        b"monitoradded",
        b"monitoraddedv2",
        b"monitorremoved",
    )
)


def query(cmd):
    """One `hyprctl -j` request, decoded."""
    return json.loads(_request("-j", cmd))


def dispatch(lua):
    """One Lua dispatch. A dispatcher missing on this Hyprland would otherwise
    fail silently, and the windows just stay where they landed."""
    reply = _request("dispatch", lua)
    if not reply.startswith("ok"):
        warn(f"dispatch rejected: {reply or 'no reply'}\n  {lua}")
    return reply.startswith("ok")


def eval_lua(lua):
    """Lua run inside the compositor. It reaches the group object, which takes
    an existing window and which no dispatcher exposes."""
    reply = _request("eval", lua)
    if reply != "ok":
        warn(f"eval rejected: {reply or 'no reply'}\n  {lua}")
    return reply == "ok"


def _request(verb, payload):
    done = subprocess.run(["hyprctl", verb, payload], capture_output=True, text=True, timeout=10)
    return done.stdout.strip()


def quote_lua(text):
    """A single-quoted Lua string literal."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def quote_lua_long(text):
    """A long-bracket literal, at a level the payload cannot close."""
    level = ""
    while f"]{level}]" in text:
        level += "="
    return f"[{level}[{text}]{level}]"


def quote_window(address):
    return quote_lua(f"address:{address}")


def format_workspace_selector(workspace):
    """A hyprctl workspace dict as a selector: its number, name:x, or special:x."""
    name = workspace.get("name", "")
    if name.startswith("special") or name == str(workspace.get("id", 0)):
        return name
    return f"name:{name}"


def get_managed_clients():
    """Mapped windows with a class the plugin does not exclude, by address."""
    return {
        c["address"]: c
        for c in query("clients")
        if c.get("mapped") and c.get("class") and c["class"] not in config.EXCLUDE_CLASSES
    }


LAYOUT_FIELDS = ("class", "workspace", "at", "size", "floating", "pinned", "fullscreen", "monitor", "grouped")


class EventStream:
    """Hyprland's event socket. Reading it costs nothing until the compositor
    has something to say, which is what keeps the daemon off a timer."""

    def __init__(self, connection):
        self._connection = connection
        self._partial = b""

    def wait(self, timeout):
        """Block until a window moves, appears or vanishes, or until `timeout`
        seconds pass. False means the compositor has gone, so the caller stops
        rather than snapshotting a session that is being torn down."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self._connection], [], [], remaining)[0]:
                return True
            chunk = self._connection.recv(65536)
            if not chunk:
                return False
            if self._has_placement_event(chunk):
                return True

    def _has_placement_event(self, chunk):
        """Event names arrive as `name>>payload` lines. A line split across two
        reads is held over, so no event is missed at a chunk boundary."""
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        return any(line.split(b">>", 1)[0] in PLACEMENT_EVENTS for line in lines)


def open_event_stream():
    """Hyprland's event socket, or None when it cannot be reached, which leaves
    the daemon on its periodic save alone."""
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if not signature:
        warn("no Hyprland instance to watch, saving on a timer instead")
        return None
    path = os.path.join(runtime_dir, "hypr", signature, ".socket2.sock")
    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(path)
        return EventStream(connection)
    except OSError as e:
        warn(f"could not watch {path}, saving on a timer instead: {e}")
        return None


def wait_for_placement_change(stream, timeout):
    """False when the compositor has gone. Without a stream this is the timer
    the daemon falls back to."""
    if stream is None:
        time.sleep(timeout)
        return True
    return stream.wait(timeout)


def get_layout():
    """Every managed window's placement, by address: what the daemon watches
    for change. Titles are left out, since they change all the time."""
    return {
        address: {field: client.get(field) for field in LAYOUT_FIELDS}
        for address, client in get_managed_clients().items()
    }


def get_monitor_layout():
    """Monitor id -> (name, top-left corner). Names survive a reboot and ids do
    not; the corner turns a saved position into an offset into that monitor."""
    try:
        return {
            m["id"]: (m["name"], [m.get("x", 0), m.get("y", 0)])
            for m in query("monitors")
            if "id" in m and "name" in m
        }
    except (TypeError, ValueError, OSError, subprocess.SubprocessError):
        return {}


def get_monitor_origins():
    """Top-left corner of every monitor, keyed by name and by id."""
    origins = {}
    for monitor_id, (name, corner) in get_monitor_layout().items():
        origins[name] = origins[monitor_id] = tuple(corner)
    return origins
