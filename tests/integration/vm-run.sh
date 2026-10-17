#!/bin/bash
# The live tests inside run.sh's VM, started there as root. vkms makes a
# display-only card per test that runs at once, since a card takes one
# compositor at a time, and Hyprland drives it itself. Mesa renders with
# llvmpipe, and the tests run as the image's user, since Hyprland refuses to
# run as root.
set -euo pipefail

cd "$(dirname "$0")/../.."
jobs=${OLS_JOBS:-1}
modprobe vkms create_default_dev=0
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
for ((i = 1; i <= jobs; i++)); do
    card=/sys/kernel/config/vkms/ols$i
    mkdir "$card" "$card/planes/plane0" "$card/crtcs/crtc0" "$card/encoders/encoder0" "$card/connectors/connector0"
    echo 1 > "$card/planes/plane0/type" # primary: a card has none otherwise, and needs one
    # Disconnected, so a test's monitors are the headless ones it creates and no other.
    echo 2 > "$card/connectors/connector0/status"
    ln -s "$card/crtcs/crtc0" "$card/planes/plane0/possible_crtcs"
    ln -s "$card/crtcs/crtc0" "$card/encoders/encoder0/possible_crtcs"
    ln -s "$card/encoders/encoder0" "$card/connectors/connector0/possible_encoders"
    echo 1 > "$card/enabled"
done
cards=()
for dev in /sys/class/drm/card*; do
    # A connector's entry resolves to the card below the device, not to the device itself.
    if [[ $(readlink -f "$dev/device") == */ols+([0-9]) ]]; then
        chmod 666 "/dev/dri/${dev##*/}"
        cards+=("/dev/dri/${dev##*/}")
    fi
done
if (( ${#cards[@]} != jobs )); then
    echo "vkms made ${#cards[@]} of $jobs cards under /sys/class/drm" >&2
    exit 1
fi

exec runuser -u tester -- env HOME=/home/tester PATH="$PWD/tests/integration/bin:/usr/local/bin:/usr/bin" \
    OLS_LIVE_TESTS=1 OLS_DRM_CARDS="${cards[*]}" python3 tests/integration/parallel.py "$@"
