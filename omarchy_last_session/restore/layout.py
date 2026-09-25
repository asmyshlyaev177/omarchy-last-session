"""After the sweep: groups rebuilt, workspace names put back, workspaces moved onto their monitors."""

from omarchy_last_session import hypr


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
