"""The compositor: hyprctl requests, Lua encoding, and views of its state."""

import json
import subprocess

from omarchy_last_session import config, warn


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
