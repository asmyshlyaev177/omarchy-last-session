# omarchy-last-session

Reopen your last session's windows on login. An [Omarchy](https://omarchy.org) shell plugin for Hyprland 0.55+.

Log out, reboot, or shut down. On the next login every window comes back on the workspace and monitor it was on, without stealing focus while it opens. Floating windows return to their position and size. Pinned, fullscreen and grouped windows come back in that state. Terminals reopen in their last working directory, and browsers keep their tabs.

The whole thing is a small Python package that talks to `hyprctl`, sleeps on Hyprland's event socket and reads `/proc`, plus a small shell service that starts it. No compositor patches, no extra daemons, no dependencies beyond Python 3.9.

## Install

Needs Omarchy 4 (Quattro) or newer.

```sh
omarchy plugin add https://github.com/asmyshlyaev177/omarchy-last-session.git --enable
```

From then on the plugin saves a snapshot the moment a window opens, closes or moves, and restores it two seconds after the shell starts on your next login. Nothing else needs wiring up: a reboot, a logout, a crash or the power button costs a few seconds of changes at most.

If the command ends with `omarchy-shell is not responding`, the shell took longer than the two seconds its command line waits to reload its plugins, but the plugin is installed. Check `omarchy plugin list`, and run `omarchy plugin enable io.github.asmyshlyaev177.last-session` if it shows as disabled.

### Optional: leave some windows out

Anything your own autostart launches should not be restored a second time. Set the excluded window classes in `~/.config/hypr/hyprland.lua`, so the service and the power menu both inherit them:

```lua
hl.env("OMARCHY_LAST_SESSION_EXCLUDE", "my-autostarted-app,another-class")
```

Find a window's class with `hyprctl clients -j | jq '.[].class'`.

## Update

```sh
omarchy plugin update io.github.asmyshlyaev177.last-session
```

## Remove

```sh
omarchy plugin remove io.github.asmyshlyaev177.last-session
rm -r ~/.local/state/omarchy-last-session
```

