"""omarchy-last-session: reopen your last session's windows on login. Omarchy / Hyprland 0.55+.

save     - snapshot every mapped window (workspace, geometry, relaunch command)
shutdown - save, then let session-keeping apps exit cleanly; run it from the
           power menu in place of save
restore  - relaunch every saved window silently on its original workspace
daemon   - run save every SAVE_INTERVAL seconds, for shutdowns that bypass the
           power menu (power button, crashes)

State lives in $XDG_STATE_HOME/omarchy-last-session (override: $OMARCHY_LAST_SESSION_DIR).
Skip the next restore:   touch <state dir>/disabled
Extra excluded classes:  OMARCHY_LAST_SESSION_EXCLUDE="class1,class2"
"""

import shutil
import sys
import time

from omarchy_last_session import config, restore, session, warn


def run_save():
    saved = session.save_session()
    if saved is None:
        print(f"no windows open, kept previous session in {config.SESSION_FILE}")
    else:
        print(f"saved {saved} windows to {config.SESSION_FILE}")


def run_shutdown():
    """An empty desktop is recorded as such: the user closed everything, and
    the daemon's last snapshot must not bring it all back."""
    saved = session.save_session(keep_previous_when_empty=False)
    print(f"saved {saved} windows")
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
    while True:
        try:
            session.save_session()
        except Exception as e:
            warn(f"save failed: {e}")
        time.sleep(config.SAVE_INTERVAL)


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
