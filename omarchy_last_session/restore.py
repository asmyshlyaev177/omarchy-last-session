"""The restore pass: launch, sweep, group, then place workspaces on monitors."""

import difflib
import os
import shlex
import time

from omarchy_last_session import chromium, config, hypr, log, proc, relaunch, session, warn


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
    ordered = sort_for_launch(windows)
    launch_saved_windows(ordered, origins, {c.get("class") for c in already_open.values()})
    placed, missing = sweep(ordered, set(already_open), origins)
    for win in missing:
        warn(f"no window turned up for {win['class']}")
    build_groups(placed)
    place_workspaces_on_monitors(windows)


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
        time.sleep(config.SPAWN_STAGGER)


def build_exec_rules(win, origins):
    selector = hypr.format_workspace_selector(win["workspace"]) + " silent"
    rules = [f"workspace = {hypr.quote_lua(selector)}"]
    if win["floating"]:
        x, y = get_saved_offset(win, origins)
        rules += ["float = true", f"move = {{{x}, {y}}}", f"size = {{{win['size'][0]}, {win['size'][1]}}}"]
    if win["pinned"]:
        rules.append("pin = true")
    return "{ " + ", ".join(rules) + " }"


def get_saved_offset(win, origins):
    """The window's offset into the monitor it was saved on, which is what
    survives that monitor moving in the layout or being unplugged."""
    ox, oy = win.get("monitor_at") or origins.get(win.get("monitor_name")) or (0, 0)
    return win["at"][0] - ox, win["at"][1] - oy


def get_live_offset(client, origins):
    ox, oy = origins.get(client.get("monitor"), (0, 0))
    return client["at"][0] - ox, client["at"][1] - oy


def get_workspace_origin(workspace, origins):
    """Top-left corner of the monitor a workspace is on right now."""
    for live in hypr.query("workspaces"):
        if live.get("id") == workspace.get("id"):
            return origins.get(live.get("monitor"), (0, 0))
    return (0, 0)


def to_absolute_position(win, origin, origins):
    """The saved offset, laid out from origin."""
    x, y = get_saved_offset(win, origins)
    return origin[0] + x, origin[1] + y


def sweep(pending, seen, origins):
    """Place the windows that escaped their exec_cmd rule, which tracks the
    spawned pid and so misses forking and single-instance apps. Returns the
    (entry, address) pairs placed and the entries that never got a window."""
    pending = list(pending)
    placed = []
    first_seen = {}
    deadline = time.time() + config.SWEEP_TIMEOUT
    while pending:
        time.sleep(1)
        now = time.time()
        if now >= deadline:
            break
        placed += place_new_arrivals(pending, seen, origins, first_seen, now, deadline)
    return placed, pending


def place_new_arrivals(pending, seen, origins, first_seen, now, deadline):
    """One pass over the desktop. Updates pending and seen in place."""
    placed = []
    for address, client in hypr.get_managed_clients().items():
        if address in seen:
            continue
        first_seen.setdefault(address, now)
        if is_worth_waiting_for(client, first_seen[address], now, deadline):
            continue
        entry = match_saved_entry(pending, client)
        if entry is None:
            continue
        pending.remove(entry)
        seen.add(address)
        placed.append((entry, address))
        # Moving one member of a group moves the whole group; a group of one
        # is still just a window.
        fixing = len(client.get("grouped") or []) <= 1 and is_out_of_place(entry, client, origins)
        log(describe_pairing(entry, client, fixing))
        if fixing:
            place_window(entry, address, origins, floating=bool(client.get("floating")))
    return placed


def is_worth_waiting_for(client, first_seen_at, now, deadline):
    """A window still titled Untitled or New Tab says nothing about which saved
    window it is, so it gets TITLE_SETTLE seconds to say more."""
    if not is_still_loading(client.get("title", "")):
        return False
    return now < min(first_seen_at + config.TITLE_SETTLE, deadline - 1)


def describe_pairing(entry, client, fixing):
    command, title, _ = score_fit(entry, client, get_client_argv(client))
    return (
        f"{client.get('class', '')} '{client.get('title', '')}' on workspace {client['workspace'].get('id')}"
        f" is the saved '{entry.get('title', '')}' from workspace {entry['workspace'].get('id')}"
        f" (command {command}, title {title:.2f}); {'placing it' if fixing else 'already in place'}"
    )


