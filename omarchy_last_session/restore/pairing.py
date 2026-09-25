"""Which live window is which saved entry, by command line, then title, then workspace."""

import os
import shlex

from omarchy_last_session import config, proc, relaunch
from omarchy_last_session.restore import titles


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
        titles.score_title_likeness(win.get("title", ""), client.get("title", "")),
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
