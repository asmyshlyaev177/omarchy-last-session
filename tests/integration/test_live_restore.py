"""Live tests: a real Hyprland with two headless monitors, real windows, and
the plugin driven through its command line. Nothing is mocked.

Run them with tests/integration/run.sh. They need OLS_LIVE_TESTS=1 plus
Hyprland, labwc and foot on PATH, so discovery from the repository root skips
them. Hyprland's backend needs a DRM device, so it runs nested in a headless
labwc on the host's render node.
"""

import glob
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "bin", "omarchy-last-session")
CONFIG = os.path.join(HERE, "hyprland.lua")
LIVE = os.environ.get("OLS_LIVE_TESTS") == "1" and all(
    shutil.which(binary) for binary in ("Hyprland", "labwc", "foot")
)
MONITORS = ("MON-A", "MON-B")
# Kitty appends its pid, so every instance answers on its own socket.
KITTY_SOCKET = "olskitty"


def wait_for(probe, what, timeout=20, log=lambda: ""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}\n{log()}")


def address(client):
    return f"'address:{client['address']}'"


class Compositor:
    """labwc as the parent, Hyprland nested in it. boot() can follow
    shutdown() for the next login; labwc stays up throughout."""

    def __init__(self, workdir):
        self.home = os.path.join(workdir, "home")
        os.makedirs(self.home)
        # Hyprland's socket path is the runtime dir plus a 60-character
        # instance signature, and a Unix socket path holds 107 bytes.
        self.runtime_dir = tempfile.mkdtemp(prefix="ols", dir="/tmp")
        self.parent = None
        self.hypr = None
        self.env = None
        self.logs = []

    def _base_env(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE")
        }
        env.update(
            HOME=self.home,
            XDG_RUNTIME_DIR=self.runtime_dir,
            XDG_CONFIG_HOME=os.path.join(self.home, ".config"),
            XDG_STATE_HOME=os.path.join(self.home, ".local", "state"),
            PATH=os.path.join(HERE, "bin") + ":" + env.get("PATH", "/usr/bin"),
        )
        return env

    def _log(self, name):
        self.logs.append(open(os.path.join(self.home, f"{name}.log"), "a"))
        return self.logs[-1]

    def requests(self):
        """Every hyprctl request the script sent, recorded by bin/hyprctl."""
        try:
            with open(os.path.join(self.home, "hyprctl.log")) as f:
                return "".join(line for line in f if not line.startswith("-j "))
        except OSError:
            return ""

    def log_tail(self, name, lines=40):
        try:
            with open(os.path.join(self.home, f"{name}.log")) as f:
                return "".join(f.readlines()[-lines:])
        except OSError:
            return ""

    def _start_parent(self):
        env = dict(
            self._base_env(), WLR_BACKENDS="headless", WLR_RENDERER="gles2", WLR_LIBINPUT_NO_DEVICES="1"
        )
        self.parent = subprocess.Popen(
            ["labwc"], env=env, stdout=self._log("labwc"), stderr=subprocess.STDOUT
        )
        wait_for(
            lambda: os.path.exists(os.path.join(self.runtime_dir, "wayland-0")),
            "labwc's socket",
            log=lambda: self.log_tail("labwc"),
        )

    def boot(self, monitors):
        if self.parent is None:
            self._start_parent()
        shutil.rmtree(os.path.join(self.runtime_dir, "hypr"), ignore_errors=True)
        env = dict(
            self._base_env(),
            WAYLAND_DISPLAY="wayland-0",
            HYPRLAND_NO_SD_NOTIFY="1",
            HYPRLAND_NO_SD_VARS="1",
            HYPRLAND_NO_CRASHREPORTER="1",
            HYPRLAND_NO_RT="1",
        )
        self.hypr = subprocess.Popen(
            ["Hyprland", "--config", CONFIG], env=env, stdout=self._log("hyprland"), stderr=subprocess.STDOUT
        )
        self.env = dict(
            env,
            HYPRLAND_INSTANCE_SIGNATURE=wait_for(
                self._signature, "Hyprland's socket", log=lambda: self.log_tail("hyprland")
            ),
        )
        wait_for(
            lambda: self.hyprctl("version", check=False),
            "hyprctl to answer",
            log=lambda: self.log_tail("hyprland"),
        )
        for name in monitors:
            self.hyprctl("output", "create", "headless", name)
        wait_for(
            lambda: {m["name"] for m in self.json("monitors")} == set(monitors),
            "the monitors",
            log=lambda: self.log_tail("hyprland"),
        )
        return self

    def _signature(self):
        sockets = glob.glob(os.path.join(self.runtime_dir, "hypr", "*", ".socket.sock"))
        return os.path.basename(os.path.dirname(sockets[0])) if sockets else None

    def hyprctl(self, *args, check=True):
        done = subprocess.run(["hyprctl", *args], env=self.env, capture_output=True, text=True, timeout=20)
        if check and done.returncode != 0:
            raise AssertionError(f"hyprctl {' '.join(args)} failed: {done.stdout}{done.stderr}")
        return done.stdout.strip() if done.returncode == 0 else ""

    def json(self, cmd):
        return json.loads(self.hyprctl("-j", cmd))

    def dispatch(self, lua):
        reply = self.hyprctl("dispatch", lua)
        if not reply.startswith("ok"):
            raise AssertionError(f"dispatch rejected: {reply}\n  {lua}")

    def lua(self, code):
        reply = self.hyprctl("eval", code)
        if reply != "ok":
            raise AssertionError(f"eval rejected: {reply}\n  {code}")

    def clients(self):
        return [c for c in self.json("clients") if c.get("mapped") and c.get("class")]

    def window(self, client):
        return next(c for c in self.clients() if c["address"] == client["address"])

    def open_windows(self, cmd, count=1):
        """exec cmd on the focused workspace and wait for its windows."""
        before = {c["address"] for c in self.clients()}
        self.dispatch(f"hl.dsp.exec_cmd([[{cmd}]])")

        def arrived():
            new = [c for c in self.clients() if c["address"] not in before]
            return new if len(new) >= count else None

        return wait_for(arrived, f"{count} window(s) for {cmd}")

    def open_window(self, cmd):
        return self.open_windows(cmd)[0]

    def kitten(self, pid, *args, check=True):
        """One remote control request to the kitty running as `pid`. A kitty
        that is still starting has no socket yet, so a caller that is waiting
        for one asks with check=False."""
        address = "unix:@{}-{}".format(KITTY_SOCKET, pid)
        done = subprocess.run(
            ["kitten", "@", "--to", address, *args], env=self.env, capture_output=True, text=True, timeout=30
        )
        if check and done.returncode != 0:
            raise AssertionError("kitten {} failed: {}{}".format(" ".join(args), done.stdout, done.stderr))
        return done.stdout if done.returncode == 0 else ""

    def config_file(self):
        return os.path.join(self.home, ".config", "omarchy", "last-session.ini")

    def run_script(self, action, state_dir=None):
        """The state directory reaches the script the way it does on a
        desktop: through the config file under XDG_CONFIG_HOME. Without one
        the file is left as it is, or as the script itself writes it."""
        if state_dir is not None:
            os.makedirs(os.path.dirname(self.config_file()), exist_ok=True)
            with open(self.config_file(), "w") as f:
                f.write(f"[general]\nstate_dir = {state_dir}\n")
        env = dict(self.env, OLS_HYPRCTL_LOG=os.path.join(self.home, "hyprctl.log"))
        return subprocess.run(
            [sys.executable, SCRIPT, action], env=env, capture_output=True, text=True, timeout=180
        )

    def shutdown(self):
        """The compositor exiting closes every client, as a logout does."""
        if self.hypr is None:
            return
        if self.hypr.poll() is None:
            self.hyprctl("dispatch", "hl.dsp.exit()", check=False)
            try:
                self.hypr.wait(15)
            except subprocess.TimeoutExpired:
                self.hypr.kill()
                self.hypr.wait()
        self.hypr = None
        self.env = None
        for sock in glob.glob(os.path.join(self.runtime_dir, "foot-*.sock")):
            pathlib.Path(sock).unlink(missing_ok=True)

    def close(self):
        self.shutdown()
        if self.parent is not None and self.parent.poll() is None:
            self.parent.terminate()
            try:
                self.parent.wait(10)
            except subprocess.TimeoutExpired:
                self.parent.kill()
        for log in self.logs:
            log.close()
        shutil.rmtree(self.runtime_dir, ignore_errors=True)


