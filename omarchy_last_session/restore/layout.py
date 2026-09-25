"""Workspaces held open through the pass, then after the sweep: groups rebuilt,
workspace names put back, workspaces moved onto their monitors."""

from omarchy_last_session import hypr

# The Lua global holding the rules hold_workspaces added, so release_workspaces
# can switch them off: a rule added at runtime has no other handle.
HOLDS = "_G.omarchy_last_session_holds"


def hold_workspaces(windows):
    """Makes each saved workspace that does not exist yet persistent until
    release_workspaces. Without it a workspace exists only once a window lands
    on it, in whatever order the apps come up, and a setup that closes gaps in
    the numbering moves workspace 3 into 2 while 2's windows are still loading."""
    held = find_workspaces_to_hold(windows)
    if not held:
        return 0
    # No monitor: each is made on the focused one, as a window landing on it would.
    # Made on its saved monitor, it changed what a monitor fell back to once its workspace moved away.
    rules = ", ".join(
        f"hl.workspace_rule({{ workspace = {hypr.quote_lua(selector)}, persistent = true }})"
        for selector in held
    )
    hypr.eval_lua(f"{HOLDS} = {{ {rules} }}")
    return len(held)


def find_workspaces_to_hold(windows):
    """Selectors of the saved workspaces yet to exist, numbered ones first and in order."""
    # One that exists is shown or holds a window, so it cannot go. Held, it moved onto
    # the focused monitor, and the monitor it left showed a new empty workspace in
    # front of the restored windows (DP-9 and HDMI-A-1, 2026-09-25).
    existing = {hypr.format_workspace_selector(w) for w in hypr.query("workspaces")}
    saved = {hypr.format_workspace_selector(w["workspace"]) for w in windows}
    # Special workspaces are overlays nothing renumbers.
    missing = (selector for selector in saved - existing if not selector.startswith("special"))
    return sorted(missing, key=get_hold_order)


def get_hold_order(selector):
    """Numbered workspaces in number order, then named ones."""
    return (0, int(selector), "") if selector.isdigit() else (1, 0, selector)


def release_workspaces():
    """An empty workspace goes once it is no longer held, as it would have."""
    hypr.eval_lua(f"for _, rule in ipairs({HOLDS} or {{}}) do rule:set_enabled(false) end {HOLDS} = nil")


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
    # By selector, not id: a named workspace is numbered anew on every boot.
    live = {hypr.format_workspace_selector(c["workspace"]) for c in hypr.get_managed_clients().values()}
    wanted = {}
    for win in windows:
        selector, monitor = hypr.format_workspace_selector(win["workspace"]), win.get("monitor_name")
        # A special workspace shows on whichever monitor toggles it.
        if monitor and selector in live and not selector.startswith("special"):
            wanted.setdefault(selector, monitor)
    for selector, monitor in wanted.items():
        target = f"workspace = {hypr.quote_lua(selector)}, monitor = {hypr.quote_lua(monitor)}"
        hypr.dispatch(f"hl.dsp.workspace.move({{ {target} }})")
    return len(wanted)
