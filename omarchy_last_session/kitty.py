"""Kitty's tabs and splits. A kitty with remote control enabled describes its
whole instance, and that description replays as a session file, so the relaunch
brings back every OS window, tab, split and working directory rather than one
bare window. Without remote control there is nothing to read and the terminal
is relaunched in its working directory, as every other terminal is."""

import json
import os
import shlex
import subprocess

from omarchy_last_session import config, proc

LISTEN_ENV = "KITTY_LISTEN_ON"
REMOTE_CONTROL = ["kitten", "@"]
# Names the pane a later directive splits or focuses; kitty keeps window ids
# to itself, so the session file carries its own.
PANE_VAR = "ols_pane"


def build_session_text(pid):
    """A session file replaying this kitty instance, or None when it does not
    answer: remote control is off, or `kitten` is not installed."""
    address = find_listen_address(pid)
    if address is None:
        return None
    layout = read_layout(address)
    return render_session(layout) if layout else None


def find_listen_address(pid):
    """Kitty exports its socket to every process it starts, and to nothing
    else, so the address is read from a window's shell rather than guessed."""
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
    """Kitty fills the tab it opens with from the first one asked for here, so
    every tab is asked for, the first included. `new_tab` reads the rest of its
    line as the title, so that title is written plain rather than quoted."""
    lines = ["new_tab " + tab["title"] if is_named(tab) else "new_tab"]
    if tab.get("enabled_layouts"):
        lines.append("enabled_layouts " + ",".join(tab["enabled_layouts"]))
    lines.append("layout " + tab.get("layout", "splits"))
    return lines + render_panes(tab) + render_active_pane(tab)


def render_panes(tab):
    """Panes in an order that rebuilds the tab. Under the splits layout each
    pair was made by splitting one pane, so the tree says which pane to focus
    and how to split it; every other layout arranges its panes by itself."""
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
    parent before its children, so each pane exists before it is split. A pair
    holding one pane, which is how kitty reports a tab that was never split,
    made no split."""
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
    """The pane that had the keyboard in this tab. It has to be asked for while
    the tab is the one being built: the directive reaches no other tab."""
    for window in tab.get("windows") or []:
        if window.get("is_active"):
            return [f"focus_matching_window var:{PANE_VAR}={window['id']}"]
    return []


def render_active_tab(tabs):
    """The tab the window opens on, which is also the title it carries. The
    first tab is where kitty starts, so only another one is asked for."""
    for index, tab in enumerate(tabs):
        if tab.get("is_active") and index:
            return ["focus_tab " + str(index)]
    return []


def build_program_argv(window):
    """The TUI a pane is running, so it comes back. A pane at a shell prompt is
    left to kitty, which opens the user's shell in the pane's directory."""
    for process in window.get("foreground_processes") or []:
        argv = [arg for arg in process.get("cmdline") or [] if not arg.startswith(config.CWD_FILE_FLAG)]
        if argv and os.path.basename(argv[0]) in config.TUI_PROGRAMS:
            return argv if all(is_one_line(arg) for arg in argv) else []
    return []


def is_named(item):
    """True for a tab or pane the user titled; every other title is the shell's
    and says nothing a new one will not say again."""
    return bool(item.get("title_overridden")) and is_one_line(item.get("title") or "")


def is_one_line(text):
    """Kitty reads a session file a line at a time, so anything carrying a
    newline would be read as further directives. A title is whatever ran in the
    pane wrote, and a directory is named by whoever made it, so neither is
    trusted with a line of its own."""
    return bool(text) and "\n" not in text and "\r" not in text
