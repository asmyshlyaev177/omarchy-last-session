# omarchy-last-session

Omarchy shell plugin that saves the open windows and reopens them after a reboot. Python 3.9, standard library only, no build step. `manifest.json` declares one `service` kind whose entry point is `Service.qml`; the shell runs it inside quickshell. `bin/omarchy-last-session` is a launcher that puts the repo root on `sys.path` and calls `cli.main()`. The README documents behaviour for users; this file holds what an agent needs to change the code safely.

## Layout

| Path | Role |
| --- | --- |
| `omarchy_last_session/config.py` | every knob; `SETTINGS` is what the config file may set, each with the comment the plugin writes above it |
| `omarchy_last_session/proc.py` | `/proc` readers |
| `omarchy_last_session/hypr.py` | `hyprctl` requests, Lua quoting, monitor and window views, the socket2 `EventStream` |
| `omarchy_last_session/relaunch.py` | recovers a window's relaunch command |
| `omarchy_last_session/chromium.py` | clears Chromium's crash mark before a relaunch |
| `omarchy_last_session/kitty.py` | reads a kitty instance over remote control, renders its session file |
| `omarchy_last_session/session.py` | snapshot file, `SaveScheduler`, graceful quit at shutdown |
| `omarchy_last_session/restore/` | the restore pass, run by `__init__.py`: `launch`, then the `sweep` that pairs live windows with saved ones (`pairing`, `titles`) and moves them (`placement`), then `layout` for groups, workspace names and monitors |
| `omarchy_last_session/notification.py` | the toast restore shows while it runs, through Omarchy's `omarchy-notification-send` and `omarchy-notification-dismiss` |
| `omarchy_last_session/cli.py` | the six commands `save`, `restore`, `shutdown`, `daemon`, `config`, `menu` |
| `tests/` | unit tests, one file per module (`test_restore_<module>.py` for `restore/<module>.py`), fixtures in `tests/helpers.py` |
| `tests/integration/` | live suite against a real Hyprland in a container |
| `docs/preview.html` | source of the marketplace `preview.png` |

## Runtime facts

