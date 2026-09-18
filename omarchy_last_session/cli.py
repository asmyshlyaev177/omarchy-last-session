"""omarchy-last-session: reopen your last session's windows on login. Omarchy / Hyprland 0.55+.

save     - snapshot every mapped window (workspace, geometry, relaunch command)
shutdown - save, then let session-keeping apps exit cleanly; optional, for a
           power menu action wired to run it first
restore  - relaunch every saved window silently on its original workspace
daemon   - save when Hyprland reports a window moving, so a power button, a
           crash or the power menu costs a few seconds of changes at most

State lives in $XDG_STATE_HOME/omarchy-last-session (override: $OMARCHY_LAST_SESSION_DIR).
Skip the next restore:   touch <state dir>/disabled
Extra excluded classes:  OMARCHY_LAST_SESSION_EXCLUDE="class1,class2"
"""

import shutil
import sys
import time

from omarchy_last_session import config, hypr, restore, session, warn


def run_save():
    print(f"saved {session.save_session()} windows to {config.SESSION_FILE}")


def run_shutdown():
    print(f"saved {session.save_session()} windows")
    try:
        shutil.copyfile(config.SESSION_FILE, config.LAST_SHUTDOWN_FILE)
    except OSError as e:
        warn(f"could not keep a copy: {e}")
    stubborn = session.quit_session_keeping_apps()
    if stubborn:
        print(f"{len(stubborn)} app(s) did not exit in time")
    else:
        print("session-keeping apps exited cleanly")


def run_daemon():
    time.sleep(config.DAEMON_INITIAL_DELAY)
    scheduler = session.SaveScheduler()
    events = hypr.open_event_stream()
    while hypr.wait_for_placement_change(events, save_if_due(scheduler)):
        pass


def save_if_due(scheduler):
    """Returns how long the daemon may sleep before looking again."""
    try:
        now = time.monotonic()
        if scheduler.is_due(hypr.get_layout(), now):
            session.save_session()
            scheduler.mark_saved(now)
        return scheduler.seconds_until_recheck(time.monotonic())
    except Exception as e:
        warn(f"save failed: {e}")
        return config.SAVE_INTERVAL


COMMANDS = {
    "save": run_save,
    "shutdown": run_shutdown,
    "restore": restore.restore_session,
    "daemon": run_daemon,
}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    command = COMMANDS.get(args[0]) if args else None
    if command is None:
        print(__doc__.strip(), file=sys.stderr)
        return 1
    command()
    return 0
