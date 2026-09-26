"""Recover the command that reopens a window: from /proc, or failing that from
the app's .desktop entry."""

from __future__ import annotations

import glob
import os
import re
import shlex
import shutil
import urllib.parse
from collections.abc import Iterator

from omarchy_last_session import config, hypr, proc

HOME = os.path.expanduser("~")
APP_FLAG = "--app="
APP_ID_FLAG = "--app-id="
# Flags that open one window as an app rather than the browser.
APP_FLAG_PREFIXES = (APP_FLAG, APP_ID_FLAG)
# The Wayland app id Chromium gives a window opened with --app=<url>: host and
# path joined by "_", every "/" turned into "_", then the profile directory.
WEB_APP_CLASS = re.compile(r"^(?:chrome|chromium|brave|msedge)-(?P<name>[^_/]+__.*)-(?P<profile>[^-]+)$")
# The one it gives a window of an app installed with the Install button, opened
# with --app-id=<id>: the app's 32-letter id, then the profile directory.
INSTALLED_APP_CLASS = re.compile(r"^(?:chrome|chromium|brave|msedge)-(?P<app_id>[a-p]{32})-[^-]+$")


def build_relaunch_command(client: hypr.Client, session_file: str | None = None) -> str | None:
    """The shell command that recreates this window's process, or None."""
    pid, cls = client["pid"], client.get("class", "")
    if session_file:
        return shlex.join([config.TERMINALS[cls][0], config.KITTY_SESSION_FLAG, session_file])
    if cls in config.TERMINALS:
        return build_terminal_command(pid, cls)
    argv = fit_app_flags(drop_per_run_args(unflatten_argv(proc.read_cmdline(pid) or [])), cls)
    cmd = shlex.join(argv) if argv else ""
    if not is_replayable(cmd):
        cmd = find_desktop_command(cls) or ""
    return add_restore_flag(cmd, cls) if cmd else None


def fit_app_flags(argv: list[str], cls: str) -> list[str]:
    """A Chromium browser is one process for all its windows, and its command
    line is that of the launch that started it: when that was a web app, every
    window reports the app's flag. A browser window drops it, and a web app
    window gets its own back from its class."""
    own = find_own_app_flag(cls, argv)
    if own is None and cls not in config.CHROMIUM_BROWSERS:
        return argv
    kept = [arg for arg in argv if not arg.startswith(APP_FLAG_PREFIXES)]
    return kept + [own] if own else kept


def find_own_app_flag(cls: str, argv: list[str]) -> str | None:
    """--app-id=<id> for a window of an installed app, --app=<url> for one opened by URL."""
    installed = INSTALLED_APP_CLASS.match(cls)
    if installed:
        return APP_ID_FLAG + installed["app_id"]
    url = find_web_app_url(cls, argv)
    return APP_FLAG + url if url else None


def find_web_app_url(cls: str, argv: list[str]) -> str | None:
    """The --app URL a web app window was opened with: the one on the command
    line when it names this window, else rebuilt from the class, which keeps
    the host and path and loses the scheme and whether a "_" was a "/"."""
    match = WEB_APP_CLASS.match(cls)
    if not match:
        return None
    for arg in argv:
        if arg.startswith(APP_FLAG) and get_web_app_name(arg[len(APP_FLAG) :]) == match["name"]:
            return arg[len(APP_FLAG) :]
    host, _, path = match["name"].partition("__")
    return f"https://{host}/{path.replace('_', '/')}"


def get_web_app_name(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.hostname or ''}_{parts.path}".replace("/", "_")


def build_terminal_command(pid: int, cls: str) -> str:
    """A terminal's command line says nothing about its contents: relaunch the
    TUI it was running in that TUI's directory, else reopen in the shell's."""
    binary, cwd_flag, exec_flag = config.TERMINALS[cls]
    tui = proc.find_descendant(pid, config.TUI_PROGRAMS)
    tui_argv = read_tui_argv(tui) if tui is not None else []
    if tui is not None and tui_argv:
        argv = build_terminal_argv(binary, cwd_flag, proc.read_cwd(tui) or HOME)
        return shlex.join(argv + ([exec_flag] if exec_flag else []) + tui_argv)
    shell = proc.find_descendant(pid, config.SHELLS)
    cwd = (proc.read_cwd(shell) if shell else None) or proc.read_cwd(pid) or HOME
    return shlex.join(build_terminal_argv(binary, cwd_flag, cwd))


