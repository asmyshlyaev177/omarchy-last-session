# omarchy-last-session

Reopen your last session's windows on login. An [Omarchy](https://omarchy.org) shell plugin for Hyprland 0.55+.

Log out, reboot, or shut down. On the next login every window comes back on the workspace and monitor it was on, without stealing focus while it opens. Floating windows return to their position and size. Pinned, fullscreen and grouped windows come back in that state. Terminals reopen in their last working directory, and browsers keep their tabs.

The whole thing is a small Python package that talks to `hyprctl` and reads `/proc`, plus a small shell service that starts it. No compositor patches, no extra daemons, no dependencies beyond Python 3.9.

## Install

```sh
omarchy plugin add https://github.com/asmyshlyaev177/omarchy-last-session.git --enable
```

The plugin saves a snapshot every minute from then on, and restores it two seconds after the shell starts on your next login. A crash or a hard power-off costs at most a minute of changes.

### Recommended: snapshot from the power menu

Omarchy closes every window about two seconds before it powers off. A browser asked to close with many tabs open shows a "close N tabs?" dialog, gets killed while showing it, and records a crash instead of a session. To make clean exits exact, take the snapshot first and let the browsers quit on their own. Add this to `~/.config/omarchy/extensions/omarchy-menu.jsonc` (create the file if it does not exist):

```jsonc
{
  "system.logout":   {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-logout"},
  "system.reboot":   {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-reboot"},
  "system.shutdown": {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-shutdown"},
}
```

Leave suspend and hibernate alone. They resume the live session. The menu reloads the file on save.

### Optional: leave some windows out

Anything your own autostart launches should not be restored a second time. Set the excluded window classes in `~/.config/hypr/hyprland.lua`, so the service and the power menu both inherit them:

```lua
hl.env("OMARCHY_LAST_SESSION_EXCLUDE", "my-autostarted-app,another-class")
```

Find a window's class with `hyprctl clients -j | jq '.[].class'`.

## How it works

The script has four commands. The plugin runs `restore` and `daemon`; the power menu runs `shutdown`; `save` is there for the command line.

