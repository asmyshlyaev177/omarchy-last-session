"""Knobs. The ones a user may change come from the config file, which ships
every default in SETTINGS and TABLES; everything else is a constant here."""

import configparser
import math
import os
import re

from omarchy_last_session import warn

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
# INI rather than JSON so the file the plugin writes can carry its own comments.
CONFIG_FILE = os.path.join(CONFIG_HOME, "omarchy", "last-session.ini")
SECTION = "general"
HOME = os.path.expanduser("~")
DEFAULT_STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local/state"), "omarchy-last-session"
)
HEADER = """\
# omarchy-last-session settings. Every value below is the default the plugin
# ships with. A saved change takes effect within a minute, with no restart.
# Lists are comma separated. A key left out keeps its default, and a section
# left out keeps its whole table.
"""
FOOTER = """\
# Nothing to set for Steam or an AppImage: their command lines cannot be
# replayed, so each is relaunched through its .desktop entry. Firefox and Zen
# restore their own session with no help.
"""

# [general]: key -> (default, the comment written above it in the file).
# A list in the file replaces the default list.
SETTINGS = {
    "exclude": (
        [],
        "Window classes never saved or restored. The shell's and the compositor's own\n"
        "(org.quickshell, xembedsniproxy, hyprland-dialog) are always left out.\n"
        "Find a window's class with:  hyprctl clients -j | jq '.[].class'",
    ),
    "session_keeping": (
        ["code", "code-oss", "code-insiders", "codium"],
        "Apps that reopen their own windows, so they are launched once per process and\n"
        "sent SIGTERM at shutdown to write their windows down first. Every browser under\n"
        "[chromium-browsers] is one as well. Add your editor if it restores its own windows.",
    ),
    "single_instance": (
        ["soffice"]
        + [
            "libreoffice-" + app
            for app in ("writer", "calc", "impress", "draw", "math", "base", "startcenter")
        ]
        + ["gimp"],
        "Apps launched once for all of their windows, because a second launch joins the\n"
        "first, but never signalled: a SIGTERM raises a save prompt, and they come back\n"
        "offering document recovery instead of the document.",
    ),
    "tui_programs": (
        ["yazi", "nvim", "vim", "btop", "htop", "ranger", "lf"],
        "Programs relaunched inside their terminal, in the directory they were in.",
    ),
    "shells": (
        ["fish", "zsh", "bash", "sh", "nu"],
        "What a terminal runs at its prompt. A terminal at a prompt reopens in that shell's directory.",
    ),
    "state_dir": ("", "Where snapshots are kept. Empty for ~/.local/state/omarchy-last-session."),
    "settle_delay": (
        10,
        "Seconds vanished windows stay unsaved. Omarchy powers off two seconds after\n"
        "closing every window, and this outlasts that, so a half-closed desktop is never saved.",
    ),
    "save_interval": (
        60,
        "Seconds between saves when no window has opened, closed or moved. Titles and\n"
        "floating geometry change without such an event.",
    ),
    "sweep_timeout": (
        30,
        "Seconds restore waits for the windows it launched. One slower than this is left unplaced.",
    ),
    "title_settle": (
        5,
        "Seconds a browser window still loading may take to show its title, which says\n"
        "which saved window it is.",
    ),
    "max_preexisting_windows": (
        3,
        "Restore does nothing when more windows than this are already open, so enabling\n"
        "the plugin mid-session reopens nothing.",
    ),
}


# The daemon wakes every save_interval; at 0 it would save in a tight loop.
MINIMUMS = {"save_interval": 1}
# Past 2**63 nanoseconds the wait itself overflows (select and sleep) and the
# daemon would die outside its retry; a year is plenty.
MAX_SECONDS = 10**9


