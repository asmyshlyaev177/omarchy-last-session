"""Reading and replacing files where another process may plant a symlink or a
FIFO, such as a browser profile a sandboxed app can write to. What is planted
is refused, never followed."""

from __future__ import annotations

import os
import stat
import tempfile
from typing import TextIO


def open_regular_file(path: str) -> TextIO:
    # Without O_NONBLOCK, opening a FIFO would wait for a writer before the check could refuse it.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path} is not a regular file")
    return os.fdopen(fd, encoding="utf-8")


def replace_file(path: str, text: str, mode: int) -> None:
    """Atomically, through a temp file beside path that mkstemp creates
    exclusively, so a symlink planted under its name is skipped."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Through the descriptor: the name could be swapped for a symlink after mkstemp.
            os.fchmod(f.fileno(), mode)
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
