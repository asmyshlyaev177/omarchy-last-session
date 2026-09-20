# omarchy-last-session

Omarchy shell plugin that saves the open windows and reopens them after a reboot. Python 3.9, standard library only, no build step. `manifest.json` declares one `service` kind whose entry point is `Service.qml`; the shell runs it inside quickshell. `bin/omarchy-last-session` is a launcher that puts the repo root on `sys.path` and calls `cli.main()`. The README documents behaviour for users; this file holds what an agent needs to change the code safely.

## Layout

| Path | Role |
| --- | --- |
| `omarchy_last_session/config.py` | every knob and environment variable |
| `omarchy_last_session/proc.py` | `/proc` readers |
| `omarchy_last_session/hypr.py` | `hyprctl` requests, Lua quoting, monitor and window views, the socket2 `EventStream` |
| `omarchy_last_session/relaunch.py` | recovers a window's relaunch command |
| `omarchy_last_session/chromium.py` | clears Chromium's crash mark before a relaunch |
| `omarchy_last_session/kitty.py` | reads a kitty instance over remote control, renders its session file |
| `omarchy_last_session/session.py` | snapshot file, `SaveScheduler`, graceful quit at shutdown |
| `omarchy_last_session/restore.py` | the restore pass, including the sweep that pairs live windows with saved ones |
| `omarchy_last_session/cli.py` | the four commands `save`, `restore`, `shutdown`, `daemon` |
| `tests/` | unit tests, one file per module, fixtures in `tests/helpers.py` |
| `tests/integration/` | live suite against a real Hyprland in a container |
| `docs/preview.html` | source of the marketplace `preview.png` |

## Runtime facts

- `Service.qml` starts `restore` two seconds after the shell loads the plugin and starts `daemon` when restore exits. Both are children of the QML object, so a shell restart or a plugin disable ends them.
- Restore refuses to run when more than `MAX_PREEXISTING_WINDOWS` (3) windows are open. Every hot reload of the installed plugin re-runs restore, which then aborts with "session already populated".
- The daemon sleeps `DAEMON_INITIAL_DELAY` (90 s) before its first save, then wakes on placement events from Hyprland's socket2 and saves at most every `SAVE_INTERVAL` (60 s) otherwise. Vanished windows are written only after `SETTLE_DELAY` (10 s) of quiet, which outlasts Omarchy's close-all before poweroff.
- State lives in `~/.local/state/omarchy-last-session`: `session.json`, `last-restore.json` (the snapshot restore used), `last-shutdown.json` (written by `shutdown` only), and the `disabled` flag. `OMARCHY_LAST_SESSION_DIR` overrides the directory.
- Every state file goes through `session.open_private` and `session.write_private`: directory 0700, files 0600, no symlink followed, another user's directory refused. The marketplace review required this, so a new state file uses them too.
- A kitty whose remote control is on gets a `kitty-<pid>.session` file in the state directory and a `kitty --session <path>` relaunch, which brings back its whole instance, so `session.does_launch_once` counts it. Without remote control there is no file and nothing changes. The file is one directive per line and pane titles come from whatever ran in the pane, so `kitty.is_one_line` keeps one from carrying a newline into it.
- `SINGLE_INSTANCE_CLASSES` decides how many launches a process gets, `SESSION_KEEPING_CLASSES` who gets a SIGTERM at shutdown, and the second is a subset of the first. LibreOffice and GIMP are in the first only: a SIGTERM raises a save prompt and they come back offering document recovery.
- Excluded classes come from `OMARCHY_LAST_SESSION_EXCLUDE`. On this machine it is set with `hl.env(...)` in `~/.config/hypr/hyprland.lua`, which is the only way the shell-spawned daemon inherits it.
- `log()` writes to stdout and `warn()` to stderr. `Service.qml` forwards both to the journal. The live suite asserts that restore's stderr is empty, so a new diagnostic that is not an error goes through `log()`.
- `chromium.mark_clean_exit` is the one write outside the state directory. It sets `profile.exit_type` to `Normal` in every `*/Preferences` under the browser's user data directory. The README discloses it, and the marketplace form was answered on that basis.

## Verification

