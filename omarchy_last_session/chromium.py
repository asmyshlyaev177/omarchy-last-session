"""Chromium will not restore its tabs after an unclean exit, and a browser the
power menu killed has recorded one. Marking its profiles as cleanly exited
before the relaunch lets --restore-last-session work; the session file itself
survives the kill."""

from __future__ import annotations

import glob
import json
import os
import shlex
from typing import Any

from omarchy_last_session import config, files, warn

USER_DATA_DIR_FLAG = "--user-data-dir="


def mark_clean_exit(cls: str, cmd: str) -> int:
    user_data_dir = find_user_data_dir(cls, cmd)
    if user_data_dir is None:
        return 0
    return sum(
        set_exit_type_normal(path) for path in glob.glob(os.path.join(user_data_dir, "*", "Preferences"))
    )


def find_user_data_dir(cls: str, cmd: str) -> str | None:
    """The --user-data-dir the window was launched with, else the default."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = []
    flagged = [token[len(USER_DATA_DIR_FLAG) :] for token in tokens if token.startswith(USER_DATA_DIR_FLAG)]
    path = flagged[-1] if flagged else os.path.join(config.CONFIG_HOME, config.CHROMIUM_BROWSERS[cls])
    return path if os.path.isdir(path) else None


def set_exit_type_normal(path: str) -> bool:
    try:
        with files.open_regular_file(path) as f:
            prefs: dict[str, Any] = json.load(f)
            mode = os.fstat(f.fileno()).st_mode & 0o777
        profile = prefs.setdefault("profile", {})
        if profile.get("exit_type") == "Normal":
            return False
        profile["exit_type"] = "Normal"
        files.replace_file(path, json.dumps(prefs, ensure_ascii=False), mode)
        return True
    except (OSError, ValueError, AttributeError) as e:
        warn(f"could not mark {path} as cleanly exited: {e}")
        return False
