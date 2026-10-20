"""Readers for /proc. Each returns None, or nothing, for a process that is gone."""

from __future__ import annotations

import glob
import os
from collections import deque
from collections.abc import Collection

PROC_ROOT = "/proc"
# A process in another mount or user namespace sees other files under the same paths.
HOST_NAMESPACES = ("mnt", "user")


def read_cmdline(pid: int) -> list[str] | None:
    try:
        with open(f"{PROC_ROOT}/{pid}/cmdline", "rb") as f:
            return [part.decode("utf-8", "replace") for part in f.read().split(b"\0") if part]
    except OSError:
        return None


def read_cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"{PROC_ROOT}/{pid}/cwd")
    except OSError:
        return None


def read_environ(pid: int) -> dict[str, str]:
    """Environment a process was started with; empty for one that is gone."""
    try:
        with open(f"{PROC_ROOT}/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return {}
    entries = (part.decode("utf-8", "replace").split("=", 1) for part in raw.split(b"\0") if part)
    return {entry[0]: entry[1] for entry in entries if len(entry) == 2}


def read_comm(pid: int) -> str | None:
    try:
        with open(f"{PROC_ROOT}/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return None


def list_children(pid: int) -> list[int]:
    pids: list[int] = []
    for path in glob.glob(f"{PROC_ROOT}/{pid}/task/*/children"):
        try:
            with open(path) as f:
                pids += [int(child) for child in f.read().split()]
        except OSError:
            continue
    return pids


def is_alive(pid: int) -> bool:
    return os.path.exists(f"{PROC_ROOT}/{pid}")


def find_descendant(pid: int, names: Collection[str], max_depth: int = 5) -> int | None:
    """Breadth-first search under pid for a host process whose comm is in names."""
    queue = deque((child, 1) for child in list_children(pid))
    while queue:
        current, depth = queue.popleft()
        # A sandboxed process names itself, and what it starts is sandboxed too.
        if not is_host_process(current):
            continue
        if read_comm(current) in names:
            return current
        if depth < max_depth:
            queue.extend((child, depth + 1) for child in list_children(current))
    return None


def read_namespace_inode(pid: int, kind: str) -> int | None:
    """The kernel's id for the namespace of that kind pid is in; it reuses one once the namespace is gone."""
    try:
        return os.stat(f"{PROC_ROOT}/{pid}/ns/{kind}").st_ino
    except OSError:
        return None


def read_cgroup(pid: int) -> str | None:
    """The process's cgroup v2 path, from the "0::" line of /proc/PID/cgroup."""
    try:
        with open(f"{PROC_ROOT}/{pid}/cgroup") as f:
            paths = [line.strip()[len("0::") :] for line in f if line.startswith("0::")]
    except OSError:
        return None
    return paths[0] if paths else None


def read_uids(pid: int) -> list[int] | None:
    """Real, effective, saved and filesystem uid, from /proc/PID/status."""
    try:
        with open(f"{PROC_ROOT}/{pid}/status") as f:
            lines = [line for line in f if line.startswith("Uid:")]
        return [int(field) for field in lines[0].split()[1:]] if lines else None
    except (OSError, ValueError):
        return None


def is_host_process(pid: int) -> bool:
    """Whether pid runs as this user in this session's mount and user namespaces,
    the only processes whose command line may be replayed outside any sandbox."""
    uids = read_uids(pid)
    if uids is None or uids[:2] != [os.getuid()] * 2:
        return False
    return all(is_in_own_namespace(pid, kind) for kind in HOST_NAMESPACES)


def is_in_own_namespace(pid: int, kind: str) -> bool:
    own = read_namespace_inode(os.getpid(), kind)
    return own is not None and read_namespace_inode(pid, kind) == own
