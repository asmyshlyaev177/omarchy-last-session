"""Chromium will not restore its tabs after an unclean exit, and a browser the
power menu killed has recorded one. Marking its profiles as cleanly exited
before the relaunch lets --restore-last-session work; the session file itself
survives the kill."""

import glob
import json
import os
import shlex

from omarchy_last_session import config, warn

USER_DATA_DIR_FLAG = "--user-data-dir="


def mark_clean_exit(cls, cmd):
    user_data_dir = find_user_data_dir(cls, cmd)
    if user_data_dir is None:
        return 0
    return sum(
        set_exit_type_normal(path) for path in glob.glob(os.path.join(user_data_dir, "*", "Preferences"))
    )


def find_user_data_dir(cls, cmd):
    """The --user-data-dir the window was launched with, else the default."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = []
    flagged = [token[len(USER_DATA_DIR_FLAG) :] for token in tokens if token.startswith(USER_DATA_DIR_FLAG)]
    path = flagged[-1] if flagged else os.path.join(config.CONFIG_HOME, config.CHROMIUM_BROWSERS[cls])
    return path if os.path.isdir(path) else None


def set_exit_type_normal(path):
    try:
        with open(path, encoding="utf-8") as f:
            prefs = json.load(f)
        profile = prefs.setdefault("profile", {})
        if profile.get("exit_type") == "Normal":
            return False
        profile["exit_type"] = "Normal"
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False)
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, AttributeError) as e:
        warn(f"could not mark {path} as cleanly exited: {e}")
        return False