The first command stops the service and deletes the plugin folder. The second deletes the snapshots. Whatever you added by hand stays until you take it out again, namely the `hl.env` line in `hyprland.lua` and the power menu actions in `omarchy-menu.jsonc` from [Apps that need a moment to save](#apps-that-need-a-moment-to-save).

Outside its own folder the plugin writes to its state directory, and to one file of another app. Before it relaunches a Chromium-based browser it sets `profile.exit_type` to `Normal` in the `Preferences` file of each profile in that browser's user data directory, which is `~/.config/BraveSoftware/Brave-Browser` for Brave, or the `--user-data-dir` the browser was running with. Chromium refuses to restore its tabs after what it took for a crash, and a browser that Omarchy killed at shutdown has recorded one. Nothing else in that file changes, and no other app's files are written.

## How it works

The plugin has four commands. The shell service runs `restore` at login and then `daemon`; `save` and `shutdown` are there for the command line.

- **`save`** snapshots every mapped window: class, workspace, monitor, geometry, floating, pinned and fullscreen state, group membership, and a relaunch command recovered from `/proc/<pid>/cmdline` and `/proc/<pid>/cwd`. When the command line cannot be replayed (an AppImage's temporary mount, a D-Bus activation), the app's `.desktop` entry is used instead.
- **`restore`** relaunches each window through a Hyprland window rule that tracks the spawned process, so it opens on the right workspace silently. A sweep pass then places the windows that escape process tracking, from forking and single-instance apps. It matches them by window class, and by the program behind the window when the class has shifted, as Chromium's does between its desktop entry and a command line. Among windows of one class, the one whose process runs the saved command comes first, so two browsers on different profiles are never confused, and then the nearest title. A browser window that is still loading is titled Untitled, New Tab or about:blank, which says nothing, so it is given a few seconds to get its title before it is paired. Every window is launched, except those of an app that reopens its own windows (a browser, VS Code): such an app is launched once per process, because a second launch adds an empty window instead of the one it was meant to bring back. A Chromium-based browser is marked as cleanly exited before its launch: Chromium refuses to restore the last session after a crash, and a browser Omarchy killed while it asked about closing its tabs has recorded one, even though its session file is intact.
- **Groups** are rebuilt after the sweep. The first member of each saved group becomes a group and the rest are added to it by address, in saved left-to-right order. Nothing depends on focus or on the order windows turn up in, so everything can be launched in one pass and slow apps overlap instead of queueing.
- **Monitors** are restored last. Workspaces bind to no monitor, so at boot they pile onto the focused one. Each is moved as a whole onto the monitor it was saved on, by name, because Hyprland renumbers monitors across boots. Floating windows are placed by their offset into that monitor, so they survive the monitor moving in the layout or being unplugged. A single-monitor machine skips the pass. Whichever workspace moves last is the one you land on; focus is not put back where you left it.
- **`shutdown`** is optional. It saves, keeps a copy of that snapshot as `last-shutdown.json`, then sends SIGTERM to the browsers and VS Code and waits up to ten seconds for them to exit, so VS Code writes all of its windows down. Run it from a power menu action, as shown below.
- **`daemon`** sleeps on Hyprland's event socket, starting ninety seconds after login so a restore in progress is not snapshotted half done. It costs nothing until the compositor reports a window opening, closing, moving, floating, grouping or going fullscreen, and saves at once when it does. It ignores the events that are not placement, because a page with a live ticker retitles several times a second. Windows that vanished are saved only once the desktop has been still for ten seconds, which outlasts the two seconds between the power menu closing every window and the poweroff, so a half-closed desktop is never written, and the socket closing with the compositor stops the daemon rather than recording a teardown. Titles and floating geometry change without any event, so it also saves once a minute.

Restore refuses to run into a desktop that already has more than three windows open, so enabling the plugin mid-session or restarting the shell does not reopen anything.

### Apps that need a moment to save

Omarchy closes every window about two seconds before it powers off, which is not long for an app with state to write. Almost none of them need help from this plugin, because the wait is already negotiated one layer down: an app that needs time registers a delay inhibitor with logind, and Omarchy ships a drop-in that holds the power off for up to fifteen seconds while it finishes. VS Code registers one, and gets its time, but that is not what loses its windows: Omarchy closes its windows one at a time first, and VS Code remembers only the last one it closed. With one window open that changes nothing. With several, the others come back only if VS Code is told to quit before its windows are closed, which is what the `shutdown` command does. To run it from the power menu, add this to `~/.config/omarchy/extensions/omarchy-menu.jsonc` (create the file if it does not exist):

```jsonc
{
  "system.logout":   {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-logout"},
  "system.reboot":   {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-reboot"},
  "system.shutdown": {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-shutdown"},
}
```

Leave suspend and hibernate alone. They resume the live session. The menu reloads the file on save.

An app that registers nothing is the one to watch, and Chromium is the example. It cannot be waited for, because what stops it exiting is its own "close N tabs?" dialog, so it is relaunched with its crash mark cleared instead. To see what an app does, look for it in `systemd-inhibit --list`.

### Terminals

A terminal's command line says nothing about what was in it, so the plugin walks the terminal's process tree:

- If a known TUI was running (`yazi`, `nvim`, `vim`, `btop`, `htop`, `ranger`, `lf`), it is relaunched in its working directory, for example `ghostty --working-directory=~/Downloads -e yazi`.
- Otherwise the terminal reopens in the shell's last working directory.

Supported terminals: ghostty, kitty, foot, alacritty. Others are one line in `TERMINALS` in `omarchy_last_session/config.py`.

### Apps restore their own content

The plugin reopens windows. What is inside them comes back through each app's own persistence:

| App | What comes back |
| --- | --- |
| Firefox, Zen, Brave, Chromium, Chrome, Edge | tabs, through their own session restore, the Chromium-based ones even after Omarchy killed them |
| sioyek, okular, zathura | the document, at the last page |
| Obsidian | the last open vaults |
| VS Code | its own windows and the folders open in them |
| Dolphin | open tabs, with *Settings → Startup → "same locations as when closed"* |
| yazi, nvim in a terminal | the same directory |

## What it cannot do

- **The exact tiling layout.** Hyprland does not expose the split tree. Windows land on the right workspace and monitor and re-tile in saved left-to-right order. Floating windows are pixel-exact.
- **An app slower than thirty seconds to show a window.** The sweep ends as soon as every window is accounted for, so the wait costs nothing when they turn up, but one that has not mapped a window by then is left unplaced and out of its group. It is named in the log either way.
- **A title or a floating window's geometry from the last minute.** Where a window sits is saved the moment it changes, so a shutdown loses none of that, but the two things no event reports are only picked up by the periodic save.
- **Unsaved in-app state.** Terminal scrollback, scroll positions and unsaved edits are gone. That needs the Wayland session-management protocol, which apps do not implement yet.
- **Several same-class windows from one process** (a browser's) are told apart by how close their titles are, because nothing else distinguishes them. A browser reopens its windows in whatever order it likes, so the title is what keeps each on its own monitor. Two windows showing the same thing, or one whose page changed completely while the session was closed, or one that takes longer than a few seconds to show its title, can still end up in each other's places. Each pairing is logged, so the journal says which.

## Configuration

Knobs in `omarchy_last_session/config.py`:

| Knob | Meaning |
| --- | --- |
| `EXCLUDE_CLASSES` | window classes never saved or restored |
| `TERMINALS` | terminal class → binary, working-directory flag, exec flag |
| `TUI_PROGRAMS` | programs relaunched inside their terminal |
| `CHROMIUM_BROWSERS` | Chromium-based browsers and their profile directories: relaunched with `--restore-last-session` and marked as cleanly exited first |
| `SESSION_KEEPING_CLASSES` | apps that reopen their own windows: launched once per process and asked to quit at shutdown. Add your editor if it restores its own windows |
| `SETTLE_DELAY` | how long vanished windows stay unsaved, 10 s |
| `SAVE_INTERVAL` | how often the daemon saves when no event has said to, 60 s |
| `SWEEP_TIMEOUT` | how long restore waits for windows, 30 s |
| `TITLE_SETTLE` | how long a browser window still loading may wait for its title, 5 s |
| `MAX_PREEXISTING_WINDOWS` | how many windows may already be open before restore refuses to run, 3 |

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

The commands work on their own, without the shell service:

```sh
~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session save
~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session restore
```

## Troubleshooting

- **What did restore bring back, and where did it put things?** `~/.local/state/omarchy-last-session/last-restore.json` is the snapshot it used; the daemon overwrites `session.json` ninety seconds into the login, so that copy is the one that still answers. Every pairing is logged with both titles, both workspaces and its scores: `journalctl --user -t omarchy-shell -b | grep omarchy-last-session`.
- **What did the last shutdown save?** `last-shutdown.json` in the same directory, written by the `shutdown` command only.
- **Nothing came back.** Check `session.json` in the state directory. An empty window list means the desktop had been empty for ten seconds or more before the shutdown. Restore also aborts when more than three windows are already open.
- **A window did not come back.** The plugin reports it on stderr, which the shell service forwards to the journal: `journalctl --user -t omarchy-shell | grep omarchy-last-session`.
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
| `hypr` | `hyprctl` requests, the event socket, Lua quoting, and views of the monitors and windows |
| `relaunch` | recovery of a window's relaunch command |
| `chromium` | the clean-exit mark a Chromium-based browser needs before it restores its tabs |
| `session` | the snapshot file, and the graceful quit at shutdown |
| `restore` | the restore pass |
| `cli` | the four commands |

The unit tests run without a compositor. `/proc` and `hyprctl` are read through small named functions in `proc` and `hypr`, so the tests substitute those at the module that owns them rather than mock Hyprland. One file per module, plus `tests/helpers.py` for the fixtures; a single file runs with `python3 -m unittest tests.test_restore -v`. The suite covers the command recovery restore depends on (Chromium's flattened argv, an AppImage's mount path, D-Bus activation, terminal working directories) and the restore pass itself: spawn order, the double-restore guard, the sweep, group rebuilding, monitor placement, and the Lua the compositor receives. The daemon's event stream runs against a real socket pair rather than a mock, so which events wake it, and which are ignored as too chatty, are pinned by tests.

### Against a real compositor

```sh
tests/integration/run.sh                  # every live test
tests/integration/run.sh -k two_monitors  # a unittest -k filter
```

These run a real Hyprland with two headless monitors in a container, open real windows, and drive the script through its command line. Nothing is mocked. Two of the stand-in apps are single-instance: they serve every window from one process and reopen those windows themselves, which is what a browser and an editor do and where most of the mistakes have been. A third stands in for Steam, whose window belongs to a helper started by relative path, so restore has to find the client through a desktop entry and not through the shortcut of the game it was started for. A test builds a session, saves it, boots a fresh compositor with the monitors in the other order, restores, and compares what came back against what was saved: workspaces, monitors, floating geometry, pinned and maximized state, groups, and that a browser is one process again. Another test undocks, restoring a two-monitor session onto one.

They need [Podman](https://podman.io) or Docker and a DRM render node. Mesa does the rendering and no GPU is needed, but the node has to exist, because the compositor's backend opens one to allocate buffers. The first run builds the image. On an Omarchy host the container mounts Omarchy's Hyprland defaults, so the windows meet the same rules as on the desktop. Set `OLS_CONTAINER=docker` where rootless Podman has no subuid range to map with.

The GitHub workflow runs the unit suite and the linters on hosted runners and leaves the live job dormant, because those runners have no DRM device. Set the repository variable `OLS_LIVE_RUNNER` to the label of a self-hosted runner with a GPU and the live job runs there.

To try a checkout as the installed plugin:

```sh
omarchy plugin add /path/to/omarchy-last-session --enable
```

That clones, so it installs the last commit.

## License

MIT
