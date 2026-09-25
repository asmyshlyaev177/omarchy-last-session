"""Launching the saved windows, each with exec rules for its workspace, floating geometry and pin."""

import time

from omarchy_last_session import chromium, config, hypr, log
from omarchy_last_session.restore import placement


def sort_for_launch(windows):
    """Workspace by workspace, tiled before floating, left to right: the
    closest Hyprland's tiling gets to the old layout."""
    return sorted(windows, key=lambda w: (w["workspace"].get("id", 0), w["floating"], w["at"][0], w["at"][1]))


def launch_saved_windows(windows, origins, running):
    """Everything is launched before anything is waited for, so a slow app
    overlaps with the rest instead of holding up the queue."""
    for win in windows:
        if not win["spawn"]:
            continue
        if win["class"] in config.CHROMIUM_BROWSERS and win["class"] not in running:
            chromium.mark_clean_exit(win["class"], win["cmd"])
        rules = build_exec_rules(win, origins)
        hypr.dispatch(f"hl.dsp.exec_cmd({hypr.quote_lua_long(win['cmd'])}, {rules})")
        log(f"launched {win['class']} onto workspace {hypr.format_workspace_selector(win['workspace'])}")
        time.sleep(config.SPAWN_STAGGER)


def build_exec_rules(win, origins):
    selector = hypr.format_workspace_selector(win["workspace"]) + " silent"
    rules = [f"workspace = {hypr.quote_lua(selector)}"]
    if win["floating"]:
        x, y = placement.get_saved_offset(win, origins)
        rules += ["float = true", f"move = {{{x}, {y}}}", f"size = {{{win['size'][0]}, {win['size'][1]}}}"]
    if win["pinned"]:
        rules.append("pin = true")
    return "{ " + ", ".join(rules) + " }"
