"""The host's own record of a sandboxed window's app, which the app cannot
rewrite, turned into the command that starts it through its sandbox again."""

from __future__ import annotations

import configparser
import json
import os
import re
import shlex
from typing import NamedTuple, cast

from omarchy_last_session import files, proc

FLATPAK_INSTANCES = ".flatpak"
FLATPAK_APP_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*)+$")
FLATPAK_FIELD = re.compile(r"^[A-Za-z0-9_.-]+$")
# The scope `snap run` puts an app in; a hook's, snap.<snap>.hook.<hook>-<uuid>, does not match.
# The UUID is spelled out because an app name may itself end in "-" and hex digits.
SNAP_SCOPE = re.compile(
    r"^snap\.(?P<snap>[a-z0-9](?:-?[a-z0-9])*)\.(?P<app>[A-Za-z0-9](?:-?[A-Za-z0-9])*)"
    r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.scope$"
)


class FlatpakApp(NamedTuple):
    app_id: str
    branch: str
    arch: str


def build_sandbox_command(pid: int) -> str | None:
    """Host-defined only, like the app's own launcher entry: the sandboxed
    process's arguments are its own to forge, so none are carried over."""
    flatpak = find_flatpak_app(pid)
    if flatpak is not None:
        return shlex.join(
            ["flatpak", "run", f"--branch={flatpak.branch}", f"--arch={flatpak.arch}", flatpak.app_id]
        )
    snap = find_snap_app(pid)
    return shlex.join(["snap", "run", snap]) if snap else None


def get_flatpak_instances_dir() -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    # The XDG spec has a relative path ignored: it would resolve against the saver's cwd.
    if not os.path.isabs(runtime_dir):
        runtime_dir = f"/run/user/{os.getuid()}"
    return os.path.join(runtime_dir, FLATPAK_INSTANCES)


def find_flatpak_app(pid: int) -> FlatpakApp | None:
    """The app of the Flatpak instance whose sandbox pid's mount namespace is,
    from the instance directory Flatpak keeps on the host for it."""
    inode = proc.read_namespace_inode(pid, "mnt")
    if inode is None:
        return None
    for instance in list_flatpak_instance_dirs():
        if is_live_instance_in(instance, inode):
            return read_flatpak_info(instance)
    return None


def list_flatpak_instance_dirs() -> list[str]:
    """Instances only: siblings such as <n>-private or the per-app ones are not
    named by digits alone. A symlink in an instance's place is not followed."""
    try:
        entries = list(os.scandir(get_flatpak_instances_dir()))
    except OSError:
        return []
    return sorted(e.path for e in entries if is_instance_name(e.name) and e.is_dir(follow_symlinks=False))


def is_instance_name(name: str) -> bool:
    # str.isdigit() alone accepts "²" and other non-ASCII digits.
    return name.isascii() and name.isdigit()


def is_live_instance_in(instance: str, inode: int) -> bool:
    """Whether the instance's sandbox is in that mount namespace and its child still is: the
    kernel reuses namespace inodes, and Flatpak removes a dead instance's directory lazily."""
    info = read_bwrapinfo(instance)
    recorded, child_pid = info.get("mnt-namespace"), info.get("child-pid")
    # type() rather than isinstance(), which takes a JSON true for the int 1.
    if type(recorded) is not int or type(child_pid) is not int or recorded != inode:
        return False
    return proc.read_namespace_inode(child_pid, "mnt") == inode


def read_bwrapinfo(instance: str) -> dict[str, object]:
    try:
        with files.open_regular_file(os.path.join(instance, "bwrapinfo.json")) as f:
            info: object = json.load(f)
    except (OSError, ValueError):
        return {}
    return cast("dict[str, object]", info) if isinstance(info, dict) else {}


def read_flatpak_info(instance: str) -> FlatpakApp | None:
    """[Application] name and [Instance] branch and arch of the instance's info keyfile."""
    keyfile = configparser.ConfigParser(interpolation=None)
    try:
        with files.open_regular_file(os.path.join(instance, "info")) as f:
            keyfile.read_file(f)
        app = FlatpakApp(
            keyfile["Application"]["name"], keyfile["Instance"]["branch"], keyfile["Instance"]["arch"]
        )
    except (OSError, ValueError, KeyError, configparser.Error):
        return None
    return app if is_valid_flatpak_app(app) else None


def is_valid_flatpak_app(app: FlatpakApp) -> bool:
    """False for anything no Flatpak would accept, which then never reaches a shell command."""
    fields = (app.branch, app.arch)
    return bool(FLATPAK_APP_ID.match(app.app_id)) and all(FLATPAK_FIELD.match(field) for field in fields)


def find_snap_app(pid: int) -> str | None:
    """What `snap run` takes to start pid's app again: <snap>, or <snap>.<app>
    for an app not named after its snap."""
    # The host's `snap run` creates the scope before confinement starts, and
    # what it yields only starts an installed snap, with no arguments.
    match = SNAP_SCOPE.match(os.path.basename(proc.read_cgroup(pid) or ""))
    if not match:
        return None
    return match["snap"] if match["app"] == match["snap"] else f"{match['snap']}.{match['app']}"