- `Service.qml` starts `restore` two seconds after the shell loads the plugin and starts `daemon` when restore exits. Both are children of the QML object, so a shell restart or a plugin disable ends them.
- Restore refuses to run when more than `MAX_PREEXISTING_WINDOWS` (3) windows are open. Every hot reload of the installed plugin re-runs restore, which then aborts with "session already populated".
- The daemon sleeps `DAEMON_INITIAL_DELAY` (90 s) before its first save, then wakes on placement events from Hyprland's socket2 and saves at most every `SAVE_INTERVAL` (60 s) otherwise. Vanished windows are written only after `SETTLE_DELAY` (10 s) of quiet, which outlasts Omarchy's close-all before poweroff.
- State lives in `~/.local/state/omarchy-last-session`: `session.json`, `last-restore.json` (the snapshot restore used), `last-shutdown.json` (written by `shutdown` only), and the `disabled` flag. The `state_dir` setting overrides the directory.
- Every state file goes through `session.open_private` and `session.write_private`: directory 0700, files 0600, no symlink followed, another user's directory refused. The marketplace review required this, so a new state file uses them too.
- A kitty taken out of `[terminals]` gets no session file and is relaunched per window from its command line (`session.write_kitty_sessions`), since the file's relaunch needs the table's binary. A kitty whose remote control is on gets a `kitty-<pid>.session` file in the state directory and a `kitty --session <path>` relaunch, which brings back its whole instance, so `session.does_launch_once` counts it. Without remote control there is no file and nothing changes. The file is one directive per line and pane titles come from whatever ran in the pane, so `kitty.is_one_line` keeps one from carrying a newline into it.
- `SINGLE_INSTANCE_CLASSES` decides how many launches a process gets, `SESSION_KEEPING_CLASSES` who gets a SIGTERM at shutdown, and the second is a subset of the first by construction: session keeping is the `[chromium-browsers]` classes plus `session_keeping`, single instance is that plus `single_instance`. LibreOffice and GIMP are in the second list only: a SIGTERM raises a save prompt and they come back offering document recovery.
- User settings come from `~/.config/omarchy/last-session.ini` (`config.CONFIG_FILE`, under `XDG_CONFIG_HOME`): INI so the file can carry comments. `[general]` holds the keys in `config.SETTINGS`; `[terminals]` and `[chromium-browsers]` are the tables in `config.TABLES`, one row per window class. `cli.main` writes the file with every default before its first command runs (`config.ensure_file`), which is the closest thing to "on install": Omarchy's installer runs no plugin code. A value in the file replaces its default outright, a section its whole table; only `exclude` is added to the shell's own classes, which stay hardcoded so a cleared list cannot make restore spawn a second shell. An unknown key or section, a number that is not one, or a table row that does not parse is warned about and skipped. A file that cannot be read at all (configparser error, bad UTF-8, a directory in its place) is warned about and leaves what was in force: the defaults at import, the last good read in the daemon, so a half-saved edit cannot send the next save to the default state dir. A deleted file means the defaults. Keys keep their case (`optionxform = str`) because window classes do, but section names and `[general]` keys match case-insensitively. Quotes around a value are dropped, `#` or `;` after a value is a comment, a relative `state_dir` is taken from home, a `~` in a profile dir is expanded so an absolute result wins the `os.path.join`. Numbers are bounded: `MINIMUMS` (save_interval at least 1, or the daemon spins) and `MAX_SECONDS` (10**9; `select` and `sleep` overflow at 2**63 ns and the daemon would die outside its retry). The environment variables `OMARCHY_LAST_SESSION_EXCLUDE` and `OMARCHY_LAST_SESSION_DIR` are gone.
- The daemon calls `config.reload_if_changed` once per wake and re-reads the file when its mtime or size changed, so an edit lands within `SAVE_INTERVAL` without a restart. `restore`, `save` and `shutdown` are fresh processes and read it at import.
- `config` opens the file through `omarchy-launch-config-editor`, which is what the README's `setup.config.last-session` menu entry runs; without that binary on PATH it names the file and exits 1. The menu reads only Omarchy's default JSONC and `~/.config/omarchy/extensions/omarchy-menu.jsonc`, so a plugin cannot add an entry itself, and nothing runs on removal to take one out. `menu` prints the rows (`cli.render_menu_rows`) with the paths of the copy that runs it, home written as `~`: the config row carries `when: [[ -d <plugin dir> ]]`, so it hides once the plugin is removed and the directory, unlike the launcher's path, is not something a layout change moves; the power rows override Omarchy's own and so never hide, and guard the shutdown call inside the action instead. `tests/test_cli.py` runs both guards through bash and checks the README shows exactly what the command prints for the standard install path.
- The sweep pairs the windows of one class in the same pass, best fit first (`restore.pairing.pair_arrivals`), once all of them have turned up and their titles have settled, or `title_settle` after the last one turned up; a lone window with one entry to take pairs at once. Paired one at a time in Hyprland's listing order, Brave windows swapped monitors in four of twelve boots in September 2026 (journal replay, 2026-09-25).
- Titles compare by shared words, not letters: letter by letter, unrelated long titles scored 0.22 to 0.31 against a saved Bybit title, above the 0.17 of that page's own early title 'Bybit'. A title has settled when its words, numbers aside, match the last pass, so a ticker or an unread count does not hold pairing up.
- Restore logs each launch, each window it may pair from the moment it turns up with every title change until it is paired, the pairing with its scores, a wait that ran out, and each entry that got no window with its saved title and workspace. The window's address ties its lines together. A missing window of a session-keeping app that reopened others also says how many it reopened, since then the app dropped it and restore could not have placed it.
- Omarchy's power actions close every window one at a time (`omarchy-hyprland-window-close-all`, in `hyprctl clients` order) and end the session 2 s later. A Chromium browser records a window whose close finishes while another of its windows is open as closed, and does not reopen it. On 2026-09-25 Brave lost its one-tab JobBot window in 2 of 4 logouts run without `shutdown`, and its session file held that window as closed 2 s before the logout ended. `shutdown` sends SIGTERM first, which makes Chromium quit as its Exit menu item does and keep every window, so for browsers the power rows are what keep windows, not what freshens the snapshot.
- Omarchy has no hook before a power action (its hooks are battery-low, font-set, post-boot, post-update, pre-refresh-pacman and theme-set), so the menu rows are the only place to run `shutdown` first.
- Chromium's session file (`~/.config/BraveSoftware/Brave-Browser/Default/Sessions/Session_*`, the last two runs) is SNSS: an 8-byte header, then per command a u16 size, a u8 id and the payload. Id 0 is {int32 window, int32 tab}, 6 a pickle holding the tab id and its page's URL and title, 16 and 17 a closed tab or window {int32 id, pad, int64 µs since 1601}.
- The toast is drawn by the shell's own notification service (`$OMARCHY_PATH/shell/plugins/notifications`), which is the freedesktop server here. Its card is a copy of the notification, so a freedesktop CloseNotification leaves it up (still on screen 1.5 s after one, 2026-09-25); `omarchy-notification-dismiss <summary>` goes through the shell's IPC and takes it down. A low-urgency card stays at least 5 s and at most 30 s whatever `-t` asks, so a restore that waits out a 30 s sweep outlives its toast.
- The shell lets toasts from the default app name, `omarchy-action`, through do-not-disturb, so restore sends under its own. Under DND the shell writes a toast straight into its history, which keeps the last ten, and a toast that was on screen lands there once it leaves, so each restore adds one entry either way. DND is on on this desktop (2026-09-25), so a restore here shows no toast. The shell mirrors each toast on screen to `~/.local/state/omarchy/notifications/<ms>-<id>.json` and moves it to `history/` when it leaves, which is how the toast was checked: on screen while restore's block ran and gone 0.1 s after it.
- `log()` writes to stdout and `warn()` to stderr. `Service.qml` forwards both to the journal. The live suite asserts that restore's stderr is empty, so a new diagnostic that is not an error goes through `log()`.
- `chromium.mark_clean_exit` is the one write outside the state directory. It sets `profile.exit_type` to `Normal` in every `*/Preferences` under the browser's user data directory. The README discloses it, and the marketplace form was answered on that basis.