def match_saved_entry(pending, client):
    """The saved entry for a live window: by class, or failing that by the
    program behind it, since Chromium reports chromium-browser when relaunched
    from a command line rather than from its desktop entry."""
    live_argv = get_client_argv(client)
    candidates = [win for win in pending if win["class"] == client.get("class", "")]
    if not candidates:
        program = get_program_name_of(live_argv)
        candidates = [win for win in pending if program and get_program_name(win.get("cmd", "")) == program]
    if not candidates:
        return None
    return max(candidates, key=lambda win: score_fit(win, client, live_argv))


def score_fit(win, client, live_argv):
    """Command first, so two browsers on different profiles stay apart, then
    title, then workspace. A browser opens its windows wherever it likes, so
    trusting the workspace sooner fills two into each other's places."""
    return (
        score_command_match(win.get("cmd", ""), live_argv),
        score_title_likeness(win.get("title", ""), client.get("title", "")),
        win["workspace"].get("id") == client["workspace"].get("id"),
    )


def score_command_match(cmd, live_argv):
    """2 for the same command line, 1 for the same program, else 0. The restore
    flag is the plugin's own addition and is ignored."""
    try:
        saved = shlex.split(cmd)
    except ValueError:
        saved = []
    if not saved or not live_argv:
        return 0
    ours = set(config.RESTORE_FLAGS.values())
    if set(saved) - ours == set(live_argv) - ours:
        return 2
    return int(os.path.basename(saved[0]) == os.path.basename(live_argv[0]))


TITLE_SEPARATORS = (" - ", " \u2014 ")
# What a browser titles a window until its page has loaded.
PLACEHOLDER_PAGES = frozenset(("untitled", "new tab", "about:blank"))


def score_title_likeness(saved, live):
    """How close two titles are, without the app name both end in. A title that
    is still a placeholder, or missing, scores zero rather than match on the
    app name alone."""
    saved_page, live_page = strip_shared_app_name(saved, live)
    if not saved_page or not live_page or is_placeholder(saved_page) or is_placeholder(live_page):
        return 0.0
    return difflib.SequenceMatcher(None, saved_page, live_page).ratio()


def strip_shared_app_name(saved, live):
    saved_page, saved_app = split_title(saved)
    live_page, live_app = split_title(live)
    if saved_app and live_app == saved_app:
        return saved_page, live_page
    if saved_app and live.strip() == saved_app:
        return saved_page, ""
    return saved.strip(), live.strip()


def split_title(title):
    """('page', 'App') for 'page - App', else the whole title and no app."""
    for separator in TITLE_SEPARATORS:
        page, found, app = title.rpartition(separator)
        if found:
            return page.strip(), app.strip()
    return title.strip(), ""


def is_placeholder(page):
    return page.lower() in PLACEHOLDER_PAGES


def is_still_loading(title):
    return is_placeholder(split_title(title)[0])


def get_program_name(cmd):
    """The executable a command line starts with, without its path: an AppImage
    mounts somewhere new on every launch, and a desktop entry names no path."""
    try:
        return os.path.basename(shlex.split(cmd)[0]).lower()
    except (ValueError, IndexError):
        return ""


def get_program_name_of(argv):
    return os.path.basename(argv[0]).lower() if argv else ""


def get_client_argv(client):
    """The window's process command line, unflattened the way save does."""
    return relaunch.unflatten_argv(proc.read_cmdline(client.get("pid", -1)) or [])


def is_out_of_place(win, client, origins):
    misplaced = client["workspace"].get("id") != win["workspace"].get("id")
    # A window rule may float a window that was saved tiled, as Omarchy's does
    # to Steam; left floating it cannot rejoin its group.
    floating_off = bool(client.get("floating")) != bool(win["floating"])
    geometry_off = win["floating"] and get_live_offset(client, origins) != get_saved_offset(win, origins)
    return misplaced or floating_off or geometry_off or bool(win.get("fullscreen", 0)) or win["pinned"]


