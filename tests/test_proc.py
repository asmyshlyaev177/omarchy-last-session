import os
import tempfile
import unittest
from unittest import mock

from omarchy_last_session import proc
from tests.helpers import pretend_host_process


class FakeProc(unittest.TestCase):
    """A /proc of the test's own, so a reader parses a real file."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        patcher = mock.patch.object(proc, "PROC_ROOT", self.root.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, pid, name, text):
        os.makedirs(os.path.join(self.root.name, str(pid)), exist_ok=True)
        with open(os.path.join(self.root.name, str(pid), name), "w") as f:
            f.write(text)


class FindDescendant(unittest.TestCase):
    def search(self, tree, comms, max_depth=5, names=("nvim",)):
        with (
            mock.patch.object(proc, "list_children", side_effect=lambda pid: tree.get(pid, [])),
            mock.patch.object(proc, "read_comm", side_effect=comms.get),
        ):
            return proc.find_descendant(1, set(names), max_depth=max_depth)

    def test_finds_a_grandchild(self):
        pretend_host_process(self)
        self.assertEqual(self.search({1: [2], 2: [3], 3: []}, {3: "nvim"}), 3)

    def test_stops_at_max_depth(self):
        pretend_host_process(self)
        self.assertIsNone(self.search({1: [2], 2: [3], 3: [4], 4: []}, {4: "nvim"}, max_depth=2))

    def test_returns_none_when_absent(self):
        pretend_host_process(self)
        self.assertIsNone(self.search({}, {}))

    def test_a_sandboxed_process_named_like_a_shell_is_skipped_for_the_host_one(self):
        """It names itself, and the restored terminal would open in its directory."""
        tree = {1: [2, 3], 2: [], 3: []}
        with mock.patch.object(proc, "is_host_process", side_effect=lambda pid: pid != 2):
            self.assertEqual(self.search(tree, {2: "bash", 3: "bash"}, names=("bash",)), 3)

    def test_nothing_under_a_sandboxed_process_is_searched(self):
        """What a sandboxed process starts is in its sandbox too, whatever it reports."""
        tree = {1: [2], 2: [3], 3: []}
        with mock.patch.object(proc, "is_host_process", side_effect=lambda pid: pid != 2):
            self.assertIsNone(self.search(tree, {3: "nvim"}))


class IsHostProcess(unittest.TestCase):
    """Only a process of this user in this session's mount and user namespaces
    may have its command line replayed at login."""

    OWN = {"mnt": 4026531841, "user": 4026531837}

    def check(self, uids, namespaces=None):
        theirs = dict(self.OWN, **(namespaces or {}))
        with (
            mock.patch.object(proc, "read_uids", return_value=uids),
            mock.patch.object(
                proc,
                "read_namespace_inode",
                side_effect=lambda pid, kind: (self.OWN if pid == os.getpid() else theirs)[kind],
            ),
        ):
            return proc.is_host_process(42)

    def test_a_process_of_this_user_in_this_users_namespaces_is_a_host_process(self):
        uid = os.getuid()
        self.assertTrue(self.check([uid, uid, uid, uid]))

    def test_another_real_uid_is_not(self):
        uid = os.getuid()
        self.assertFalse(self.check([uid + 1, uid, uid, uid]))

    def test_another_effective_uid_is_not(self):
        uid = os.getuid()
        self.assertFalse(self.check([uid, uid + 1, uid, uid]))

    def test_fewer_than_two_uids_is_not(self):
        self.assertFalse(self.check([os.getuid()]))

    def test_another_mount_namespace_is_not(self):
        uid = os.getuid()
        self.assertFalse(self.check([uid, uid, uid, uid], {"mnt": 4026533500}))

    def test_another_user_namespace_is_not(self):
        uid = os.getuid()
        self.assertFalse(self.check([uid, uid, uid, uid], {"user": 4026533499}))

    def test_a_namespace_that_cannot_be_read_is_not(self):
        uid = os.getuid()
        self.assertFalse(self.check([uid, uid, uid, uid], {"mnt": None}))

    def test_a_process_whose_status_cannot_be_read_is_not(self):
        self.assertFalse(self.check(None))

    def test_this_process_is_a_host_process(self):
        self.assertTrue(proc.is_host_process(os.getpid()))

    def test_a_process_that_is_gone_is_not(self):
        self.assertFalse(proc.is_host_process(2**22 + 1))


class ReadUids(FakeProc):
    def test_uids_are_read_from_status(self):
        self.write(42, "status", "Name:\tbash\nUid:\t1000\t1001\t1002\t1003\nGid:\t1000\t1000\t1000\t1000\n")
        self.assertEqual(proc.read_uids(42), [1000, 1001, 1002, 1003])

    def test_a_status_without_a_uid_line_has_none(self):
        self.write(42, "status", "Name:\tbash\nGid:\t1000\t1000\t1000\t1000\n")
        self.assertIsNone(proc.read_uids(42))

    def test_a_uid_line_that_is_not_numbers_has_none(self):
        self.write(42, "status", "Uid:\t1000\tx\t1000\t1000\n")
        self.assertIsNone(proc.read_uids(42))

    def test_this_processs_real_and_effective_uid(self):
        with mock.patch.object(proc, "PROC_ROOT", "/proc"):
            self.assertEqual(proc.read_uids(os.getpid())[:2], [os.getuid(), os.geteuid()])


class ReadNamespaceInode(unittest.TestCase):
    def test_the_inode_is_the_one_the_kernel_names_the_namespace_by(self):
        name = os.readlink(f"/proc/{os.getpid()}/ns/mnt")
        self.assertEqual(f"mnt:[{proc.read_namespace_inode(os.getpid(), 'mnt')}]", name)

    def test_a_process_that_is_gone_has_none(self):
        self.assertIsNone(proc.read_namespace_inode(2**22 + 1, "mnt"))


class ReadCgroup(FakeProc):
    V2 = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-kitty-1.scope\n"

    def test_the_v2_line_is_read_wherever_it_sits(self):
        """A hybrid setup lists the v1 hierarchies before it."""
        self.write(42, "cgroup", "12:pids:/user.slice\n1:name=systemd:/user.slice\n" + self.V2)
        self.assertEqual(proc.read_cgroup(42), self.V2[len("0::") :].strip())

    def test_a_v1_only_process_has_no_v2_path(self):
        self.write(42, "cgroup", "12:pids:/user.slice\n1:name=systemd:/user.slice\n")
        self.assertIsNone(proc.read_cgroup(42))

    def test_a_process_that_is_gone_has_no_cgroup(self):
        self.assertIsNone(proc.read_cgroup(42))

    def test_this_process_has_a_cgroup_v2_path(self):
        with mock.patch.object(proc, "PROC_ROOT", "/proc"):
            self.assertIsNotNone(proc.read_cgroup(os.getpid()))
