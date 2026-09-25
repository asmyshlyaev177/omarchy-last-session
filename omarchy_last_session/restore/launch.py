"""Launching the saved windows, each with exec rules for its workspace, floating geometry and pin."""

import time

from omarchy_last_session import chromium, config, hypr, log
from omarchy_last_session.restore import pairing, placement


def sort_for_launch(windows):
    """Browsers first, then workspace by workspace, tiled before floating, left
    to right: the closest Hyprland's tiling gets to the old layout."""
    return sorted(
        windows,
        key=lambda w: (
            w["class"] not in config.CHROMIUM_BROWSERS,
            w["workspace"].get("id", 0),
            w["floating"],
            w["at"][0],
            w["at"][1],
        ),
    )


def launch_saved_windows(windows, origins, running):
    """Everything is launched before anything is waited for, so a slow app
    overlaps with the rest instead of holding up the queue. The exception is a
    web app of a browser still starting: launched first, it would start the
    browser without --restore-last-session, and Chromium ignores that flag once
    it is running, opening a new tab instead of the session."""
    starting = set()
    for win in windows:
        if not win["spawn"]:
            continue
        program = pairing.get_program_name(win["cmd"])
        if program in starting:
            starting.remove(program)
            wait_for_program(program)
        if win["class"] in config.CHROMIUM_BROWSERS and win["class"] not in running:
            chromium.mark_clean_exit(win["class"], win["cmd"])
            starting.add(program)
        rules = build_exec_rules(win, origins)
        hypr.dispatch(f"hl.dsp.exec_cmd({hypr.quote_lua_long(win['cmd'])}, {rules})")
        log(f"launched {win['class']} onto workspace {hypr.format_workspace_selector(win['workspace'])}")
        time.sleep(config.SPAWN_STAGGER)


def wait_for_program(program):
    """Until a window of `program` maps, which means its process is up and
    takes further launches as its own, or BROWSER_START_TIMEOUT passes. The
    browser was not running, so any such window is the one just launched, or a
    web app of it that was already open and already takes them."""
    deadline = time.time() + config.BROWSER_START_TIMEOUT
    while time.time() < deadline:
        for client in hypr.get_managed_clients().values():
            if pairing.get_program_name_of(pairing.get_client_argv(client)) == program:
                return True
        time.sleep(0.25)
    log(f"{program} opened no window in {config.BROWSER_START_TIMEOUT} s, launching its web apps anyway")
    return False


def build_exec_rules(win, origins):
    selector = hypr.format_workspace_selector(win["workspace"]) + " silent"
    rules = [f"workspace = {hypr.quote_lua(selector)}"]
    if win["floating"]:
        x, y = placement.get_saved_offset(win, origins)
        rules += ["float = true", f"move = {{{x}, {y}}}", f"size = {{{win['size'][0]}, {win['size'][1]}}}"]
    if win["pinned"]:
        rules.append("pin = true")
    return "{ " + ", ".join(rules) + " }"
