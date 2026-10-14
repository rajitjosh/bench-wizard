import unittest

from bench_wizard.graph import CycleError, toposort


class ToposortTest(unittest.TestCase):
    def test_requirement_comes_first(self):
        order = toposort(["lending", "erpnext"], {"lending": {"erpnext"}})
        self.assertEqual(order, ["erpnext", "lending"])

    def test_ties_keep_input_order(self):
        order = toposort(["b", "a", "c"], {})
        self.assertEqual(order, ["b", "a", "c"])

    def test_chain(self):
        order = toposort(["a", "b", "c"], {"a": {"b"}, "b": {"c"}})
        self.assertEqual(order, ["c", "b", "a"])

    def test_unknown_requirement_is_ignored(self):
        self.assertEqual(toposort(["a"], {"a": {"frappe"}}), ["a"])

    def test_cycle_names_the_apps(self):
        with self.assertRaises(CycleError) as ctx:
            toposort(["x", "a", "b"], {"a": {"b"}, "b": {"a"}})
        self.assertEqual(sorted(ctx.exception.names), ["a", "b"])
