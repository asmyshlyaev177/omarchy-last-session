"""Recover the command that reopens a window: from /proc, or failing that from
the app's .desktop entry."""

import glob
import os
import shlex
import shutil

from omarchy_last_session import config, proc

HOME = os.path.expanduser("~")


def build_relaunch_command(client, session_file=None):
    """The shell command that recreates this window's process, or None."""
    pid, cls = client["pid"], client.get("class", "")
    if session_file:
        return shlex.join([config.TERMINALS[cls][0], config.KITTY_SESSION_FLAG, session_file])
    if cls in config.TERMINALS:
        return build_terminal_command(pid, cls)
    argv = drop_per_run_args(unflatten_argv(proc.read_cmdline(pid) or []))
    cmd = shlex.join(argv) if argv else ""
    if not is_replayable(cmd):
        cmd = find_desktop_command(cls) or ""
    return add_restore_flag(cmd, cls) if cmd else None


def build_terminal_command(pid, cls):
    """A terminal's command line says nothing about its contents: relaunch the
    TUI it was running in that TUI's directory, else reopen in the shell's."""
    binary, cwd_flag, exec_flag = config.TERMINALS[cls]
    tui = proc.find_descendant(pid, config.TUI_PROGRAMS)
    tui_argv = read_tui_argv(tui) if tui is not None else []
    if tui_argv:
        argv = build_terminal_argv(binary, cwd_flag, proc.read_cwd(tui) or HOME)
        return shlex.join(argv + ([exec_flag] if exec_flag else []) + tui_argv)
    shell = proc.find_descendant(pid, config.SHELLS)
    cwd = (proc.read_cwd(shell) if shell else None) or proc.read_cwd(pid) or HOME
    return shlex.join(build_terminal_argv(binary, cwd_flag, cwd))


def read_tui_argv(pid):
    """Empty for a process that is gone, so the terminal falls back to its shell."""
    argv = proc.read_cmdline(pid) or [proc.read_comm(pid) or ""]
    return drop_per_run_args(argv)


def drop_per_run_args(argv):
    """Without the arguments that point at nothing once the process is gone."""
    return [arg for arg in argv if arg and not arg.startswith(config.PER_RUN_ARG_PREFIXES)]


def build_terminal_argv(binary, cwd_flag, cwd):
    if cwd_flag.endswith("="):
        return [binary, cwd_flag + cwd]
    return [binary, cwd_flag, cwd]


def unflatten_argv(argv):
    """Chromium rewrites argv into one blob to set its process title. Split it
    back only when the first token runs, so real paths with spaces survive."""
    if len(argv) != 1 or " " not in argv[0]:
        return argv
    try:
        tokens = shlex.split(argv[0])
    except ValueError:
        return argv
    return tokens if tokens and is_runnable(tokens[0]) else argv


def is_runnable(path):
    if os.sep in path:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    return shutil.which(path) is not None


def is_replayable(cmd):
    """False for what a reboot invalidates: an AppImage's temporary mount, and
    a D-Bus activation, which starts a service rather than a window."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens or config.DBUS_SERVICE_FLAG in tokens:
        return False
    return not tokens[0].startswith(config.APPIMAGE_MOUNT_PREFIX) and is_runnable(tokens[0])


def add_restore_flag(cmd, cls):
    flag = config.RESTORE_FLAGS.get(cls)
    if not flag or flag in cmd:
        return cmd
    return f"{cmd} {flag}"


def normalize_class(cls):
    lowered = cls.lower()
    for prefix in config.CLASS_PREFIXES:
        if lowered.startswith(prefix):
            return lowered[len(prefix) :]
    return lowered


def find_desktop_command(cls):
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


def iter_desktop_entries():
    """(path, entry) for every launchable .desktop file, user directories first."""
    for directory in config.DESKTOP_DIRS:
        for path in sorted(glob.glob(os.path.join(os.path.expanduser(directory), "*.desktop"))):
            entry = read_desktop_entry(path)
            if entry.get("exec"):
                yield path, entry


def get_desktop_entry_names(path, entry):
    """The file name and StartupWMClass, either of which can name a class."""
    names = {os.path.basename(path).removesuffix(".desktop").lower()}
    if entry.get("startupwmclass"):
        names.add(entry["startupwmclass"].lower())
    return names


def get_desktop_entry_program(entry):
    """Basename of the program Exec runs, lowercased; None when unparsable."""
    try:
        return os.path.basename(shlex.split(entry["exec"])[0]).lower()
    except (ValueError, IndexError):
        return None


def read_desktop_entry(path):
    """Keys of [Desktop Entry], lowercased, with Exec's field codes stripped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f]
    except OSError:
        return {}
    entry, in_section = {}, False
    for line in lines:
        if line.startswith("["):
            in_section = line == "[Desktop Entry]"
        elif in_section and "=" in line:
            key, _, value = line.partition("=")
            entry.setdefault(key.strip().lower(), value.strip())
    if entry.get("exec"):
        entry["exec"] = config.DESKTOP_FIELD_CODES.sub("", entry["exec"]).strip()
    return entry