def read_tui_argv(pid: int) -> list[str]:
    """Empty for a process that is gone, so the terminal falls back to its shell."""
    argv = proc.read_cmdline(pid) or [proc.read_comm(pid) or ""]
    return drop_per_run_args(argv)


def drop_per_run_args(argv: list[str]) -> list[str]:
    """Without the arguments that point at nothing once the process is gone."""
    return [arg for arg in argv if arg and not arg.startswith(config.PER_RUN_ARG_PREFIXES)]


def build_terminal_argv(binary: str, cwd_flag: str, cwd: str) -> list[str]:
    if cwd_flag.endswith("="):
        return [binary, cwd_flag + cwd]
    return [binary, cwd_flag, cwd]


def unflatten_argv(argv: list[str]) -> list[str]:
    """Chromium rewrites argv into one blob to set its process title. Split it
    back only when the first token runs, so real paths with spaces survive."""
    if len(argv) != 1 or " " not in argv[0]:
        return argv
    try:
        tokens = shlex.split(argv[0])
    except ValueError:
        return argv
    return tokens if tokens and is_runnable(tokens[0]) else argv


def is_runnable(path: str) -> bool:
    if os.sep in path:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    return shutil.which(path) is not None


def is_replayable(cmd: str) -> bool:
    """False for what a reboot invalidates: an AppImage's temporary mount, and
    a D-Bus activation, which starts a service rather than a window."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens or config.DBUS_SERVICE_FLAG in tokens:
        return False
    return not tokens[0].startswith(config.APPIMAGE_MOUNT_PREFIX) and is_runnable(tokens[0])


def add_restore_flag(cmd: str, cls: str) -> str:
    flag = config.RESTORE_FLAGS.get(cls)
    if not flag or flag in cmd:
        return cmd
    return f"{cmd} {flag}"


def normalize_class(cls: str) -> str:
    lowered = cls.lower()
    for prefix in config.CLASS_PREFIXES:
        if lowered.startswith(prefix):
            return lowered[len(prefix) :]
    return lowered


def find_desktop_command(cls: str) -> str | None:
    """Exec line of the .desktop entry for a window class, or None. An entry
    named after the class beats one that merely runs a program of that name:
    every game shortcut Steam writes runs steam, with the game's URL."""
    wanted = normalize_class(cls)
    by_program = None
    for path, entry in iter_desktop_entries():
        if wanted in get_desktop_entry_names(path, entry):
            return entry["exec"]
        if by_program is None and wanted == get_desktop_entry_program(entry):
            by_program = entry["exec"]
    return by_program


def iter_desktop_entries() -> Iterator[tuple[str, dict[str, str]]]:
    """(path, entry) for every launchable .desktop file, user directories first."""
    for directory in config.DESKTOP_DIRS:
        for path in sorted(glob.glob(os.path.join(os.path.expanduser(directory), "*.desktop"))):
            entry = read_desktop_entry(path)
            if entry.get("exec"):
                yield path, entry


def get_desktop_entry_names(path: str, entry: dict[str, str]) -> set[str]:
    """The file name and StartupWMClass, either of which can name a class."""
    names = {os.path.basename(path).removesuffix(".desktop").lower()}
    if entry.get("startupwmclass"):
        names.add(entry["startupwmclass"].lower())
    return names


def get_desktop_entry_program(entry: dict[str, str]) -> str | None:
    """Basename of the program Exec runs, lowercased; None when unparsable."""
    try:
        return os.path.basename(shlex.split(entry["exec"])[0]).lower()
    except (ValueError, IndexError):
        return None


def read_desktop_entry(path: str) -> dict[str, str]:
    """Keys of [Desktop Entry], lowercased, with Exec's field codes stripped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f]
    except OSError:
        return {}
    entry: dict[str, str] = {}
    in_section = False
    for line in lines:
        if line.startswith("["):
            in_section = line == "[Desktop Entry]"
        elif in_section and "=" in line:
            key, _, value = line.partition("=")
            entry.setdefault(key.strip().lower(), value.strip())
    if entry.get("exec"):
        entry["exec"] = config.DESKTOP_FIELD_CODES.sub("", entry["exec"]).strip()
    return entry
