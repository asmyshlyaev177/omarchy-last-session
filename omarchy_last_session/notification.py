"""Omarchy's toasts, sent and taken down through its own commands, which reach the shell that draws them."""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Iterator

# Not omarchy-action, the default, which shows through do-not-disturb as the
# answer to something the user just did.
APP_NAME = "omarchy-last-session"
# The longest a hung shell holds up the end of restore.
SEND_WAIT = 2
# omarchy-shell gives up on its IPC call after two seconds and kills it one later.
DISMISS_WAIT = 5


@contextlib.contextmanager
def showing(summary: str, body: str, glyph: str, seconds: float) -> Iterator[None]:
    """A toast up while the block runs, or for `seconds` should restore be killed
    first. Neither command is waited on at the start, and a missing one is skipped."""
    milliseconds = str(round(seconds * 1000))
    sender = start_quietly(
        ["omarchy-notification-send", "--app-name", APP_NAME, "-g", glyph, "-t", milliseconds, summary, body]
    )
    try:
        yield
    finally:
        # The shell takes down what is on screen under that summary, so the
        # toast has to have arrived first.
        wait_or_kill(sender, SEND_WAIT)
        # A freedesktop CloseNotification leaves the shell's card up for its
        # whole lifetime; only the shell's own dismissal takes it down.
        wait_or_kill(start_quietly(["omarchy-notification-dismiss", summary]), DISMISS_WAIT)


def start_quietly(argv: list[str]) -> subprocess.Popen[bytes] | None:
    """The process, or None when the command is missing. Its output would land in restore's."""
    try:
        return subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        return None


def wait_or_kill(process: subprocess.Popen[bytes] | None, seconds: float) -> None:
    if process is None:
        return
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
