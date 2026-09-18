"""Knobs. Environment variables are read once, at import."""

import os
import re

STATE_DIR = os.environ.get("OMARCHY_LAST_SESSION_DIR") or os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "omarchy-last-session"
)
SESSION_FILE = os.path.join(STATE_DIR, "session.json")
DISABLE_FLAG = os.path.join(STATE_DIR, "disabled")
# The daemon overwrites session.json shortly after the next login; this copy
# still says what was open at shutdown.
LAST_SHUTDOWN_FILE = os.path.join(STATE_DIR, "last-shutdown.json")

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

# Without it Chromium calls the kill a crash and asks instead of restoring.
# XWayland reports Chromium; native Wayland chromium or chromium-browser.
RESTORE_FLAGS = dict.fromkeys(
    ("brave-browser", "Chromium", "chromium", "chromium-browser", "google-chrome", "microsoft-edge"),
    "--restore-last-session",
)

# Apps that reopen their own windows: launched once per process, since a second
# launch of one adds an empty window, and asked to quit at shutdown so they
# write their state down. Add your editor if it restores its own windows.
SESSION_KEEPING_CLASSES = frozenset(RESTORE_FLAGS) | {"code", "code-oss", "code-insiders", "codium"}

GRACEFUL_QUIT_TIMEOUT = 10
SAVE_INTERVAL = 60
DAEMON_INITIAL_DELAY = 90
# The sweep ends as soon as every window is accounted for, so only a window
# that never appears pays the full wait.
SWEEP_TIMEOUT = 30
SPAWN_STAGGER = 0.3
# Restore refuses to run into a desktop with more windows than this open.
MAX_PREEXISTING_WINDOWS = 3
