"""Sandboxed windows come back through their sandbox, from records the host
keeps for them: Flatpak's instance directories, and the cgroup scope `snap run`
puts a snap app in. Each instance directory here has the shape Flatpak 1.18.4
wrote on 2026-10-20."""

import os
import tempfile
import unittest
from unittest import mock

from omarchy_last_session import proc, sandbox
from tests.helpers import FLATPAK_CHILD_PID, write_flatpak_instance

TEXT_EDITOR_NS = 4026533500


class FlatpakCase(unittest.TestCase):
    """A runtime directory of the test's own, and a window process in a fixed mount namespace."""

    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.addCleanup(self.runtime.cleanup)
        for patcher in (
            mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.runtime.name}),
            mock.patch.object(proc, "read_namespace_inode", return_value=TEXT_EDITOR_NS),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_instance(self, **kwargs):
        return write_flatpak_instance(self.runtime.name, **kwargs)


class FlatpakApp(FlatpakCase):
    def test_the_instance_sharing_the_windows_mount_namespace_names_the_app(self):
        self.write_instance()
        self.assertEqual(
            sandbox.find_flatpak_app(2960649), sandbox.FlatpakApp("org.gnome.TextEditor", "stable", "x86_64")
        )

    def test_the_app_is_relaunched_through_flatpak_on_its_branch_and_arch(self):
        self.write_instance()
        self.assertEqual(
            sandbox.build_sandbox_command(2960649),
            "flatpak run --branch=stable --arch=x86_64 org.gnome.TextEditor",
        )

    def test_an_instance_in_another_namespace_is_not_the_windows(self):
        self.write_instance(mnt_namespace=4026539999)
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_the_matching_instance_is_found_among_others(self):
        other = "[Application]\nname=org.gnome.Papers\n\n[Instance]\nbranch=stable\narch=x86_64\n"
        self.write_instance(name="1111", mnt_namespace=4026531111, info=other)
        self.write_instance(name="2222")
        self.assertEqual(sandbox.find_flatpak_app(2960649).app_id, "org.gnome.TextEditor")

    def test_directories_not_named_by_digits_are_not_instances(self):
        """Flatpak keeps <n>-private, a per-app directory and wl beside the instances."""
        self.write_instance(name="3189576737-private")
        self.write_instance(name="org.gnome.TextEditor")
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_without_XDG_RUNTIME_DIR_the_users_run_directory_is_read(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertEqual(sandbox.get_flatpak_instances_dir(), f"/run/user/{os.getuid()}/.flatpak")

    def test_a_relative_XDG_RUNTIME_DIR_is_ignored(self):
        """The XDG spec says to: it would resolve against whatever directory save ran in."""
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "run"}):
            self.assertEqual(sandbox.get_flatpak_instances_dir(), f"/run/user/{os.getuid()}/.flatpak")

    def test_a_directory_named_by_non_ascii_digits_is_not_an_instance(self):
        """str.isdigit() is true for "²" and "٣"."""
        self.write_instance(name="٣²")
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_a_process_whose_namespace_cannot_be_read_has_no_app(self):
        self.write_instance()
        with mock.patch.object(proc, "read_namespace_inode", return_value=None):
            self.assertIsNone(sandbox.build_sandbox_command(2960649))

    def test_no_instances_directory_means_no_app(self):
        self.assertIsNone(sandbox.find_flatpak_app(2960649))


class UntrustedFlatpakRecords(FlatpakCase):
    """Whatever does not parse, or names an app id, branch or arch no Flatpak
    would accept, is refused rather than written into a shell command."""

    def info_with(self, name="org.gnome.TextEditor", branch="stable", arch="x86_64"):
        return f"[Application]\nname={name}\n\n[Instance]\nbranch={branch}\narch={arch}\n"

    def test_an_app_id_that_is_not_a_flatpak_id_is_refused(self):
        for n, name in enumerate(
            ("TextEditor", "org.gnome.TextEditor; rm -rf ~", "-org.gnome.x", "org..gnome")
        ):
            with self.subTest(name=name):
                instance = self.write_instance(name=str(n), info=self.info_with(name=name))
                self.assertIsNone(sandbox.read_flatpak_info(instance))

    def test_a_branch_or_arch_with_shell_text_is_refused(self):
        cases = (("stable --command=sh", "x86_64"), ("stable", "x86_64$(id)"), ("", "x86_64"))
        for n, (branch, arch) in enumerate(cases):
            with self.subTest(branch=branch, arch=arch):
                instance = self.write_instance(name=str(n), info=self.info_with(branch=branch, arch=arch))
                self.assertIsNone(sandbox.read_flatpak_info(instance))

    def test_an_info_file_missing_a_key_is_refused(self):
        self.write_instance(info="[Application]\nname=org.gnome.TextEditor\n")
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_an_info_file_that_does_not_parse_is_refused(self):
        self.write_instance(info="name=org.gnome.TextEditor\n")
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_a_percent_sign_is_read_as_text(self):
        """configparser would expand %(...)s unless interpolation is off."""
        self.write_instance(info=self.info_with(name="org.gnome.TextEditor") + "runtime=%(x)s\n")
        self.assertEqual(sandbox.find_flatpak_app(2960649).app_id, "org.gnome.TextEditor")

    def test_bwrapinfo_without_an_integer_namespace_is_refused(self):
        instance = self.write_instance()
        for text in ('{"mnt-namespace": "4026533500"}', "[4026533500]", "not json"):
            with self.subTest(text=text):
                with open(os.path.join(instance, "bwrapinfo.json"), "w") as f:
                    f.write(text)
                self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_a_json_true_is_not_the_namespace_inode_1(self):
        """bool is a subclass of int, so isinstance() alone would take it."""
        self.write_instance(mnt_namespace=True)
        with mock.patch.object(proc, "read_namespace_inode", return_value=1):
            self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_a_symlink_in_an_instances_place_is_not_followed(self):
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        real = write_flatpak_instance(elsewhere.name)
        os.makedirs(os.path.join(self.runtime.name, ".flatpak"))
        os.symlink(real, os.path.join(self.runtime.name, ".flatpak", "42"))
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_a_symlinked_info_file_is_refused(self):
        instance = self.write_instance()
        elsewhere = os.path.join(self.runtime.name, "planted-info")
        os.rename(os.path.join(instance, "info"), elsewhere)
        os.symlink(elsewhere, os.path.join(instance, "info"))
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_a_fifo_in_the_info_files_place_is_refused_without_waiting_for_a_writer(self):
        instance = self.write_instance()
        os.unlink(os.path.join(instance, "info"))
        os.mkfifo(os.path.join(instance, "info"))
        self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_a_fifo_in_bwrapinfos_place_is_refused(self):
        instance = self.write_instance()
        os.unlink(os.path.join(instance, "bwrapinfo.json"))
        os.mkfifo(os.path.join(instance, "bwrapinfo.json"))
        self.assertIsNone(sandbox.find_flatpak_app(2960649))


