# Hyprland for the live restore tests. Its backend needs a DRM device for its
# allocator, so a GPU-less container cannot run it bare: it runs nested in a
# headless labwc that renders on the host's render node. No seat, no systemd.
#
# labwc rather than the smaller cage: Hyprland binds xdg_wm_base at version 6,
# and cage, sway and weston all still advertise 5, which fails the connection.
#
# The repository is bind-mounted at /work by run.sh.
FROM docker.io/library/archlinux:latest

RUN pacman -Syu --noconfirm --needed hyprland labwc foot ttf-dejavu mesa python \
    && pacman -Scc --noconfirm

# Hyprland refuses to run as root, and the tests are owned by uid 1000 on the host.
RUN useradd -m -u 1000 tester
USER tester
ENV HOME=/home/tester
WORKDIR /work
