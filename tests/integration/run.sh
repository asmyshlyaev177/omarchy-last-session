#!/bin/bash
# Run the live tests in a container: a real Hyprland with two headless
# monitors, real foot windows, and the script under test driven end to end.
#
#   tests/integration/run.sh            # every live test
#   tests/integration/run.sh -k groups  # unittest -k filter
#
# The compositor renders through a render node of the host, passed in
# read-write: the first one Mesa can drive, or $OLS_RENDER_NODE. Rendering is
# done by Mesa and a real GPU is not needed, but the node has to exist, because
# the backend opens one to allocate buffers. On an Omarchy host its Hyprland
# defaults are mounted in, so the compositor runs the same window and group
# rules as the desktop it is meant to restore.
#
# podman by default; OLS_CONTAINER=docker for CI, where rootless podman has no
# subuid range to map with.
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
image=omarchy-last-session-test
runtime=${OLS_CONTAINER:-podman}

mesa_render_node() {
    local node driver
    for node in /dev/dri/renderD*; do
        driver=$(basename "$(readlink -f "/sys/class/drm/$(basename "$node")/device/driver")")
        [[ $driver == nvidia ]] || { echo "$node"; return; }
    done
}
node=${OLS_RENDER_NODE:-$(mesa_render_node)}
if [[ -z $node || ! -e $node ]]; then
    echo "no render node Mesa can use under /dev/dri; set OLS_RENDER_NODE" >&2
    exit 1
fi

"$runtime" build -q -t "$image" "$repo/tests/integration"

args=(--rm -v "$repo:/work:ro" --device "$node"
      -e WLR_RENDER_DRM_DEVICE="$node")
# rootless podman maps the host uid into the container; the root docker daemon
# already runs the image as its own uid 1000 and rejects the flag.
if [[ $runtime == podman ]]; then args+=(--userns=keep-id); fi
if [[ -d /usr/share/omarchy ]]; then
    args+=(-v /usr/share/omarchy:/usr/share/omarchy:ro)
fi

exec "$runtime" run "${args[@]}" \
    -e OLS_LIVE_TESTS=1 -e PATH=/work/tests/integration/bin:/usr/local/bin:/usr/bin \
    "$image" python3 -m unittest discover -s tests/integration -v "$@"