class StaleFlatpakInstances(FlatpakCase):
    """The kernel reuses a namespace's inode once it is gone, and Flatpak removes
    a dead instance's directory only later, so a dead instance can still match."""

    def namespaces(self, by_pid):
        return mock.patch.object(proc, "read_namespace_inode", side_effect=lambda pid, kind: by_pid.get(pid))

    def test_an_instance_whose_child_is_gone_is_refused(self):
        self.write_instance()
        with self.namespaces({2960649: TEXT_EDITOR_NS}):
            self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_an_instance_whose_child_is_in_another_namespace_is_refused(self):
        self.write_instance()
        with self.namespaces({2960649: TEXT_EDITOR_NS, FLATPAK_CHILD_PID: 4026539999}):
            self.assertIsNone(sandbox.find_flatpak_app(2960649))

    def test_an_instance_whose_child_is_in_the_namespace_is_the_windows(self):
        self.write_instance()
        with self.namespaces({2960649: TEXT_EDITOR_NS, FLATPAK_CHILD_PID: TEXT_EDITOR_NS}):
            self.assertEqual(sandbox.find_flatpak_app(2960649).app_id, "org.gnome.TextEditor")

    def test_a_child_pid_that_is_not_an_integer_is_refused(self):
        for n, child_pid in enumerate((None, True, "2960648", 2960648.0)):
            with self.subTest(child_pid=child_pid):
                instance = self.write_instance(name=str(n), child_pid=child_pid)
                self.assertFalse(sandbox.is_live_instance_in(instance, TEXT_EDITOR_NS))


class SnapApp(unittest.TestCase):
    """A snap app runs in the scope `snap run` made for it, which names the
    snap and the app. The line below is gedit's, read on 2026-10-20 (snapd 2.78)."""

    USER_SLICE = "/user.slice/user-1000.slice/user@1000.service/app.slice/"
    GEDIT = USER_SLICE + "snap.gedit.gedit-6c8b1fc2-8212-45af-8b8b-2f39f30e2ee5.scope"

    def setUp(self):
        # No Flatpak instance can match, whatever runs on the machine.
        patcher = mock.patch.object(proc, "read_namespace_inode", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command_for(self, cgroup):
        with mock.patch.object(proc, "read_cgroup", return_value=cgroup):
            return sandbox.build_sandbox_command(7)

    def test_an_app_named_after_its_snap_is_run_by_the_snaps_name(self):
        self.assertEqual(self.command_for(self.GEDIT), "snap run gedit")

    def test_another_app_of_a_snap_is_run_as_snap_dot_app(self):
        cgroup = self.USER_SLICE + "snap.libreoffice.writer-0b5e2f1a-1c2d-4e3f-8a9b-0c1d2e3f4a5b.scope"
        self.assertEqual(self.command_for(cgroup), "snap run libreoffice.writer")

    def test_a_hooks_scope_is_not_an_app(self):
        cgroup = self.USER_SLICE + "snap.gedit.hook.configure-6c8b1fc2-8212-45af-8b8b-2f39f30e2ee5.scope"
        self.assertIsNone(self.command_for(cgroup))

    def test_a_scope_that_is_not_a_snaps_is_not_an_app(self):
        self.assertIsNone(self.command_for(self.USER_SLICE + "app-com.microsoft.VSCode-6863.scope"))

    def test_a_process_whose_cgroup_cannot_be_read_has_no_snap(self):
        self.assertIsNone(self.command_for(None))

    def test_an_app_name_ending_in_hex_keeps_it(self):
        cgroup = self.USER_SLICE + "snap.tool.tool-cafe-6c8b1fc2-8212-45af-8b8b-2f39f30e2ee5.scope"
        self.assertEqual(self.command_for(cgroup), "snap run tool.tool-cafe")

    def test_names_no_snap_could_have_are_refused(self):
        uuid = "6c8b1fc2-8212-45af-8b8b-2f39f30e2ee5"
        for scope in (
            f"snap.Gedit.gedit-{uuid}.scope",
            f"snap.-gedit.gedit-{uuid}.scope",
            f"snap.ged--it.gedit-{uuid}.scope",
            f"snap.gedit.gedit;sh-{uuid}.scope",
            "snap.gedit.gedit-6c8b.scope",
        ):
            with self.subTest(scope=scope):
                self.assertIsNone(self.command_for(self.USER_SLICE + scope))


if __name__ == "__main__":
    unittest.main()
