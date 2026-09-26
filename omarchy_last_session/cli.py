"""omarchy-last-session: reopen your last session's windows on login. Omarchy / Hyprland 0.55+.

save     - snapshot every mapped window (workspace, geometry, relaunch command)
shutdown - save, then let session-keeping apps exit cleanly; optional, for a
           power menu action wired to run it first
restore  - relaunch every saved window silently on its original workspace
daemon   - save when Hyprland reports a window moving, so a power button, a
           crash or the power menu costs a few seconds of changes at most
config   - open the config file in your editor; the daemon picks an edit up
           within a minute
menu     - print the rows for ~/.config/omarchy/extensions/omarchy-menu.jsonc:
           the config file under Setup > Config, and Omarchy's own Logout,
           Reboot and Shutdown rows, each running shutdown first

Config lives in $XDG_CONFIG_HOME/omarchy/last-session.ini, written with every default
and a comment on each the first time the plugin runs.
State lives in $XDG_STATE_HOME/omarchy-last-session, or the state_dir set there.
Skip the next restore:   touch <state dir>/disabled
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable

from omarchy_last_session import config, hypr, log, restore, session, warn

# Omarchy's own: opens the user's editor and shows a toast, as the Config menu entries do.
CONFIG_EDITOR = "omarchy-launch-config-editor"
MENU_ICON = "󰁯"
# Omarchy's own menu, under OMARCHY_PATH or the path Omarchy itself falls back to.
OMARCHY_MENU = "default/omarchy/omarchy-menu.jsonc"
OMARCHY_DEFAULT_PATH = "/usr/share/omarchy"
# What Omarchy's Logout, Reboot and Shutdown rows run. The rows are found by these,
# since their ids, icons and labels are Omarchy's to change.
POWER_COMMANDS = ("omarchy-system-logout", "omarchy-system-reboot", "omarchy-system-shutdown")
# The checkout: where the launcher, the manifest and this package live.
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_save() -> None:
    print(f"saved {session.save_session()} windows to {config.SESSION_FILE}")


def run_shutdown() -> None:
    print(f"saved {session.save_session()} windows")
    try:
        session.copy_session_to(config.LAST_SHUTDOWN_FILE)
    except OSError as e:
        warn(f"could not keep a copy: {e}")
    stubborn = session.quit_session_keeping_apps()
    if stubborn:
        print(f"{len(stubborn)} app(s) did not exit in time")
    else:
        print("session-keeping apps exited cleanly")


def run_daemon() -> None:
    time.sleep(config.DAEMON_INITIAL_DELAY)
    scheduler = session.SaveScheduler()
    events = hypr.open_event_stream()
    while hypr.wait_for_placement_change(events, save_if_due(scheduler)):
        pass


def run_config() -> int:
    try:
        os.execvp(CONFIG_EDITOR, [CONFIG_EDITOR, config.CONFIG_FILE])
    except FileNotFoundError:
        warn(f"{CONFIG_EDITOR} is not on PATH, so open the file yourself: {config.CONFIG_FILE}")
        return 1


def run_menu() -> int:
    path = os.path.join(os.environ.get("OMARCHY_PATH") or OMARCHY_DEFAULT_PATH, OMARCHY_MENU)
    try:
        power_rows = read_power_rows(path)
    except (OSError, ValueError) as e:
        warn(f"could not read Omarchy's menu {path}: {e}")
        return 1
    if not power_rows:
        warn(f"no row in Omarchy's menu {path} runs {', '.join(POWER_COMMANDS)}")
        return 1
    print(render_menu_rows(tilde(PLUGIN_DIR), power_rows), end="")
    return 0


def read_power_rows(path: str) -> dict[str, dict[str, object]]:
    """Omarchy's rows that run a power command, by id, read as its menu reads
    them: whole-line comments and trailing commas dropped."""
    with open(path, encoding="utf-8") as f:
        text = re.sub(r"^\s*//[^\n]*(\n|$)", "", f.read(), flags=re.MULTILINE)
    rows: object = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
    if not isinstance(rows, dict):
        raise ValueError("not a JSON object")
    return {
        row_id: row
        for row_id, row in rows.items()
        if isinstance(row, dict) and row.get("action") in POWER_COMMANDS
    }


def render_menu_rows(root: str, power_rows: dict[str, dict[str, object]]) -> str:
    """JSONC rows for the user's menu file. The config row hides itself while
    the plugin directory is gone: the directory outlives any move of the
    launcher inside it. The power rows replace Omarchy's own, so they never
    hide; without the plugin they skip straight to powering off."""
    launcher = f"{root}/bin/omarchy-last-session"
    rows: dict[str, dict[str, object]] = {
        "setup.config.last-session": {
            "icon": MENU_ICON,
            "label": "Last Session",
            "when": f"[[ -d {root} ]]",
            "action": f"{launcher} config",
        }
    }
    for row_id, row in power_rows.items():
        # Every other field is copied: Omarchy's menu fills in whatever a row leaves
        # out, with no icon and the row's id as its label.
        rows[row_id] = {**row, "action": f"[[ -x {launcher} ]] && {launcher} shutdown; {row['action']}"}
    return "".join(f"  {format_json(row_id)}: {format_json(row)},\n" for row_id, row in rows.items())


def format_json(value: object) -> str:
    """On one line and with the icons as they are, as Omarchy writes its menu."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def tilde(path: str) -> str:
    """~ for the home directory, which the menu's bash expands, so the rows read
    the same on every machine."""
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path == home or path.startswith(home + os.sep) else path


def save_if_due(scheduler: session.SaveScheduler) -> float:
    """Returns how long the daemon may sleep before looking again."""
    try:
        if config.reload_if_changed():
            log(f"reloaded {config.CONFIG_FILE}")
        now = time.monotonic()
        if scheduler.is_due(hypr.get_layout(), now):
            session.save_session()
            scheduler.mark_saved(now)
        return scheduler.seconds_until_recheck(time.monotonic())
    except Exception as e:
        warn(f"save failed: {e}")
        return config.SAVE_INTERVAL


COMMANDS: dict[str, Callable[[], int | None]] = {
    "save": run_save,
    "shutdown": run_shutdown,
    "restore": restore.restore_session,
    "daemon": run_daemon,
    "config": run_config,
    "menu": run_menu,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = COMMANDS.get(args[0]) if args else None
    if command is None:
        print((__doc__ or "").strip(), file=sys.stderr)
        return 1
    config.ensure_file()
    return command() or 0