def editor_windows(session):
    """Each of the editor's windows, by title and workspace. A spare launched
    on top of the running instance shows up here with no project in its
    title."""
    return {w["title"]: w["workspace"]["id"] for w in session["windows"] if w["class"] == "code"}


def tabs_by_monitor(session):
    """Which monitor each of the browser's windows came back on, keyed by the
    tab number in its title, which survives the title drifting around it."""
    return {
        w["title"].split()[1]: w["monitor_name"]
        for w in session["windows"]
        if app_name(w["class"]) == "chromium"
    }


def kitty_shape(listing):
    """Every OS window, tab and split of a kitty instance, with each pane named
    by its working directory so two instances compare."""
    return [
        [
            (tab["title"], tab["layout"], name_panes((tab.get("layout_state") or {}).get("pairs"), tab))
            for tab in os_window["tabs"]
        ]
        for os_window in json.loads(listing)
    ]


def name_panes(node, tab):
    """The split tree, with pane ids replaced by the directory each pane is in:
    ids are assigned in the order panes open and say nothing across restarts."""
    cwds = {window["id"]: window["cwd"] for window in tab["windows"]}
    if node is None or isinstance(node, int):
        return cwds.get(node)
    named = {side: name_panes(node[side], tab) for side in ("one", "two") if side in node}
    named["side_by_side"] = node.get("horizontal", True)
    return named


