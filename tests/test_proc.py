import unittest
from unittest import mock

from omarchy_last_session import proc


class FindDescendant(unittest.TestCase):
    def search(self, tree, comms, max_depth=5):
        with (
            mock.patch.object(proc, "list_children", side_effect=lambda pid: tree.get(pid, [])),
            mock.patch.object(proc, "read_comm", side_effect=comms.get),
        ):
            return proc.find_descendant(1, {"nvim"}, max_depth=max_depth)

    def test_finds_a_grandchild(self):
        self.assertEqual(self.search({1: [2], 2: [3], 3: []}, {3: "nvim"}), 3)

    def test_stops_at_max_depth(self):
        self.assertIsNone(self.search({1: [2], 2: [3], 3: [4], 4: []}, {4: "nvim"}, max_depth=2))

    def test_returns_none_when_absent(self):
        self.assertIsNone(self.search({}, {}))
