"""The restore pass: launch, sweep, group, then name workspaces and place them on monitors."""

import os

from omarchy_last_session import config, hypr, session, warn
from omarchy_last_session.restore import launch, layout, sweep


def restore_session():
    if os.path.exists(config.DISABLE_FLAG):
        return
    windows = session.load_session()
    if not windows:
        return
    already_open = hypr.get_managed_clients()
    if len(already_open) > config.MAX_PREEXISTING_WINDOWS:
        warn("session already populated, aborting")
        return
    session.keep_restore_copy()
    origins = hypr.get_monitor_origins()
    ordered = launch.sort_for_launch(windows)
    launch.launch_saved_windows(ordered, origins, {c.get("class") for c in already_open.values()})
    placed, missing = sweep.sweep(ordered, set(already_open), origins)
    for win in missing:
        warn(describe_missing(win, placed, missing))
    layout.build_groups(placed)
    layout.name_workspaces(windows)
    layout.place_workspaces_on_monitors(windows)


def describe_missing(win, placed, missing):
    cls, workspace = win["class"], win["workspace"].get("id")
    line = f"no window turned up for {cls} '{win.get('title', '')}' from workspace {workspace}"
    reopened = sum(entry["class"] == cls for entry, _ in placed)
    if cls not in config.SESSION_KEEPING_CLASSES or not reopened:
        return line
    # Running and back with its other windows: its own session dropped this one,
    # as Chromium does with a window Omarchy's power menu closes before it quits.
    total = reopened + sum(entry["class"] == cls for entry in missing)
    return f"{line}; {cls} reopened {reopened} of its {total} windows"