## Verification

```sh
python3 -m unittest discover -s tests                                  # 363 tests, about a second
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

- `tests/__init__.py` points `config.CONFIG_FILE` at a file that does not exist and reloads, so the suite never reads this machine's config. It runs on the first `tests.` import, which comes after the package import in every test module, which is why it reloads rather than sets an environment variable. `ConfigFileCase` in `tests/helpers.py` gives a test a file of its own, reloaded on entry and on exit.
- Tests patch at the module that owns the call, for example `hypr.query`, `proc.read_cmdline` or `config.TITLE_SETTLE`, never `subprocess` or `open`.
- `tests/helpers.py` provides `client()`, `saved_window()`, `live_window()`, `grouped_clients()`, `pretend_runnable()` (patches `relaunch.is_runnable`, so no test depends on a binary being installed), `write_executable()`, `StateDirCase` (patches every state path and offers `save_with`, `read_session`, `write_session`) and `RestoreHarness`.
- `RestoreHarness.run_restore` runs the whole restore pass and returns every dispatch, `eval_lua` and `mark_clean_exit` call in one list, so order across the pass is asserted, not just membership.
- The daemon's event stream is tested against a real `socket.socketpair()`, which pins which events wake it and which are ignored.
- A bug fix comes with a test named after the behaviour it pins.
- The live suite's `run_script` writes the config file only when given a state dir; `test_the_config_file_is_written_on_first_run_and_an_edit_is_honoured` runs without one, so the launcher writes the template into the fake home and the test edits it as a user would.
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

To rehearse a boot without rebooting, run what the menu's Logout row runs, from a terminal other than VS Code's, because `shutdown` quits VS Code and its terminal with it. SDDM autologins only at startup here (`Relogin=false` in `/usr/lib/sddm/sddm.conf.d/default.conf`), so it shows the login screen, and logging in starts a fresh Hyprland whose restore runs as it does at boot. With `save` in place of `shutdown` it rehearses a desktop without the power rows, where Brave can drop a window.

```sh
~/.config/omarchy/plugins/io.github.asmyshlyaev177.last-session/bin/omarchy-last-session shutdown; omarchy-system-logout
journalctl --user -t omarchy-shell --since -5min | grep omarchy-last-session   # after logging back in
```

- Any file written under the installed directory hot-reloads the plugin: restore runs and aborts, and the daemon's 90 s delay starts again.
- `~/.config/omarchy/last-session.ini` here excludes jobbot's browser classes and omacal, and `~/.config/omarchy/extensions/omarchy-menu.jsonc` holds the `setup.config.last-session` entry.
- Find the daemon with `ps -C python3 -o pid,ppid,args | grep 'last-session daemon'`. A `pgrep -f` on that string also matches the shell running it.
- `omarchy plugin add <url> --enable` prints "omarchy-shell is not responding" because `omarchy-shell` gives its IPC two seconds and the plugin reload takes longer. The request still lands, but the plugin may be left disabled, and `omarchy plugin enable <id>` fixes it.
- Never edit anything under `/usr/share/omarchy`. Omarchy's own scripts are read there for reference only.

## Marketplace

- Listed through <https://github.com/omacom/omarchy-plugin-marketplace/issues/7527> as category System with tags hyprland, workspaces and system. The listing binds to one exact commit, and a newer commit reaches the marketplace only through its "verify newer upstream commit" form with the full 40-character SHA. Bump `version` in `manifest.json` for a release.
- The automated checks want `manifest.json`, `README.md` and `LICENSE` at the root, install and removal instructions in the README, and no `/tmp` state, `curl | sh`, `sudo` or unpinned remote code. The security baseline passed with no findings at commit `4ed3117`; the `pacman` call in `tests/integration/Dockerfile` was not flagged.
- `preview.png` at the root is rendered from `docs/preview.html` with the command in that file's header comment. The card shows it in a 344 by 175 box with a cover crop, so the image holds one large mark and the name and nothing small.
