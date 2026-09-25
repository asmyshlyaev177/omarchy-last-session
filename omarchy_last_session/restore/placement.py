"""Moving a live window to where it was saved, with its floating geometry, pin and fullscreen."""

from omarchy_last_session import hypr


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
