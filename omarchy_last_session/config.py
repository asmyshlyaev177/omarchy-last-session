"""Knobs. Environment variables are read once, at import."""

import os
import re

STATE_DIR = os.environ.get("OMARCHY_LAST_SESSION_DIR") or os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "omarchy-last-session"
)
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
SESSION_FILE = os.path.join(STATE_DIR, "session.json")
DISABLE_FLAG = os.path.join(STATE_DIR, "disabled")
# The daemon overwrites session.json soon after the next login; these two do not.
LAST_SHUTDOWN_FILE = os.path.join(STATE_DIR, "last-shutdown.json")
LAST_RESTORE_FILE = os.path.join(STATE_DIR, "last-restore.json")
KITTY_SESSION_FILE = os.path.join(STATE_DIR, "kitty-{pid}.session")
KITTY_SESSION_GLOB = os.path.join(STATE_DIR, "kitty-*.session")

# hyprland-dialog is the compositor's own, such as its "not responding" prompt.
EXCLUDE_CLASSES = {"org.quickshell", "xembedsniproxy", "hyprland-dialog"}
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
KITTY_CLASS = "kitty"
KITTY_SESSION_FLAG = "--session"
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

# class -> user data directory under CONFIG_HOME. XWayland reports Chromium;
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

# Sent SIGTERM at shutdown so they write their windows down. Add your editor if
# it restores its own windows.
SESSION_KEEPING_CLASSES = frozenset(RESTORE_FLAGS) | {"code", "code-oss", "code-insiders", "codium"}
LIBREOFFICE_CLASSES = {"soffice"} | {
    "libreoffice-" + app for app in ("writer", "calc", "impress", "draw", "math", "base", "startcenter")
}
# Launched once per process: a second launch joins the instance already running.
# A SIGTERM to LibreOffice or GIMP raises a save prompt, so neither is sent one.
SINGLE_INSTANCE_CLASSES = SESSION_KEEPING_CLASSES | LIBREOFFICE_CLASSES | {"gimp"}

GRACEFUL_QUIT_TIMEOUT = 10
DAEMON_INITIAL_DELAY = 90
# SETTLE_DELAY outlasts the two seconds between the power menu closing every
# window and the poweroff, so a half-closed desktop is never saved.
SETTLE_DELAY = 10
SAVE_INTERVAL = 60
SWEEP_TIMEOUT = 30
TITLE_SETTLE = 5
SPAWN_STAGGER = 0.3
MAX_PREEXISTING_WINDOWS = 3
