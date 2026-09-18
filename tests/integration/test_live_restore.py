"""Live tests: a real Hyprland with two headless monitors, real windows, and
the script driven through its command line. Nothing is mocked.

Run them in a container:  tests/integration/run.sh
They need OLS_LIVE_TESTS=1 plus Hyprland, labwc and foot on PATH, so the
discovery from the repository root skips them.

Hyprland's backend needs a DRM device, so it runs nested in a headless labwc
on the host's render node. A test builds a session out of foot windows, saves
it, boots a fresh compositor, restores, and compares what came back with what
was saved: once with the monitors in the other order, as their ids swap across
real reboots, and once with the second monitor unplugged.
"""

import glob
import json
import os
import pathlib
import shutil
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

    def run_script(self, action, state_dir):
        env = dict(
            self.env,
            OMARCHY_LAST_SESSION_DIR=state_dir,
            OLS_HYPRCTL_LOG=os.path.join(self.home, "hyprctl.log"),
        )
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

        restored = self.comp.run_script("restore", os.path.join(self.work, "state"))
        self.assertEqual(restored.stderr, "", restored.stdout + "\n" + self.comp.log_tail("hyprland"))
        after = self.snapshot("save", "state-after")

        with self.subTest("windows on their workspaces, monitors and geometry"):
            self.assertEqual(shape(after), shape(saved), self.comp.requests())
        with self.subTest("groups"):
            self.assertEqual(group_shapes(after), group_shapes(saved))
        with self.subTest("the browser is one process again"):
            pids = {c["pid"] for c in self.comp.clients() if app_name(c["class"]) == "chromium"}
            self.assertEqual(len(pids), 1)

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


if __name__ == "__main__":
    unittest.main()
