"""The restore pass: launch, sweep, group, then name workspaces and place them on monitors."""

import dataclasses
import os
import re
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
        warn(describe_missing(win, placed, missing))
    build_groups(placed)
    name_workspaces(windows)
    place_workspaces_on_monitors(windows)


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
    watched = {}
    deadline = time.time() + config.SWEEP_TIMEOUT
    while pending:
        time.sleep(1)
        now = time.time()
        if now >= deadline:
            break
        placed += place_new_arrivals(pending, seen, origins, watched, now, deadline)
    return placed, pending


@dataclasses.dataclass
class Sighting:
    """A window the sweep has yet to pair: when it turned up, its title at the
    last pass, and whether that title had stopped changing by then."""

    first_seen: float
    title: str
    is_settled: bool = False


def place_new_arrivals(pending, seen, origins, watched, now, deadline):
    """One pass over the desktop. Updates pending, seen and watched in place."""
    in_play = {
        address: client
        for address, client in hypr.get_managed_clients().items()
        if address not in seen and find_candidates(pending, client)
    }
    watch_titles(in_play, watched, now)
    ready = find_ready_windows(pending, in_play, watched, now, deadline)
    placed = pair_arrivals(pending, ready)
    for entry, address in placed:
        pending.remove(entry)
        seen.add(address)
        place_arrival(entry, address, ready[address], origins)
    return placed


def watch_titles(in_play, watched, now):
    """Logs the title each window turns up with and every change after it: a
    browser titles a window long before its page has loaded."""
    for address, client in in_play.items():
        cls, title, before = client.get("class", ""), client.get("title", ""), watched.get(address)
        if before is None:
            log(f"{cls} {address} turned up on workspace {client['workspace'].get('id')} titled '{title}'")
            watched[address] = Sighting(now, title)
            continue
        if title != before.title:
            log(f"{cls} {address} is now titled '{title}'")
        watched[address] = Sighting(before.first_seen, title, has_title_settled(before.title, title))


def find_ready_windows(pending, in_play, watched, now, deadline):
    """The windows to pair this pass: all of a class together, once titles can
    tell them apart or TITLE_SETTLE has passed since the last of them turned up."""
    ready = {}
    for cls, rivals in group_by_class(in_play).items():
        wanted = len(set().union(*(find_candidates(pending, client) for client in rivals.values())))
        unsettled = sum(not watched[address].is_settled for address in rivals)
        if can_tell_apart(len(rivals), wanted, unsettled):
            ready.update(rivals)
            continue
        waited = now - max(watched[address].first_seen for address in rivals)
        if waited >= config.TITLE_SETTLE or now >= deadline - 1:
            log(
                f"waited {waited:.0f} s for {cls} windows: {len(rivals)} of {wanted} turned up,"
                f" {unsettled} still changing titles"
            )
            ready.update(rivals)
    return ready


def can_tell_apart(windows, entries, unsettled):
    """Once every rival has turned up and its title has settled. A lone window
    with one entry to take has nothing to be told apart from."""
    return (windows == 1 and entries == 1) or (windows >= entries and unsettled == 0)


def group_by_class(clients):
    classes = {}
    for address, client in clients.items():
        classes.setdefault(client.get("class", ""), {})[address] = client
    return classes


def pair_arrivals(pending, arrivals):
    """(entry, address) pairs, the closest fit first: paired in the order Hyprland
    listed them, a window that barely fitted an entry took it from one that fitted it well."""
    fits = []
    for address, client in arrivals.items():
        live_argv = get_client_argv(client)
        fits += [
            (score_fit(pending[i], client, live_argv), address, i) for i in find_candidates(pending, client)
        ]
    pairs, paired_addresses, paired_entries = [], set(), set()
    for _, address, index in sorted(fits, key=lambda fit: fit[0], reverse=True):
        if address in paired_addresses or index in paired_entries:
            continue
        paired_addresses.add(address)
        paired_entries.add(index)
        pairs.append((pending[index], address))
    return pairs


def place_arrival(entry, address, client, origins):
    # Moving one member of a group moves the whole group; a group of one
    # is still just a window.
    fixing = len(client.get("grouped") or []) <= 1 and is_out_of_place(entry, client, origins)
    log(describe_pairing(entry, address, client, fixing))
    if fixing:
        place_window(entry, address, origins, floating=bool(client.get("floating")))


def describe_pairing(entry, address, client, fixing):
    command, title, _ = score_fit(entry, client, get_client_argv(client))
    return (
        f"{client.get('class', '')} {address} '{client.get('title', '')}'"
        f" on workspace {client['workspace'].get('id')}"
        f" is the saved '{entry.get('title', '')}' from workspace {entry['workspace'].get('id')}"
        f" (command {command}, title {title:.2f}); {'placing it' if fixing else 'already in place'}"
    )


def find_candidates(pending, client):
    """Indexes of the entries a window may be: of its class, or else of its program,
    since Chromium reports chromium-browser when relaunched from a command line."""
    by_class = [i for i, win in enumerate(pending) if win["class"] == client.get("class", "")]
    if by_class:
        return by_class
    program = get_program_name_of(get_client_argv(client))
    return [i for i, win in enumerate(pending) if program and get_program_name(win.get("cmd", "")) == program]


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
# Titles are compared by the words they share: two unrelated long titles have
# more letters in common by chance than a page's early title has with its own.
WORD = re.compile(r"\w+")


def score_title_likeness(saved, live):
    """The share of words two titles have in common, without the app name both end
    in. A placeholder or missing title scores zero rather than match on the app name."""
    saved_page, live_page = strip_shared_app_name(saved, live)
    if not saved_page or not live_page or is_placeholder(saved_page) or is_placeholder(live_page):
        return 0.0
    saved_words, live_words = get_words(saved_page), get_words(live_page)
    if not saved_words or not live_words:
        return float(saved_page == live_page)
    return 2 * len(saved_words & live_words) / (len(saved_words) + len(live_words))


def has_title_settled(before, after):
    """The same words a pass apart, numbers aside, and no placeholder: a price or
    an unread count keeps moving on a page that has loaded."""
    return not is_still_loading(after) and drop_numbers(get_words(before)) == drop_numbers(get_words(after))


def get_words(text):
    return set(WORD.findall(text.lower()))


def drop_numbers(words):
    return {word for word in words if not word.isdigit()}


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


def name_workspaces(windows):
    """A numbered workspace is recreated by its number, which comes back with
    no name, so the name it was saved under is put back on it."""
    live = {w.get("id"): w.get("name") for w in hypr.query("workspaces")}
    wanted = {}
    for win in windows:
        number, name = win["workspace"].get("id"), win["workspace"].get("name", "")
        if isinstance(number, int) and number > 0 and name and name != str(number) and number in live:
            wanted.setdefault(number, name)
    renamed = 0
    for number, name in wanted.items():
        if live[number] != name:
            selector, quoted = hypr.quote_lua(str(number)), hypr.quote_lua(name)
            hypr.dispatch(f"hl.dsp.workspace.rename({{ workspace = {selector}, name = {quoted} }})")
            renamed += 1
    return renamed


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
