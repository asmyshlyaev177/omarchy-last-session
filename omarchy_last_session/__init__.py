"""Reopen the last session's windows on login: an Omarchy shell plugin for Hyprland 0.55+."""

from __future__ import annotations

import sys


# Both streams are pipes to the shell, and Python block-buffers a piped stdout:
# without the flush the daemon's lines sit unseen until it exits.
def log(message: str) -> None:
    print(f"omarchy-last-session: {message}", flush=True)


def warn(message: str) -> None:
    print(f"omarchy-last-session: {message}", file=sys.stderr, flush=True)