def app_name(cls):
    """The browser comes back under its other app id, chromium-browser rather
    than chromium, exactly as the real one does. Everything else about the
    window still has to match."""
    return "chromium" if cls.startswith("chromium") else cls


def shape(session):
    """What restore must reproduce, in a form that survives a reboot: no
    addresses, pids, titles, or tiled geometry. The command carries a
    terminal's working directory."""
    rows = []
    for w in session["windows"]:
        geometry = (tuple(w["at"]), tuple(w["size"])) if w["floating"] else None
        rows.append(
            (
                app_name(w["class"]),
                w["workspace"]["id"],
                w["monitor_name"],
                w["floating"],
                geometry,
                w["pinned"],
                w["fullscreen"],
                w["cmd"],
            )
        )
    return sorted(rows)


def group_shapes(session):
    members = {}
    for w in session["windows"]:
        if w.get("group") is not None:
            members.setdefault(w["group"], []).append(w)
    return sorted(
        (rows[0]["workspace"]["id"], tuple(sorted(app_name(w["class"]) for w in rows)))
        for rows in members.values()
    )


@unittest.skipUnless(LIVE, "live tests need OLS_LIVE_TESTS=1, Hyprland, labwc and foot")
class LiveRestore(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="ols-live-")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.comp = Compositor(self.work)
        self.addCleanup(self.comp.close)
        self.project = os.path.join(self.work, "project")
        os.makedirs(self.project)

    def build_session(self):
        """What a user had open across two monitors: a terminal in a project,
        a maximized window, tiled, floating and pinned windows, a group, and a
        browser whose second window sits in that group."""
        c = self.comp
        # workspace 1, on MON-A
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        c.open_window(f"foot -D {self.project}")
        big = address(c.open_window("foot --app-id=big"))
        c.dispatch(f"hl.dsp.window.fullscreen({{ mode = 'maximized', action = 'set', window = {big} }})")

        # workspace 2, on MON-B
        c.dispatch("hl.dsp.focus({ workspace = 2 })")
        c.open_window("foot --app-id=notes")
        calc = c.open_window("foot --app-id=calc")
        c.dispatch(f"hl.dsp.window.float({{ action = 'on', window = {address(calc)} }})")
        c.dispatch(f"hl.dsp.window.resize({{ x = 500, y = 300, window = {address(calc)} }})")
        c.dispatch(f"hl.dsp.window.move({{ x = 2020, y = 100, window = {address(calc)} }})")
        wait_for(lambda: c.window(calc)["at"] == [2020, 100], "the floating window to settle on MON-B")
        assert c.window(calc)["size"] == [500, 300], c.window(calc)

        pip = address(c.open_window("foot --app-id=pip"))
        c.dispatch(f"hl.dsp.window.float({{ action = 'on', window = {pip} }})")
        c.dispatch(f"hl.dsp.window.resize({{ x = 400, y = 200, window = {pip} }})")
        c.dispatch(f"hl.dsp.window.move({{ x = 3000, y = 600, window = {pip} }})")
        c.dispatch(f"hl.dsp.window.pin({{ action = 'on', window = {pip} }})")

        # workspace 3 is created on the focused monitor, so go through 1
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        c.dispatch("hl.dsp.focus({ workspace = 3 })")
        alpha = address(c.open_window("foot --app-id=alpha"))
        c.dispatch(f"hl.dsp.group.toggle({{ window = {alpha} }})")
        c.open_window("foot --app-id=beta")

        # the browser reopens its own second window, which belongs to the group
        with open(os.path.join(c.home, ".fakebrowser-windows"), "w") as f:
            f.write("2\n")
        c.dispatch("hl.dsp.focus({ workspace = 2 })")
        tab = address(c.open_windows("chromium", count=2)[1])
        c.dispatch(f"hl.dsp.window.move({{ workspace = 3, follow = false, window = {tab} }})")
        c.lua(f"hl.get_window({alpha}).group:add(hl.get_window({tab}))")
        c.dispatch("hl.dsp.focus({ workspace = 2 })")

    def write_desktop_entry(self, name, exec_line):
        apps = os.path.join(self.comp.home, ".local", "share", "applications")
        os.makedirs(apps, exist_ok=True)
        with open(os.path.join(apps, name), "w") as f:
            f.write(f"[Desktop Entry]\nExec={exec_line}\n")

    def write_kitty_config(self):
        """Remote control is what lets a kitty describe its own tabs and
        splits; it is off until the user turns it on, as it is on a desktop."""
        path = os.path.join(self.comp.home, ".config", "kitty", "kitty.conf")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("allow_remote_control socket-only\nlisten_on unix:@{}\n".format(KITTY_SOCKET))

    def snapshot(self, action, name):
        state = os.path.join(self.work, name)
        done = self.comp.run_script(action, state)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(state, "session.json")) as f:
            return json.load(f)

    def test_session_round_trips_across_a_reboot(self):
        self.comp.boot(MONITORS)
        self.build_session()
        saved = self.snapshot("shutdown", "state")
        self.assertEqual(len(saved["windows"]), 9)
        states = {w["class"]: (w["floating"], w["pinned"], w["fullscreen"]) for w in saved["windows"]}
        self.assertEqual(states["pip"], (True, True, 0))
        self.assertEqual(states["big"], (False, False, 1))
        self.assertEqual(group_shapes(saved), [(3, ("alpha", "beta", "chromium"))])

        self.comp.shutdown()
        self.comp.boot(tuple(reversed(MONITORS)))
        ids = {m["name"]: m["id"] for m in self.comp.json("monitors")}
        self.assertLess(ids["MON-B"], ids["MON-A"], "ids should swap as across a real reboot")

        # A browser Omarchy killed has recorded a crash, after which Chromium
        # refuses to restore its session; restore clears the mark before the
        # relaunch. The stand-in has no profile, so plant one.
        prefs = os.path.join(self.comp.home, ".config", "chromium", "Default", "Preferences")
        os.makedirs(os.path.dirname(prefs))
        with open(prefs, "w") as f:
            json.dump({"profile": {"exit_type": "Crashed"}}, f)

        restored = self.comp.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + self.comp.log_tail("hyprland"))
        self.assertIn("is the saved", restored.stdout, "each placement should be accounted for")
        after = self.snapshot("save", "state-after")

        with self.subTest("windows on their workspaces, monitors and geometry"):
            self.assertEqual(shape(after), shape(saved), self.comp.requests())
        with self.subTest("groups"):
            self.assertEqual(group_shapes(after), group_shapes(saved))
        with self.subTest("the browser is one process again"):
            pids = {c["pid"] for c in self.comp.clients() if app_name(c["class"]) == "chromium"}
            self.assertEqual(len(pids), 1)
        with self.subTest("the browser's crashed exit was marked clean before its relaunch"):
            with open(prefs) as f:
                self.assertEqual(json.load(f)["profile"]["exit_type"], "Normal")

    def test_the_config_file_is_written_on_first_run_and_an_edit_is_honoured(self):
        """A fresh login has no config file: the first run writes one holding
        every default with a comment on each, and a class added to its exclude
        line is neither saved nor restored from then on. The whole path runs
        as it does on a desktop: the launcher, a real home, the shipped
        template read back by the interpreter the desktop has."""
        c = self.comp
        c.boot(MONITORS)
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        c.open_window("foot --app-id=notes")
        c.open_window("foot --app-id=keep")
        state = os.path.join(c.home, ".local", "state", "omarchy-last-session", "session.json")

        first = c.run_script("save")
        self.assertEqual((first.returncode, first.stderr), (0, ""), first.stdout + first.stderr)
        with open(c.config_file()) as f:
            template = f.read()
        for expected in (
            "[general]\n",
            "\nexclude =\n",
            "\n[terminals]\n",
            "\nfoot = foot, -D\n",
            "\n[chromium-browsers]\n",
        ):
            self.assertIn(expected, template)
        self.assertGreater(template.count("\n# "), 20, "every setting should come with its comment")
        with open(state) as f:
            before = json.load(f)
        self.assertEqual({w["class"] for w in before["windows"]}, {"notes", "keep"})

        with open(c.config_file(), "w") as f:
            f.write(template.replace("\nexclude =\n", "\nexclude = notes\n", 1))
        second = c.run_script("save")
        self.assertEqual((second.returncode, second.stderr), (0, ""), second.stdout + second.stderr)
        with open(state) as f:
            self.assertEqual({w["class"] for w in json.load(f)["windows"]}, {"keep"})

        # An older snapshot still holds the window; restore leaves it out too.
        with open(state, "w") as f:
            json.dump(before, f)
        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore")
        self.assertEqual(
            (restored.returncode, restored.stderr), (0, ""), restored.stdout + c.log_tail("hyprland")
        )
        wait_for(lambda: {w["class"] for w in c.clients()} == {"keep"} or None, "the kept window alone")
        time.sleep(2)
        self.assertEqual({w["class"] for w in c.clients()}, {"keep"})

    def test_two_windows_of_one_browser_keep_their_own_monitors(self):
        """One process, two windows, the same class: only their titles say
        which is which. Swap them and each comes back on the other's monitor."""
        c = self.comp
        c.boot(MONITORS)
        with open(os.path.join(c.home, ".fakebrowser-windows"), "w") as f:
            f.write("2\n")
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        opened = c.open_windows("chromium", count=2)
        stray = next(w for w in opened if c.window(w)["title"].startswith("tab 2"))
        c.dispatch(f"hl.dsp.window.move({{ workspace = 2, follow = false, window = {address(stray)} }})")
        wait_for(lambda: c.window(stray)["workspace"]["id"] == 2, "the second window to reach workspace 2")

        saved = self.snapshot("shutdown", "state")
        before = tabs_by_monitor(saved)
        self.assertEqual(
            len(set(before.values())), 2, f"the two windows should start on different monitors: {before}"
        )

        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + c.log_tail("hyprland"))
        after = self.snapshot("save", "state-after")
        self.assertEqual(tabs_by_monitor(after), before, c.requests())

    def test_an_editor_is_launched_once_for_all_of_its_windows(self):
        """One process serves every window of the editor and it reopens them
        itself, so launching it once per window adds a spare with no project in
        it and leaves the real one unaccounted for."""
        c = self.comp
        c.boot(MONITORS)
        with open(os.path.join(c.home, ".fakeeditor-windows"), "w") as f:
            f.write("2\n")
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        opened = c.open_windows("code", count=2)
        stray = next(w for w in opened if c.window(w)["title"].startswith("project 2"))
        c.dispatch(f"hl.dsp.window.move({{ workspace = 2, follow = false, window = {address(stray)} }})")
        wait_for(lambda: c.window(stray)["workspace"]["id"] == 2, "the second window to reach workspace 2")

        saved = self.snapshot("shutdown", "state")
        with self.subTest("one launch serves both windows"):
            self.assertEqual([w["spawn"] for w in saved["windows"] if w["class"] == "code"], [True, False])
        before = editor_windows(saved)
        self.assertEqual(sorted(before.values()), [1, 2], before)

        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + c.log_tail("hyprland"))
        after = self.snapshot("save", "state-after")
        with self.subTest("both projects back, and nothing spare"):
            self.assertEqual(editor_windows(after), before, c.requests())

    def test_an_office_suite_is_launched_once_for_all_of_its_windows(self):
        """One process serves every window, and a second launch joins it rather
        than opening one, so launching per saved window would only race a
        second instance. It reopens nothing itself, so one window returns and
        the sweep waits its full time before reporting the rest."""
        c = self.comp
        c.boot(MONITORS)
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        doc = c.open_window(f"libreoffice --writer {self.project}/notes.odt")
        centre = c.open_window("libreoffice")
        self.assertEqual(doc["pid"], centre["pid"], "both windows should belong to one process")

        saved = self.snapshot("shutdown", "state")
        office = sorted(saved["windows"], key=lambda w: w["class"])
        self.assertEqual([w["class"] for w in office], ["libreoffice-writer", "soffice"])
        with self.subTest("one launch serves every window of the instance"):
            self.assertEqual([w["spawn"] for w in office], [True, False])

        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore", os.path.join(self.work, "state"))
        with self.subTest("launched once, not once per saved window"):
            self.assertEqual(c.requests().count(office[0]["cmd"]), 1, c.requests())
        with self.subTest("one instance, and it is the only thing running"):
            self.assertEqual(len({w["pid"] for w in c.clients()}), 1, c.clients())
        with self.subTest("the window it cannot reopen is reported"):
            self.assertIn("no window turned up", restored.stderr)

    def test_steam_comes_back_without_the_game_it_was_started_for(self):
        """Steam's window belongs to a helper whose relative path cannot be
        replayed, so restore goes to a .desktop entry, and every game shortcut
        Steam writes is also an entry that runs steam. Omarchy floats Steam by
        rule, so it comes back floating and has to be tiled to rejoin its
        group."""
        c = self.comp
        c.boot(MONITORS)
        self.write_desktop_entry("steam.desktop", "steam %U")
        self.write_desktop_entry("Warhammer 40,000 Boltgun.desktop", "steam steam://rungameid/2005010")
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        opened = c.open_windows("steam steam://rungameid/2005010", count=2)
        game = next(w for w in opened if w["class"].startswith("steam_app_"))
        os.kill(game["pid"], signal.SIGTERM)
        wait_for(
            lambda: all(not w["class"].startswith("steam_app_") for w in c.clients()), "the game to close"
        )
        steam = next(w for w in opened if w["class"] == "steam")
        notes = address(c.open_window("foot --app-id=notes"))
        c.dispatch(f"hl.dsp.window.float({{ action = 'off', window = {address(steam)} }})")
        c.dispatch(f"hl.dsp.group.toggle({{ window = {notes} }})")
        c.lua(f"hl.get_window({notes}).group:add(hl.get_window({address(steam)}))")
        wait_for(lambda: len(c.window(steam)["grouped"]) == 2, "Steam to join the group")

        saved = self.snapshot("shutdown", "state")
        self.assertEqual(
            sorted((w["class"], w["cmd"]) for w in saved["windows"]),
            [("notes", "foot --app-id=notes"), ("steam", "steam")],
        )
        self.assertEqual(group_shapes(saved), [(1, ("notes", "steam"))])

        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + c.log_tail("hyprland"))
        time.sleep(1)  # a game started by mistake maps moments after the client
        self.assertEqual(sorted(w["class"] for w in c.clients()), ["notes", "steam"], c.requests())
        after = self.snapshot("save", "state-after")
        with self.subTest("tiled again, and back in its group"):
            self.assertEqual(shape(after), shape(saved), c.requests())
            self.assertEqual(group_shapes(after), group_shapes(saved), c.requests())

    def test_a_terminal_comes_back_with_its_tabs_and_splits(self):
        """A kitty with remote control on describes its own instance, so the
        snapshot holds every tab, split and working directory of it. One launch
        replays the lot, which is why the second window is not launched again."""
        c = self.comp
        c.boot(MONITORS)
        self.write_kitty_config()
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        pid = c.open_window("kitty")["pid"]
        c.kitten(pid, "launch", "--type=window", "--location=vsplit", "--cwd=/usr")
        c.kitten(pid, "launch", "--type=window", "--location=hsplit", "--cwd=/var")
        c.kitten(pid, "launch", "--type=tab", "--tab-title=logs", "--cwd=/etc")
        before = kitty_shape(c.kitten(pid, "ls"))
        self.assertEqual(len(before[0]), 2, before)

        saved = self.snapshot("shutdown", "state")
        kitty_windows = [w for w in saved["windows"] if w["class"] == "kitty"]
        self.assertEqual(len(kitty_windows), 1, saved["windows"])
        self.assertIn("--session", kitty_windows[0]["cmd"])

        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + c.log_tail("hyprland"))
        live = wait_for(
            lambda: [w for w in c.clients() if w["class"] == "kitty"], "kitty to come back", log=c.requests
        )
        self.assertEqual(len(live), 1, live)
        listing = wait_for(
            lambda: c.kitten(live[0]["pid"], "ls", check=False), "the restored kitty to describe itself"
        )
        self.assertEqual(kitty_shape(listing), before, c.requests())

    def test_a_session_from_two_monitors_comes_back_on_one(self):
        """Undocked between logins. The second monitor's windows have nowhere
        of their own to go, so they must land on the one that is left rather
        than off the side of it, where no bind can reach them."""
        self.comp.boot(MONITORS)
        self.comp.dispatch("hl.dsp.focus({ workspace = 2 })")
        self.comp.open_window("foot --app-id=notes")
        calc = self.comp.open_window("foot --app-id=calc")
        target = address(calc)
        self.comp.dispatch(f"hl.dsp.window.float({{ action = 'on', window = {target} }})")
        self.comp.dispatch(f"hl.dsp.window.resize({{ x = 500, y = 300, window = {target} }})")
        self.comp.dispatch(f"hl.dsp.window.move({{ x = 2400, y = 200, window = {target} }})")
        wait_for(
            lambda: self.comp.window(calc)["at"] == [2400, 200], "the floating window to settle on MON-B"
        )
        saved = self.snapshot("shutdown", "state")
        self.assertEqual({w["monitor_name"] for w in saved["windows"]}, {"MON-B"})

        self.comp.shutdown()
        self.comp.boot((MONITORS[0],))
        restored = self.comp.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + self.comp.log_tail("hyprland"))

        only = self.comp.json("monitors")[0]
        for c in self.comp.clients():
            with self.subTest(window=c["class"]):
                self.assertEqual(c["monitor"], only["id"], self.comp.requests())
                self.assertGreaterEqual(c["at"][0], only["x"], self.comp.requests())
                self.assertLess(c["at"][0], only["x"] + only["width"], self.comp.requests())

    def test_a_renamed_workspace_comes_back_under_its_number(self):
        """A numbered workspace the user renamed keeps its number: the bar
        lists it by that. Restored as name:Home it would be a new named
        workspace with a negative id, which the bar hides."""
        c = self.comp
        c.boot(MONITORS)
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        c.dispatch("hl.dsp.workspace.rename({ workspace = '1', name = 'Home' })")
        c.open_window("foot --app-id=notes")
        saved = self.snapshot("shutdown", "state")
        self.assertEqual([w["workspace"] for w in saved["windows"]], [{"id": 1, "name": "Home"}])

        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + c.log_tail("hyprland"))
        after = self.snapshot("save", "state-after")
        self.assertEqual(
            [w["workspace"] for w in after["windows"]], [{"id": 1, "name": "Home"}], c.requests()
        )
        self.assertEqual([w["id"] for w in c.json("workspaces") if w["name"] == "Home"], [1], c.requests())

    def test_a_workspace_renamed_again_comes_back_under_its_latest_name(self):
        """Home became Home1 during the session, so Home1 is what comes back."""
        c = self.comp
        c.boot(MONITORS)
        c.dispatch("hl.dsp.focus({ workspace = 1 })")
        c.dispatch("hl.dsp.workspace.rename({ workspace = '1', name = 'Home' })")
        c.open_window("foot --app-id=notes")
        c.dispatch("hl.dsp.workspace.rename({ workspace = '1', name = 'Home1' })")
        saved = self.snapshot("shutdown", "state")
        self.assertEqual([w["workspace"] for w in saved["windows"]], [{"id": 1, "name": "Home1"}])

        c.shutdown()
        c.boot(MONITORS)
        restored = c.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + c.log_tail("hyprland"))
        after = self.snapshot("save", "state-after")
        self.assertEqual(
            [w["workspace"] for w in after["windows"]], [{"id": 1, "name": "Home1"}], c.requests()
        )
        named = [(w["id"], w["name"]) for w in c.json("workspaces") if w["name"].startswith("Home")]
        self.assertEqual(named, [(1, "Home1")], c.requests())


if __name__ == "__main__":
    unittest.main()
