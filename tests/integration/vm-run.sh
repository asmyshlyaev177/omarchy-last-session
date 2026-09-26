#!/bin/bash
# The live tests inside run.sh's VM, started there as root. vkms makes a
# display-only card that Hyprland drives itself, Mesa renders with llvmpipe,
# and the tests run as the image's user, since Hyprland refuses to run as root.
set -euo pipefail

cd "$(dirname "$0")/../.."
modprobe vkms
card=
for dev in /sys/class/drm/card*; do
    # A connector's entry resolves to the card below vkms, not to vkms itself.
    if [[ $(readlink -f "$dev/device") == */vkms ]]; then card=/dev/dri/${dev##*/}; fi
done
if [[ -z $card ]]; then
    echo "vkms made no card under /sys/class/drm" >&2
    exit 1
fi
chmod 666 "$card"

exec runuser -u tester -- env HOME=/home/tester PATH="$PWD/tests/integration/bin:/usr/local/bin:/usr/bin" \
    OLS_LIVE_TESTS=1 OLS_DRM_CARD="$card" python3 -m unittest discover -s tests/integration -v "$@"