```sh
python3 -m unittest discover -s tests                                  # 261 tests, under a second
uv run --no-project --python 3.9 python -m unittest discover -s tests  # the CI's 3.9 leg
uvx ruff check . && uvx ruff format --check .                          # config in pyproject.toml
omarchy plugin validate .
OLS_CONTAINER=podman tests/integration/run.sh                          # live suite, about 75 s
```

CI runs the unit suite on Python 3.9 and 3.13 and the two ruff checks. The live job only runs when the repository variable `OLS_LIVE_RUNNER` names a self-hosted runner with a DRM render node. Keep the code 3.9 compatible, so no `match`, no `X | Y` unions and no `zip(strict=...)`.

`run.sh` bind-mounts the repo live, so run it from a frozen copy while the tree is being edited (`git archive HEAD | tar -x -C <scratch dir>`, plus an `rsync` of uncommitted work).

## Writing

The README is for users: what it does, how to install it, how to configure it. No internals, no war stories. Everything an agent needs goes here instead, one line per fact.

Comments explain what a name cannot. No comment restates the line under it, no docstring restates the function name, and a behaviour worth pinning gets a test rather than a paragraph. Markdown here is one line per bullet or paragraph, never hard-wrapped.

## Test conventions

- Tests patch at the module that owns the call, for example `hypr.query`, `proc.read_cmdline` or `config.TITLE_SETTLE`, never `subprocess` or `open`.
- `tests/helpers.py` provides `client()`, `saved_window()`, `live_window()`, `grouped_clients()`, `pretend_runnable()` (patches `relaunch.is_runnable`, so no test depends on a binary being installed), `write_executable()` and `StateDirCase` (patches every state path and offers `save_with`, `read_session`, `write_session`).
- `test_restore.py` runs the restore pass through a harness that records every dispatch, `eval_lua` and `mark_clean_exit` call in one list, so order across the pass is asserted, not just membership.
- The daemon's event stream is tested against a real `socket.socketpair()`, which pins which events wake it and which are ignored.
- A bug fix comes with a test named after the behaviour it pins.
- `tests/integration/bin` holds the stand-in apps: a browser, an editor, Steam and an office suite. Each reproduces one shape restore has to handle, taken from what the real app does on this desktop.
- Every session file `test_kitty.py` expects was replayed into a real kitty and the instance it built compared with the one it came from, so those shapes are what kitty does. Re-check against a real kitty before changing one.

## Trying a change on this desktop

The installed plugin at `~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session` is a git clone of the GitHub repository, not of this checkout. `omarchy plugin update io.github.asmyshlyaev177.last-session` fast-forwards it after a push. For uncommitted work, copy the tree over it and restart the shell:

```sh
rsync -a --delete --exclude='.git/' --exclude='__pycache__/' --exclude='.ruff_cache/' \
  ./ ~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/
omarchy-restart-shell
```

The daemon's first save lands 90 s later. The README is public and does not carry this recipe.

- Any file written under the installed directory hot-reloads the plugin: restore runs and aborts, and the daemon's 90 s delay starts again.
- Find the daemon with `ps -C python3 -o pid,ppid,args | grep 'last-session daemon'`. A `pgrep -f` on that string also matches the shell running it.
- `omarchy plugin add <url> --enable` prints "omarchy-shell is not responding" because `omarchy-shell` gives its IPC two seconds and the plugin reload takes longer. The request still lands, but the plugin may be left disabled, and `omarchy plugin enable <id>` fixes it.
- Never edit anything under `/usr/share/omarchy`. Omarchy's own scripts are read there for reference only.

## Marketplace

- Listed through https://github.com/omacom/omarchy-plugin-marketplace/issues/7527 as category System with tags hyprland, workspaces and system. The listing binds to one exact commit, and a newer commit reaches the marketplace only through its "verify newer upstream commit" form with the full 40-character SHA. Bump `version` in `manifest.json` for a release.
- The automated checks want `manifest.json`, `README.md` and `LICENSE` at the root, install and removal instructions in the README, and no `/tmp` state, `curl | sh`, `sudo` or unpinned remote code. The security baseline passed with no findings at commit `4ed3117`; the `pacman` call in `tests/integration/Dockerfile` was not flagged.
- `preview.png` at the root is rendered from `docs/preview.html` with the command in that file's header comment. The card shows it in a 344 by 175 box with a cover crop, so the image holds one large mark and the name and nothing small.
