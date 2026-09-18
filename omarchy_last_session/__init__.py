"""Reopen the last session's windows on login: an Omarchy shell plugin for Hyprland 0.55+."""

import sys


def warn(message):
    print(f"omarchy-last-session: {message}", file=sys.stderr)
