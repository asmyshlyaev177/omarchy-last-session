"""A kitty instance's tabs and splits, rendered as a session file. Needs the
instance's remote control on; without it there is nothing to read."""

import json
import os
import shlex
import subprocess

from omarchy_last_session import config, proc, relaunch

LISTEN_ENV = "KITTY_LISTEN_ON"
REMOTE_CONTROL = ["kitten", "@"]
# Names the pane a later directive splits or focuses; kitty's own window ids
# do not survive into the new instance.
PANE_VAR = "ols_pane"


def build_session_text(pid):
    """A session file replaying this instance, or None when it does not answer."""
    address = find_listen_address(pid)
    if address is None:
        return None
    layout = read_layout(address)
    return render_session(layout) if layout else None


def find_listen_address(pid):
    """Kitty exports its socket only to the processes it starts, so the address
    is read from a pane's shell rather than guessed."""
    for child in proc.list_children(pid):
        address = proc.read_environ(child).get(LISTEN_ENV)
        if address:
            return address
    return None


def read_layout(address):
    """Every OS window, tab and pane of the instance, as kitty reports them."""
    try:
        done = subprocess.run(
            REMOTE_CONTROL + ["--to", address, "ls"], capture_output=True, text=True, timeout=5
        )
        return json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def render_session(os_windows):
    lines = []
    for index, os_window in enumerate(os_windows):
        if index:
            lines.append("new_os_window")
        lines += render_os_window(os_window)
    return "\n".join(lines) + "\n"


def render_os_window(os_window):
    tabs = os_window.get("tabs") or []
    lines = []
    for tab in tabs:
        lines += render_tab(tab)
    return lines + render_active_tab(tabs)


def render_tab(tab):
    """`new_tab` reads the rest of its line as the title, so the title is
    written plain rather than quoted."""
    lines = ["new_tab " + tab["title"] if is_named(tab) else "new_tab"]
    if tab.get("enabled_layouts"):
        lines.append("enabled_layouts " + ",".join(tab["enabled_layouts"]))
    lines.append("layout " + tab.get("layout", "splits"))
    return lines + render_panes(tab) + render_active_pane(tab)


def render_panes(tab):
    """Panes in an order that rebuilds the tab. The splits layout has a tree
    saying which pane to split; every other layout arranges its own."""
    by_id = {window["id"]: window for window in tab.get("windows") or []}
    pairs = (tab.get("layout_state") or {}).get("pairs")
    if not pairs:
        return [render_launch(window, None) for window in tab.get("windows") or []]
    lines = [render_launch(by_id[find_head(pairs)], None)]
    for anchor, horizontal, new in iter_splits(pairs):
        lines.append(f"focus_matching_window var:{PANE_VAR}={anchor}")
        lines.append(render_launch(by_id[new], "vsplit" if horizontal else "hsplit"))
    return lines


def find_head(node):
    """The pane a subtree grew from: splitting it made the pair."""
    while not isinstance(node, int):
        node = node["one"] if "one" in node else node["two"]
    return node


def iter_splits(node):
    """(pane to split, side by side, pane the split made) for every pair, a
    parent before its children, so each pane exists before it is split. Kitty
    reports a tab that was never split as a pair holding one pane."""
    if isinstance(node, int):
        return
    sides = [side for side in ("one", "two") if side in node]
    if len(sides) == 2:
        yield find_head(node["one"]), node.get("horizontal", True), find_head(node["two"])
    for side in sides:
        for split in iter_splits(node[side]):
            yield split


def render_launch(window, location):
    argv = ["launch"]
    if location:
        argv.append("--location=" + location)
    argv += ["--var", f"{PANE_VAR}={window['id']}"]
    if is_one_line(window.get("cwd") or ""):
        argv.append("--cwd=" + window["cwd"])
    if is_named(window):
        argv.append("--title=" + window["title"])
    return shlex.join(argv + build_program_argv(window))


def render_active_pane(tab):
    """Asked for while this tab is the one being built: it reaches no other."""
    for window in tab.get("windows") or []:
        if window.get("is_active"):
            return [f"focus_matching_window var:{PANE_VAR}={window['id']}"]
    return []


def render_active_tab(tabs):
    """Kitty starts on the first tab, so only another one is asked for."""
    for index, tab in enumerate(tabs):
        if tab.get("is_active") and index:
            return ["focus_tab " + str(index)]
    return []


def build_program_argv(window):
    """The TUI a pane is running. A pane at a prompt is left to kitty, which
    opens the user's shell in its directory."""
    for process in window.get("foreground_processes") or []:
        argv = relaunch.drop_per_run_args(process.get("cmdline") or [])
        if argv and os.path.basename(argv[0]) in config.TUI_PROGRAMS:
            return argv if all(is_one_line(arg) for arg in argv) else []
    return []


def is_named(item):
    """True for a tab or pane the user titled, not one the shell titled."""
    return bool(item.get("title_overridden")) and is_one_line(item.get("title") or "")


def is_one_line(text):
    """Kitty reads a session file one directive per line, and a pane's title is
    whatever ran in it printed, so text spanning lines is dropped."""
    return bool(text) and "\n" not in text and "\r" not in text
