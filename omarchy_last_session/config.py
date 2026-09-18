"""Knobs. Environment variables are read once, at import."""

import os
import re

STATE_DIR = os.environ.get("OMARCHY_LAST_SESSION_DIR") or os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "omarchy-last-session"
)
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
SESSION_FILE = os.path.join(STATE_DIR, "session.json")
DISABLE_FLAG = os.path.join(STATE_DIR, "disabled")
# The daemon overwrites session.json shortly after the next login; this copy
# still says what was open at shutdown.
LAST_SHUTDOWN_FILE = os.path.join(STATE_DIR, "last-shutdown.json")
# What restore brought back, kept because the daemon overwrites session.json
# soon after login.
LAST_RESTORE_FILE = os.path.join(STATE_DIR, "last-restore.json")

# Never saved or restored: add whatever your autostart already launches.
EXCLUDE_CLASSES = {"org.quickshell", "xembedsniproxy"}
EXCLUDE_CLASSES |= {
    c.strip() for c in os.environ.get("OMARCHY_LAST_SESSION_EXCLUDE", "").split(",") if c.strip()
}

# class -> (binary, cwd flag, exec flag). A cwd flag ending in "=" is joined to
# its value: ghostty silently ignores the separated form.
TERMINALS = {
    "kitty": ("kitty", "-d", None),
    "foot": ("foot", "-D", None),
    "Alacritty": ("alacritty", "--working-directory", "-e"),
    "com.mitchellh.ghostty": ("ghostty", "--working-directory=", "-e"),
}
TUI_PROGRAMS = {"yazi", "nvim", "vim", "btop", "htop", "ranger", "lf"}
SHELLS = {"fish", "zsh", "bash", "sh", "nu"}

# An AppImage mounts under a fresh random suffix on every launch.
APPIMAGE_MOUNT_PREFIX = "/tmp/.mount_"
# AppImages prefix the window class; their .desktop entry is not prefixed.
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

# Chromium-based browsers: class -> user data directory under CONFIG_HOME.
# Each is relaunched with --restore-last-session, and its profiles are marked
# as cleanly exited first: Chromium refuses to restore after a crash, and a
# browser the power menu killed has recorded one. XWayland reports Chromium;
# native Wayland chromium or chromium-browser.
CHROMIUM_BROWSERS = {
    "brave-browser": "BraveSoftware/Brave-Browser",
    "Chromium": "chromium",
    "chromium": "chromium",
    "chromium-browser": "chromium",
    "google-chrome": "google-chrome",
    "microsoft-edge": "microsoft-edge",
}
RESTORE_FLAGS = dict.fromkeys(CHROMIUM_BROWSERS, "--restore-last-session")

# Apps that reopen their own windows: launched once per process, since a second
# launch of one adds an empty window, and asked to quit at shutdown so they
# write their state down. Add your editor if it restores its own windows.
SESSION_KEEPING_CLASSES = frozenset(RESTORE_FLAGS) | {"code", "code-oss", "code-insiders", "codium"}

GRACEFUL_QUIT_TIMEOUT = 10
DAEMON_INITIAL_DELAY = 90
# The daemon sleeps on Hyprland's event socket and saves as soon as a window
# appears or moves. Windows that vanished are saved only once the desktop has
# been still for SETTLE_DELAY, which outlasts the two seconds between the power
# menu closing every window and the poweroff. Titles and floating geometry
# change without any event, so it also saves every SAVE_INTERVAL.
SETTLE_DELAY = 10
SAVE_INTERVAL = 60
# The sweep ends as soon as every window is accounted for, so only a window
# that never appears pays the full wait.
SWEEP_TIMEOUT = 30
# A browser titles a window Untitled, New Tab or about:blank until its page
# has loaded. Such a window is paired once its title says what it shows, or
# after TITLE_SETTLE seconds.
TITLE_SETTLE = 5
SPAWN_STAGGER = 0.3
# Restore refuses to run into a desktop with more windows than this open.
MAX_PREEXISTING_WINDOWS = 3
