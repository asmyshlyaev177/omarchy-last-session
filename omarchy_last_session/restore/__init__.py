"""The restore pass: launch, sweep, group, then name workspaces, place them on monitors
and show on each monitor the workspace it showed."""

from __future__ import annotations

import contextlib
import os

from omarchy_last_session import config, hypr, notification, session, warn
from omarchy_last_session.restore import launch, layout, sweep
from omarchy_last_session.restore.pairing import Pairing

TOAST_SUMMARY = "Restoring last session\u2026"
# nf-md-window_restore, from the Nerd Font Omarchy draws its toast glyphs with.
TOAST_GLYPH = "\U000f05b2"


def read_recorded_instance() -> str | None:
    """The signature restore --login last ran under; None when there is none to read."""
    try:
        with session.open_private(config.RESTORED_INSTANCE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        warn(f"cannot read {config.RESTORED_INSTANCE_FILE}: {e}")
        return None


def record_instance(signature: str) -> None:
    try:
        session.write_private(config.RESTORED_INSTANCE_FILE, signature)
    except OSError as e:
        warn(f"cannot write {config.RESTORED_INSTANCE_FILE}: {e}")


def claim_instance() -> bool:
    """Records this Hyprland instance for restore; False when restore already ran under it."""
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "").strip()
    if not signature:
        return True
    if read_recorded_instance() == signature:
        warn(
            "restore already ran in this Hyprland session, skipping; "
            "run restore without --login to reopen the session anyway"
        )
        return False
    # Recorded before the disabled and populated checks: no shell start after the first is a login.
    record_instance(signature)
    return True


def restore_session(is_login: bool = False) -> None:
    if is_login and not claim_instance():
        return
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
    with show_toast(windows):
        layout.hold_workspaces(windows)
        try:
            restore_windows(windows, already_open)
        finally:
            layout.release_workspaces()


def restore_windows(windows: list[session.SavedWindow], already_open: dict[str, hypr.Client]) -> None:
    origins = hypr.get_monitor_origins()
    ordered = launch.sort_for_launch(windows)
    launch.launch_saved_windows(ordered, origins, {c.get("class", "") for c in already_open.values()})
    placed, missing = sweep.sweep(ordered, set(already_open), origins)
    for win in missing:
        warn(describe_missing(win, placed, missing))
    layout.build_groups(placed)
    layout.name_workspaces(windows)
    layout.place_workspaces_on_monitors(windows)
    layout.show_saved_workspaces(windows)


def show_toast(windows: list[session.SavedWindow]) -> contextlib.AbstractContextManager[None]:
    count = len(windows)
    # Every launch, the sweep waited out, and a moment for the grouping and
    # moves after it: the toast outlives the pass only when restore is killed.
    seconds = count * config.SPAWN_STAGGER + config.SWEEP_TIMEOUT + 5
    body = f"Reopening {count} window{'' if count == 1 else 's'}"
    return notification.showing(TOAST_SUMMARY, body, TOAST_GLYPH, seconds)


def describe_missing(
    win: session.SavedWindow, placed: list[Pairing], missing: list[session.SavedWindow]
) -> str:
    cls, workspace = win["class"], win["workspace"].get("id")
    line = f"no window turned up for {cls} '{win.get('title', '')}' from workspace {workspace}"
    reopened = sum(entry["class"] == cls for entry, _ in placed)
    if cls not in config.SESSION_KEEPING_CLASSES or not reopened:
        return line
    # Running and back with its other windows: its own session dropped this one,
    # as Chromium does with a window Omarchy's power menu closes before it quits.
    total = reopened + sum(entry["class"] == cls for entry in missing)
    return f"{line}; {cls} reopened {reopened} of its {total} windows"
