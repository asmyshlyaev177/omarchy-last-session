# omarchy-last-session

Session restore for [Omarchy](https://omarchy.org): saves the windows you have open and brings them back after a reboot, shutdown, logout or crash. An Omarchy shell plugin for Hyprland 0.55+.

Every window is restored on the workspace and monitor it was on. Floating windows keep their position and size. Pinned, fullscreen and grouped windows come back in that state. Terminals reopen in their last working directory, and browsers keep their tabs. Saving is automatic, so there is no save step before you power off.

Python 3.9, standard library only. No compositor patches and no extra daemons.

## Install

Needs Omarchy 4 (Quattro) or newer.

```sh
omarchy plugin add https://github.com/asmyshlyaev177/omarchy-last-session.git --enable
```

It then saves a snapshot whenever a window opens, closes or moves, and restores it two seconds after the shell starts on your next login.

If the command ends with `omarchy-shell is not responding`, the plugin is installed but may be left disabled. Check `omarchy plugin list` and run `omarchy plugin enable io.github.asmyshlyaev177.last-session`.

### Leave some windows out

Set the classes your own autostart already launches in `~/.config/hypr/hyprland.lua`, so the service and the power menu both inherit them:

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

Whatever you added by hand stays: the `hl.env` line above, and the power menu actions below.

### What it writes

Its own state directory, `~/.local/state/omarchy-last-session`, and one file belonging to another app: before relaunching a Chromium-based browser it sets `profile.exit_type` to `Normal` in each profile's `Preferences`, because Chromium will not restore tabs after what it recorded as a crash. Nothing else in that file changes.

## Let apps save before the power goes

Omarchy closes every window about two seconds before it powers off. Most apps hold the shutdown themselves for as long as they need; VS Code remembers only the last window closed, so it has to be told to quit first. Add this to `~/.config/omarchy/extensions/omarchy-menu.jsonc`, creating the file if it does not exist:

```jsonc
{
  "system.logout":   {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-logout"},
  "system.reboot":   {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-reboot"},
  "system.shutdown": {"action":"~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-shutdown"},
}
```

Leave suspend and hibernate alone, they resume the live session. The menu reloads the file on save.

## What comes back

The plugin reopens windows. What is inside them comes from each app's own persistence:

| App | What comes back |
| --- | --- |
| Firefox, Zen, Brave, Chromium, Chrome, Edge | tabs, even after Omarchy killed them |
| sioyek, okular, zathura | the document, at the last page |
| Obsidian | the last open vaults |
| VS Code | its own windows and the folders open in them |
| Dolphin | open tabs, with *Settings → Startup → "same locations as when closed"* |
| kitty | its OS windows, tabs, splits and directories, with remote control on |
| a terminal | the shell's last directory, or a TUI (`yazi`, `nvim`, `vim`, `btop`, `htop`, `ranger`, `lf`) in its own |

Terminals supported out of the box: ghostty, kitty, foot, alacritty. Another is one line in `TERMINALS`.

Turning kitty's remote control on is what lets it describe its tabs and splits:

```conf
allow_remote_control socket-only
listen_on unix:@mykitty
```

## Limits

- The exact tiling layout. Windows land on the right workspace and monitor and re-tile in saved left-to-right order. Floating windows are pixel-exact.
- Unsaved in-app state: scrollback, scroll positions, unsaved edits.
- An app that takes more than thirty seconds to show a window. It is left unplaced and named in the log.
- One window from an app that serves every window from one process and reopens none of them itself, such as LibreOffice or GIMP.
- Titles and floating geometry changed in the last minute before a crash. A clean shutdown loses neither.
- Same-class windows from one process are told apart by their titles, so two showing the same thing can end up in each other's places. Every pairing is logged.

Restore refuses to run when more than three windows are already open, so enabling the plugin mid-session reopens nothing.

## Configuration

Knobs in `omarchy_last_session/config.py`:

| Knob | Meaning |
| --- | --- |
| `EXCLUDE_CLASSES` | window classes never saved or restored |
| `TERMINALS` | terminal class → binary, working-directory flag, exec flag |
| `TUI_PROGRAMS` | programs relaunched inside their terminal |
| `CHROMIUM_BROWSERS` | Chromium-based browsers and their profile directories |
| `SESSION_KEEPING_CLASSES` | apps `shutdown` sends SIGTERM to, so they write their windows down. Add your editor if it restores its own windows |
| `SINGLE_INSTANCE_CLASSES` | apps launched once per process, because a second launch joins the first |
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

To skip the next restore without disabling the plugin:

```sh
touch ~/.local/state/omarchy-last-session/disabled
```

## Command line

Four commands, which work without the shell service. `restore` runs at login and `daemon` after it; `save` and `shutdown` are for your own scripts.

```sh
~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session save
~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session restore
```

## Troubleshooting

Everything the plugin prints reaches the journal:

```sh
journalctl --user -t omarchy-shell -b | grep omarchy-last-session
```

| Question | Where to look |
| --- | --- |
| What did restore bring back, and where did it put things? | `last-restore.json`, plus one log line per pairing |
| What did the last shutdown save? | `last-shutdown.json`, written by `shutdown` only |
| Nothing came back | `session.json`. An empty window list means the desktop was empty for ten seconds before shutdown |
| A window did not come back | the log names it |
| Is the plugin running? | `omarchy plugin list --json \| jq '.[] \| select(.id == "io.github.asmyshlyaev177.last-session")'` |

All four files live in `~/.local/state/omarchy-last-session`.

## Development

```sh
python3 -m unittest discover -s tests -v
uvx ruff check . && uvx ruff format --check .
omarchy plugin validate .
tests/integration/run.sh                  # live, needs Podman or Docker and a DRM render node
tests/integration/run.sh -k two_monitors  # one live test
```

The code is the `omarchy_last_session` package; `bin/omarchy-last-session` only starts it. `CLAUDE.md` holds the notes for changing it.

| Module | What it holds |
| --- | --- |
| `config` | every knob |
| `proc` | the `/proc` readers |
| `hypr` | `hyprctl` requests, the event socket, Lua quoting, monitor and window views |
| `relaunch` | recovery of a window's relaunch command |
| `chromium` | the clean-exit mark a Chromium-based browser needs before it restores its tabs |
| `kitty` | a kitty instance's tabs and splits, rendered as a session file |
| `session` | the snapshot file, the daemon's schedule, the graceful quit at shutdown |
| `restore` | the restore pass |
| `cli` | the four commands |

The unit tests need no compositor: `/proc` and `hyprctl` are read through named functions the tests substitute. The live tests drive the plugin against a real Hyprland in a container, with stand-in apps for the shapes that break restore.

## License

MIT