def unquote(text):
    """Without the pair of quotes users write around a value, which INI has no use for."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def parse_terminal(text):
    """(binary, cwd flag, exec flag or None) from 'binary, flag[, flag]'; None otherwise."""
    parts = [unquote(part) for part in text.split(",")]
    while parts and not parts[-1]:
        parts.pop()
    if len(parts) not in (2, 3) or not all(parts):
        return None
    return (parts[0], parts[1], parts[2] if len(parts) == 3 else None)


def parse_profile_dir(text):
    """Under ~/.config unless absolute; ~ is expanded so it does not end up under it."""
    return os.path.expanduser(unquote(text)) or None


# Sections holding a table: name -> (default rows, comment, parser of one row's value).
# A section in the file replaces the whole table.
TABLES = {
    "terminals": (
        {
            "kitty": ("kitty", "-d", None),
            "foot": ("foot", "-D", None),
            "Alacritty": ("alacritty", "--working-directory", "-e"),
            "com.mitchellh.ghostty": ("ghostty", "--working-directory=", "-e"),
        },
        "Terminals, by window class: the binary, the flag that sets the working directory,\n"
        "and the flag that runs a program in it, when the terminal has one. A directory flag\n"
        "ending in = is joined to its value, which is the only form ghostty accepts.",
        parse_terminal,
    ),
    "chromium-browsers": (
        {
            # XWayland reports Chromium; native Wayland chromium or chromium-browser.
            "brave-browser": "BraveSoftware/Brave-Browser",
            "Chromium": "chromium",
            "chromium": "chromium",
            "chromium-browser": "chromium",
            "google-chrome": "google-chrome",
            "microsoft-edge": "microsoft-edge",
        },
        "Chromium-based browsers, by window class: the profile directory under ~/.config.\n"
        "Each is relaunched with --restore-last-session after its profiles are marked as\n"
        "cleanly exited, since Chromium restores no tabs after what it took for a crash.",
        parse_profile_dir,
    ),
}
DEFAULTS = {key: default for key, (default, _) in SETTINGS.items()}
DEFAULTS.update({name: rows for name, (rows, _, _) in TABLES.items()})

# hyprland-dialog is the compositor's own, such as its "not responding" prompt.
BUILTIN_EXCLUDE_CLASSES = frozenset({"org.quickshell", "xembedsniproxy", "hyprland-dialog"})
KITTY_CLASS = "kitty"
KITTY_SESSION_FLAG = "--session"
RESTORE_FLAG = "--restore-last-session"
# yazi's directory file and LibreOffice's splash descriptor: named per run.
PER_RUN_ARG_PREFIXES = ("--cwd-file", "--splash-pipe")

# An AppImage mounts under a fresh random suffix on every launch, and prefixes
# the window class; its .desktop entry is not prefixed.
APPIMAGE_MOUNT_PREFIX = "/tmp/.mount_"
CLASS_PREFIXES = ("appimagekit-",)
# A service invocation, not a way to reopen the window.
DBUS_SERVICE_FLAG = "--gapplication-service"
DESKTOP_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")
DESKTOP_DIRS = (
    "~/.local/share/applications",
    "/usr/local/share/applications",
    "/usr/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
    "/var/lib/flatpak/exports/share/applications",
)

GRACEFUL_QUIT_TIMEOUT = 10
DAEMON_INITIAL_DELAY = 90
SPAWN_STAGGER = 0.3


def load_file():
    """The config file over DEFAULTS; None for a file that cannot be read at all.
    A key that is not a setting or a value that cannot be one is reported and skipped."""
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.optionxform = str  # window classes are case-sensitive: Alacritty, Chromium
    try:
        # utf-8-sig: an editor's byte order mark would otherwise hide the first line.
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            parser.read_file(f)
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError, configparser.Error) as e:
        warn(f"ignoring {CONFIG_FILE}: {e}")
        return None
    settings = dict(DEFAULTS)
    for section in parser.sections():
        name = section.lower()
        if name == SECTION:
            read_settings(parser.items(section), settings)
        elif name in TABLES:
            settings[name] = read_table(name, parser.items(section))
        else:
            warn(f"{CONFIG_FILE}: no section named [{section}]")
    return settings


def read_settings(items, settings):
    for written, text in items:
        key = written.lower()
        if key not in SETTINGS:
            warn(f"{CONFIG_FILE}: no setting named {written!r}")
            continue
        value = parse_value(key, text)
        if value is None:
            lowest = MINIMUMS.get(key, 0)
            warn(
                f"{CONFIG_FILE}: {key} must be a number from {lowest} to {MAX_SECONDS}; using {DEFAULTS[key]}"
            )
        else:
            settings[key] = value


def read_table(section, items):
    _, _, parse = TABLES[section]
    rows = {}
    for key, text in items:
        value = parse(text)
        if value is None:
            warn(f"{CONFIG_FILE}: [{section}] {key} = {text!r} is not a usable entry, skipped")
        else:
            rows[key] = value
    return rows


def parse_value(key, text):
    """What the text stands for, in the default's type; None for a number it cannot be."""
    default = DEFAULTS[key]
    if isinstance(default, list):
        return [unquote(item) for item in re.split(r"[,\n]", text) if unquote(item)]
    if isinstance(default, str):
        return unquote(text)
    return parse_number(text, MINIMUMS.get(key, 0))


def parse_number(text, lowest):
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or not lowest <= number <= MAX_SECONDS:
        return None
    return int(number) if number.is_integer() else number


