"""Readers for /proc. Each returns None, or nothing, for a process that is gone."""

import glob
import os
from collections import deque


def read_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [part.decode("utf-8", "replace") for part in f.read().split(b"\0") if part]
    except OSError:
        return None


def read_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def read_environ(pid):
    """Environment a process was started with; empty for one that is gone."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return {}
    entries = (part.decode("utf-8", "replace").split("=", 1) for part in raw.split(b"\0") if part)
    return {entry[0]: entry[1] for entry in entries if len(entry) == 2}


def read_comm(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return None


def list_children(pid):
    pids = []
    for path in glob.glob(f"/proc/{pid}/task/*/children"):
        try:
            with open(path) as f:
                pids += [int(child) for child in f.read().split()]
        except OSError:
            continue
    return pids


def is_alive(pid):
    return os.path.exists(f"/proc/{pid}")


def find_descendant(pid, names, max_depth=5):
    """Breadth-first search under pid for a process whose comm is in names."""
    queue = deque((child, 1) for child in list_children(pid))
    while queue:
        current, depth = queue.popleft()
        if read_comm(current) in names:
            return current
        if depth < max_depth:
            queue.extend((child, depth + 1) for child in list_children(current))
    return None
