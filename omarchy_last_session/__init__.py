"""Reopen the last session's windows on login: an Omarchy shell plugin for Hyprland 0.55+."""

import sys


def log(message):
    """What the plugin did, on stdout. The shell service forwards it to the journal."""
    print(f"omarchy-last-session: {message}")


def warn(message):
    """What went wrong, on stderr, so a run with nothing wrong writes none."""
    print(f"omarchy-last-session: {message}", file=sys.stderr)