def place_window(win, address, origins, floating=False):
    """Move and shape a live window to match its saved state. `floating` says
    whether it floats right now."""
    target = hypr.quote_window(address)
    if win["pinned"]:
        place_pinned_window(win, target, origins)
    else:
        place_on_workspace(win, target, origins, floating)
    fullscreen = win.get("fullscreen", 0)
    if fullscreen:
        mode = "fullscreen" if fullscreen & 2 else "maximized"
        hypr.dispatch(f"hl.dsp.window.fullscreen({{ mode = '{mode}', action = 'set', window = {target} }})")


def place_pinned_window(win, target, origins):
    """A pinned window belongs to a monitor rather than a workspace, so it is
    placed by monitor name, or by its workspace when that monitor is gone."""
    monitor = win.get("monitor_name")
    origin = origins.get(monitor)
    if origin is None:
        origin = get_workspace_origin(win["workspace"], origins)
    else:
        hypr.dispatch(f"hl.dsp.window.move({{ monitor = {hypr.quote_lua(monitor)}, window = {target} }})")
    hypr.dispatch(f"hl.dsp.window.float({{ action = 'on', window = {target} }})")
    shape_floating_window(win, target, to_absolute_position(win, origin, origins))
    hypr.dispatch(f"hl.dsp.window.pin({{ action = 'on', window = {target} }})")


def place_on_workspace(win, target, origins, floating):
    selector = hypr.quote_lua(hypr.format_workspace_selector(win["workspace"]))
    hypr.dispatch(f"hl.dsp.window.move({{ workspace = {selector}, follow = false, window = {target} }})")
    if not win["floating"]:
        if floating:
            hypr.dispatch(f"hl.dsp.window.float({{ action = 'off', window = {target} }})")
        return
    hypr.dispatch(f"hl.dsp.window.float({{ action = 'on', window = {target} }})")
    # A workspace takes its floating windows along when it changes monitor, so
    # the offset is laid out from wherever the workspace is now.
    origin = get_workspace_origin(win["workspace"], origins)
    shape_floating_window(win, target, to_absolute_position(win, origin, origins))


def shape_floating_window(win, target, at):
    # Resizing re-centres the window, so the size goes first.
    hypr.dispatch(
        f"hl.dsp.window.resize({{ x = {win['size'][0]}, y = {win['size'][1]}, window = {target} }})"
    )
    hypr.dispatch(f"hl.dsp.window.move({{ x = {at[0]}, y = {at[1]}, window = {target} }})")


def build_groups(placed):
    members = {}
    for win, address in placed:
        if win.get("group") is not None:
            members.setdefault(win["group"], []).append((win, address))
    built = 0
    for group in sorted(members):
        # Tabs follow where the windows sat, not the order they turned up.
        addresses = [address for _, address in sorted(members[group], key=lambda member: member[0]["at"])]
        if build_group(addresses):
            built += 1
    return built


def build_group(addresses):
    """The first member becomes a group and the rest are added to it."""
    if len(addresses) < 2:
        return False
    anchor = hypr.quote_window(addresses[0])
    hypr.dispatch(f"hl.dsp.group.toggle({{ window = {anchor} }})")
    for address in addresses[1:]:
        hypr.eval_lua(f"hl.get_window({anchor}).group:add(hl.get_window({hypr.quote_window(address)}))")
    return True


def place_workspaces_on_monitors(windows):
    """Workspaces bind to no monitor, so at boot they pile onto the focused
    one. Each moves as a whole onto the monitor it was saved on, by name,
    since Hyprland renumbers monitors across boots."""
    if len(hypr.query("monitors")) < 2:
        return 0
    live = {c["workspace"]["id"] for c in hypr.get_managed_clients().values()}
    wanted = {}
    for win in windows:
        workspace, monitor = win["workspace"].get("id"), win.get("monitor_name")
        if monitor and isinstance(workspace, int) and workspace > 0 and workspace in live:
            wanted.setdefault(workspace, monitor)
    for workspace, monitor in wanted.items():
        selector, name = hypr.quote_lua(str(workspace)), hypr.quote_lua(monitor)
        hypr.dispatch(f"hl.dsp.workspace.move({{ workspace = {selector}, monitor = {name} }})")
    return len(wanted)