def apply(settings):
    """Sets the knobs the other modules read."""
    global STATE_DIR, SESSION_FILE, DISABLE_FLAG, LAST_SHUTDOWN_FILE, LAST_RESTORE_FILE
    global KITTY_SESSION_FILE, KITTY_SESSION_GLOB, EXCLUDE_CLASSES, TUI_PROGRAMS, SHELLS
    global TERMINALS, CHROMIUM_BROWSERS, RESTORE_FLAGS, SESSION_KEEPING_CLASSES, SINGLE_INSTANCE_CLASSES
    global SETTLE_DELAY, SAVE_INTERVAL, SWEEP_TIMEOUT, TITLE_SETTLE, MAX_PREEXISTING_WINDOWS
    # A relative path is taken from home, so every command resolves it alike whatever its cwd.
    state_dir = os.path.expanduser(settings["state_dir"])
    STATE_DIR = os.path.join(HOME, state_dir) if state_dir else DEFAULT_STATE_DIR
    SESSION_FILE = os.path.join(STATE_DIR, "session.json")
    DISABLE_FLAG = os.path.join(STATE_DIR, "disabled")
    # The daemon overwrites session.json soon after the next login; these two do not.
    LAST_SHUTDOWN_FILE = os.path.join(STATE_DIR, "last-shutdown.json")
    LAST_RESTORE_FILE = os.path.join(STATE_DIR, "last-restore.json")
    KITTY_SESSION_FILE = os.path.join(STATE_DIR, "kitty-{pid}.session")
    KITTY_SESSION_GLOB = os.path.join(STATE_DIR, "kitty-*.session")
    EXCLUDE_CLASSES = BUILTIN_EXCLUDE_CLASSES | frozenset(settings["exclude"])
    TUI_PROGRAMS = frozenset(settings["tui_programs"])
    SHELLS = frozenset(settings["shells"])
    TERMINALS = dict(settings["terminals"])
    CHROMIUM_BROWSERS = dict(settings["chromium-browsers"])
    RESTORE_FLAGS = dict.fromkeys(CHROMIUM_BROWSERS, RESTORE_FLAG)
    # Sent SIGTERM at shutdown so they write their windows down.
    SESSION_KEEPING_CLASSES = frozenset(CHROMIUM_BROWSERS) | frozenset(settings["session_keeping"])
    # Launched once per process: a second launch joins the instance already running.
    SINGLE_INSTANCE_CLASSES = SESSION_KEEPING_CLASSES | frozenset(settings["single_instance"])
    SETTLE_DELAY = settings["settle_delay"]
    SAVE_INTERVAL = settings["save_interval"]
    SWEEP_TIMEOUT = settings["sweep_timeout"]
    TITLE_SETTLE = settings["title_settle"]
    MAX_PREEXISTING_WINDOWS = settings["max_preexisting_windows"]


_loaded_stamp = None
_in_force = dict(DEFAULTS)


def get_file_stamp():
    """What has to differ for the file to count as edited; None without a file."""
    try:
        st = os.stat(CONFIG_FILE)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def reload():
    """A file that cannot be read leaves what was in force, so a half-saved
    edit does not drop the daemon to the defaults."""
    global _loaded_stamp, _in_force
    _loaded_stamp = get_file_stamp()
    settings = load_file()
    if settings is not None:
        _in_force = settings
    apply(_in_force)


def reload_if_changed():
    """True when the file was edited since it was last read, in which case it
    has been read again: the daemon follows an edit without a restart."""
    if get_file_stamp() == _loaded_stamp:
        return False
    reload()
    return True


def render_template():
    """The file as the plugin writes it: every default, with a comment on each."""
    lines = [HEADER, f"[{SECTION}]"]
    for key, (default, doc) in SETTINGS.items():
        lines += [""] + render_comment(doc) + [render_entry(key, default)]
    for name, (rows, doc, _) in TABLES.items():
        lines += ["", f"[{name}]"] + render_comment(doc)
        lines += [render_entry(key, row) for key, row in rows.items()]
    return "\n".join(lines + ["", FOOTER])


def render_comment(doc):
    return ["# " + line for line in doc.split("\n")]


def render_entry(key, value):
    if isinstance(value, (list, tuple)):
        value = ", ".join(part for part in value if part)
    return f"{key} = {value}".rstrip()


def write_defaults():
    """Refuses to touch an existing file."""
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "x", encoding="utf-8") as f:
        f.write(render_template())


def ensure_file():
    """Written once, the first time the plugin runs, so there is a commented
    file to open from the menu."""
    global _loaded_stamp
    if os.path.exists(CONFIG_FILE):
        return
    try:
        write_defaults()
    except FileExistsError:
        pass  # another command of the plugin got there first, with the same file
    except OSError as e:
        warn(f"could not write {CONFIG_FILE}: {e}")
        return
    # The template holds the defaults already in force, so nothing is applied;
    # only the stamp moves, or the daemon would take its own write for an edit.
    _loaded_stamp = get_file_stamp()


reload()
