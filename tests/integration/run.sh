#!/bin/bash
# Run the live tests in a container: a real Hyprland with two headless
# monitors, real foot windows, and the script under test driven end to end.
#
#   tests/integration/run.sh            # every live test
#   tests/integration/run.sh -k groups  # unittest -k filter
#   OLS_JOBS=1 tests/integration/run.sh # one test at a time, not half as many as CPUs
#   OLS_VM=1 tests/integration/run.sh   # in a VM, for a host without a render node
#
# The compositor renders through a render node of the host, passed in
# read-write: the first one Mesa can drive, or $OLS_RENDER_NODE. Rendering is
# done by Mesa and a real GPU is not needed, but the node has to exist, because
# the backend opens one to allocate buffers. On an Omarchy host its Hyprland
# defaults are mounted in, so the compositor runs the same window and group
# rules as the desktop it is meant to restore.
#
# OLS_VM=1 needs /dev/kvm instead of a render node: the container boots the
# image's own kernel, where vm-run.sh loads vkms for a display-only card. CI
# runs it this way, because GitHub's hosted runners have KVM and no render node.
#
# podman by default; OLS_CONTAINER=docker for CI, where rootless podman has no
# subuid range to map with.
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
image=omarchy-last-session-test
runtime=${OLS_CONTAINER:-podman}
# Tests run this many at a time, each with a compositor of its own.
jobs=${OLS_JOBS:-$(( ($(nproc) + 1) / 2 ))}

mesa_render_node() {
    local node driver
    for node in /dev/dri/renderD*; do
        driver=$(basename "$(readlink -f "/sys/class/drm/$(basename "$node")/device/driver")")
        [[ $driver == nvidia ]] || { echo "$node"; return; }
    done
}

use_render_node() {
    local node=${OLS_RENDER_NODE:-$(mesa_render_node)}
    if [[ -z $node || ! -e $node ]]; then
        echo "no render node Mesa can use under /dev/dri; set OLS_RENDER_NODE, or OLS_VM=1" >&2
        exit 1
    fi
    args+=(--device "$node" -e WLR_RENDER_DRM_DEVICE="$node"
           -e OLS_LIVE_TESTS=1 -e OLS_JOBS="$jobs" -e PATH=/work/tests/integration/bin:/usr/local/bin:/usr/bin)
    command=(python3 tests/integration/parallel.py "$@")
}

use_vm() {
    if [[ ! -e /dev/kvm ]]; then
        echo "OLS_VM needs /dev/kvm" >&2
        exit 1
    fi
    # On a CI runner /dev/kvm is root:kvm 0660, and the image runs as uid 1000.
    args+=(--device /dev/kvm --group-add "$(stat -c %g /dev/kvm)")
    # --exec takes one command line, which the guest's shell splits again.
    local tests="OLS_JOBS=$jobs /work/tests/integration/vm-run.sh"
    if (( $# )); then tests+=$(printf ' %q' "$@"); fi
    command=(vng --run /boot/vmlinuz-linux --user root --memory 4G --cpus "$(nproc)" --cwd /work
             --exec "$tests")
}

# Hyprland 0.56.2 segfaults at exit while a group is open. A host's systemd-coredump took
# 2-10 s to store each dump, with every client still connected: 80 s of a 234 s run.
args=(--rm --ulimit core=0 -v "$repo:/work:ro")
# rootless podman maps the host uid into the container; the root docker daemon
# already runs the image as its own uid 1000 and rejects the flag.
if [[ $runtime == podman ]]; then args+=(--userns=keep-id); fi
if [[ -d /usr/share/omarchy ]]; then
    args+=(-v /usr/share/omarchy:/usr/share/omarchy:ro)
fi
if [[ ${OLS_VM:-} == 1 ]]; then use_vm "$@"; else use_render_node "$@"; fi

"$runtime" build -q -t "$image" "$repo/tests/integration"
exec "$runtime" run "${args[@]}" "$image" "${command[@]}"