- **`save`** snapshots every mapped window: class, workspace, monitor, geometry, floating, pinned and fullscreen state, group membership, and a relaunch command recovered from `/proc/<pid>/cmdline` and `/proc/<pid>/cwd`. When the command line cannot be replayed (an AppImage's temporary mount, a D-Bus activation), the app's `.desktop` entry is used instead.
- **`restore`** relaunches each window through a Hyprland window rule that tracks the spawned process, so it opens on the right workspace silently. A sweep pass then places the windows that escape process tracking, from forking and single-instance apps. It matches them by window class, and by the program behind the window when the class has shifted, as Chromium's does between its desktop entry and a command line. Every window is launched, except those of an app that reopens its own windows (a browser, VS Code): such an app is launched once per process, because a second launch adds an empty window instead of the one it was meant to bring back.
- **Groups** are rebuilt after the sweep. The first member of each saved group becomes a group and the rest are added to it by address, in saved left-to-right order. Nothing depends on focus or on the order windows turn up in, so everything can be launched in one pass and slow apps overlap instead of queueing.
- **Monitors** are restored last. Workspaces bind to no monitor, so at boot they pile onto the focused one. Each is moved as a whole onto the monitor it was saved on, by name, because Hyprland renumbers monitors across boots. Floating windows are placed by their offset into that monitor, so they survive the monitor moving in the layout or being unplugged. A single-monitor machine skips the pass. Whichever workspace moves last is the one you land on; focus is not put back where you left it.
- **`shutdown`** saves, even an empty desktop, keeps a copy of that snapshot as `last-shutdown.json`, then sends SIGTERM to the browsers and VS Code so they write their sessions out, and waits up to ten seconds for them to exit. VS Code records which folders were open only as it quits, so a folder opened shortly before a hard shutdown is otherwise never recorded.
- **`daemon`** runs `save` every sixty seconds, starting ninety seconds after login so a restore in progress is not snapshotted half done.

Restore refuses to run into a desktop that already has more than three windows open, so enabling the plugin mid-session or restarting the shell does not reopen anything.

### Terminals

A terminal's command line says nothing about what was in it, so the script walks the terminal's process tree:

- If a known TUI was running (`yazi`, `nvim`, `vim`, `btop`, `htop`, `ranger`, `lf`), it is relaunched in its working directory, for example `ghostty --working-directory=~/Downloads -e yazi`.
- Otherwise the terminal reopens in the shell's last working directory.

Supported terminals: ghostty, kitty, foot, alacritty. Others are one line in `TERMINALS` in `omarchy_last_session/config.py`.

### Apps restore their own content

The plugin reopens windows. What is inside them comes back through each app's own persistence:

| App | What comes back |
| --- | --- |
| Firefox, Zen, Brave, Chromium, Chrome, Edge | tabs, through their own session restore |
| sioyek, okular, zathura | the document, at the last page |
| Obsidian | the last open vaults |
| VS Code | its own windows and the folders open in them |
| Dolphin | open tabs, with *Settings → Startup → "same locations as when closed"* |
| yazi, nvim in a terminal | the same directory |

## What it cannot do

- **The exact tiling layout.** Hyprland does not expose the split tree. Windows land on the right workspace and monitor and re-tile in saved left-to-right order. Floating windows are pixel-exact.
- **An app slower than thirty seconds to show a window.** The sweep ends as soon as every window is accounted for, so the wait costs nothing when they turn up, but one that has not mapped a window by then is left unplaced and out of its group. It is named in the log either way.
- **Unsaved in-app state.** Terminal scrollback, scroll positions and unsaved edits are gone. That needs the Wayland session-management protocol, which apps do not implement yet.
- **Several same-class windows from one process** (a browser's) are told apart by how close their titles are, because nothing else distinguishes them. A browser reopens its windows in whatever order it likes, so the title is what keeps each on its own monitor. Two windows showing the same thing, or one whose page changed completely while the session was closed, can still end up in each other's places.

## Configuration

Knobs in `omarchy_last_session/config.py`:

| Knob | Meaning |
| --- | --- |
| `EXCLUDE_CLASSES` | window classes never saved or restored |
| `TERMINALS` | terminal class → binary, working-directory flag, exec flag |
| `TUI_PROGRAMS` | programs relaunched inside their terminal |
| `RESTORE_FLAGS` | browsers, and the flag that makes each restore its own session |
| `SESSION_KEEPING_CLASSES` | apps that reopen their own windows: launched once per process and asked to quit at shutdown. Add your editor if it restores its own windows |
| `SAVE_INTERVAL` | daemon save period, 60 s |
| `SWEEP_TIMEOUT` | how long restore waits for windows, 30 s |

Environment variables, read by every command:

| Variable | Meaning |
| --- | --- |
| `OMARCHY_LAST_SESSION_EXCLUDE` | extra excluded classes, comma separated |
| `OMARCHY_LAST_SESSION_DIR` | state directory, default `~/.local/state/omarchy-last-session` |

To skip the restore on the next login without disabling the plugin:

```sh
touch ~/.local/state/omarchy-last-session/disabled
```

## Command line

The script works on its own, without the shell service:

```sh
~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session save
~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session restore
```

## Troubleshooting

- **What did the last shutdown save?** `~/.local/state/omarchy-last-session/last-shutdown.json`. The daemon overwrites `session.json` ninety seconds into the next login, so that copy is the one that still answers the question.
- **Nothing came back.** Check `session.json` in the state directory. An empty window list means that nothing was open when the power menu ran, or that the snapshot was taken after the windows were closed; wire the power menu as shown above. Restore also aborts when more than three windows are already open.
- **A window did not come back.** The script reports it on stderr, which the shell service forwards to the journal: `journalctl --user -t omarchy-shell | grep omarchy-last-session`.
- **Plugin status.** `omarchy plugin list --json | jq '.[] | select(.id == "io.github.asmyshlyaev177.last-session")'`.

## Development

```sh
python3 -m unittest discover -s tests -v
uvx ruff check .
uvx ruff format --check .
omarchy plugin validate .
```

The code is the `omarchy_last_session` package; `bin/omarchy-last-session` only starts it.

| Module | What it holds |
| --- | --- |
| `config` | every knob |
| `proc` | the `/proc` readers |
| `hypr` | `hyprctl` requests, Lua quoting, and views of the monitors and windows |
| `relaunch` | recovery of a window's relaunch command |
| `session` | the snapshot file, and the graceful quit at shutdown |
| `restore` | the restore pass |
| `cli` | the four commands |

The unit tests run without a compositor. `/proc` and `hyprctl` are read through small named functions in `proc` and `hypr`, so the tests substitute those at the module that owns them rather than mock Hyprland. One file per module, plus `tests/helpers.py` for the fixtures; a single file runs with `python3 -m unittest tests.test_restore -v`. The suite covers the command recovery restore depends on (Chromium's flattened argv, an AppImage's mount path, D-Bus activation, terminal working directories) and the restore pass itself: spawn order, the double-restore guard, the sweep, group rebuilding, monitor placement, and the Lua the compositor receives.

### Against a real compositor

```sh
tests/integration/run.sh                  # every live test
tests/integration/run.sh -k two_monitors  # a unittest -k filter
```

These run a real Hyprland with two headless monitors in a container, open real windows, and drive the script through its command line. Nothing is mocked. Two of the stand-in apps are single-instance: they serve every window from one process and reopen those windows themselves, which is what a browser and an editor do and where most of the mistakes have been. A test builds a session, saves it, boots a fresh compositor with the monitors in the other order, restores, and compares what came back against what was saved: workspaces, monitors, floating geometry, pinned and maximized state, groups, and that a browser is one process again. Another test undocks, restoring a two-monitor session onto one.

They need [Podman](https://podman.io) or Docker and a DRM render node. Mesa does the rendering and no GPU is needed, but the node has to exist, because the compositor's backend opens one to allocate buffers. The first run builds the image. On an Omarchy host the container mounts Omarchy's Hyprland defaults, so the windows meet the same rules as on the desktop. Set `OLS_CONTAINER=docker` where rootless Podman has no subuid range to map with.

The GitHub workflow runs the unit suite and the linters on hosted runners and leaves the live job dormant, because those runners have no DRM device. Set the repository variable `OLS_LIVE_RUNNER` to the label of a self-hosted runner with a GPU and the live job runs there.

To try a checkout as the installed plugin:

```sh
omarchy plugin add /path/to/omarchy-last-session --enable
```

## License

MIT
