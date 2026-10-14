import copy
import json
import tempfile
import unittest
from pathlib import Path

from bench_wizard import catalog

ROOT = Path(__file__).resolve().parent.parent


def good():
    return {
        "frappe_versions": {
            "version-15": {"title": "Version 15", "python": "3.11", "node": "18", "default": True},
            "version-16": {"title": "Version 16", "python": "3.14", "node": "24"},
        },
        "apps": {
            "frappe": {"title": "Frappe", "description": "d", "repo": "r",
                       "branches": {"version-15": "version-15"}, "requires": [], "private": False},
            "erpnext": {"title": "ERPNext", "description": "d", "repo": "r",
                        "branches": {"version-15": "version-15"}, "requires": ["frappe"], "private": False},
        },
        "presets": {
            "plain": {"title": "Plain", "description": "d", "apps": ["erpnext"], "default": True},
            "empty": {"title": "Empty", "description": "d", "apps": []},
        },
    }


class CatalogTest(unittest.TestCase):
    def assertRejected(self, data, text):
        with self.assertRaises(catalog.CatalogError) as ctx:
            catalog.validate(data)
        self.assertIn(text, str(ctx.exception))

    def test_shipped_catalog_loads(self):
        data = catalog.load(ROOT / "catalog.json")
        self.assertEqual(catalog.default_version(data), "version-15")
        self.assertEqual(catalog.default_preset(data), "plain-erpnext")
        self.assertEqual(sorted(data["apps"]), ["erpnext", "frappe", "india_compliance", "lending"])
        self.assertEqual(sorted(data["presets"]), ["empty-frappe", "plain-erpnext"])

    def test_shipped_catalog_names_no_private_project(self):
        text = (ROOT / "catalog.json").read_text().lower()
        self.assertNotIn("platform", text)
        self.assertNotIn("promantia", text)

    def test_good_catalog_passes(self):
        catalog.validate(good())

    def test_unreadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text("{not json")
            with self.assertRaises(catalog.CatalogError):
                catalog.load(path)

    def test_missing_app_field(self):
        data = good()
        del data["apps"]["erpnext"]["repo"]
        self.assertRejected(data, "erpnext")

    def test_unknown_requires(self):
        data = good()
        data["apps"]["erpnext"]["requires"] = ["hrms"]
        self.assertRejected(data, "hrms")

    def test_cycle(self):
        data = good()
        data["apps"]["frappe"]["requires"] = ["erpnext"]
        self.assertRejected(data, "cycle")

    def test_preset_with_unknown_app(self):
        data = good()
        data["presets"]["plain"]["apps"] = ["crm"]
        self.assertRejected(data, "crm")

    def test_unknown_branch_key(self):
        data = good()
        data["apps"]["erpnext"]["branches"]["version-99"] = "x"
        self.assertRejected(data, "version-99")

    def test_no_default_version(self):
        data = good()
        del data["frappe_versions"]["version-15"]["default"]
        self.assertRejected(data, "default")

    def test_two_default_versions(self):
        data = good()
        data["frappe_versions"]["version-16"]["default"] = True
        self.assertRejected(data, "default")

    def test_no_default_preset(self):
        data = good()
        del data["presets"]["plain"]["default"]
        self.assertRejected(data, "default")

    def test_two_default_presets(self):
        data = good()
        data["presets"]["empty"]["default"] = True
        self.assertRejected(data, "default")

    def test_frappe_app_required(self):
        data = good()
        del data["apps"]["frappe"]
        data["apps"]["erpnext"]["requires"] = []
        self.assertRejected(data, "frappe")


LOCAL = {
    "apps": {
        "my_app": {"title": "My App", "description": "d", "repo": "git@example.com:me/my_app.git",
                   "branches": {"version-15": "main"}, "requires": ["frappe", "erpnext"], "private": True},
    },
    "presets": {
        "my-project": {"title": "My Project", "description": "d", "apps": ["erpnext", "my_app"]},
    },
}


class LocalCatalogTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.main = self.dir / "catalog.json"
        self.main.write_text(json.dumps(good()))
        self.local = self.dir / "local.json"

    def test_missing_local_file_is_fine(self):
        data = catalog.load(self.main, self.local)
        self.assertEqual(sorted(data["apps"]), ["erpnext", "frappe"])

    def test_local_apps_and_presets_are_added_and_marked(self):
        self.local.write_text(json.dumps(LOCAL))
        data = catalog.load(self.main, self.local)
        self.assertIn("my_app", data["apps"])
        self.assertTrue(data["apps"]["my_app"]["local"])
        self.assertTrue(data["presets"]["my-project"]["local"])
        self.assertFalse(data["apps"]["erpnext"].get("local", False))
        self.assertEqual(catalog.default_preset(data), "plain")

    def test_local_entry_overrides_shipped_one(self):
        local = copy.deepcopy(LOCAL)
        local["apps"]["erpnext"] = dict(good()["apps"]["erpnext"], repo="git@example.com:fork/erpnext.git")
        self.local.write_text(json.dumps(local))
        data = catalog.load(self.main, self.local)
        self.assertEqual(data["apps"]["erpnext"]["repo"], "git@example.com:fork/erpnext.git")

    def test_local_default_flags_are_ignored(self):
        local = copy.deepcopy(LOCAL)
        local["presets"]["my-project"]["default"] = True
        self.local.write_text(json.dumps(local))
        data = catalog.load(self.main, self.local)
        self.assertEqual(catalog.default_preset(data), "plain")

    def test_invalid_local_file_names_the_file(self):
        local = copy.deepcopy(LOCAL)
        local["presets"]["my-project"]["apps"] = ["nope"]
        self.local.write_text(json.dumps(local))
        with self.assertRaises(catalog.CatalogError) as ctx:
            catalog.load(self.main, self.local)
        self.assertIn("nope", str(ctx.exception))
        self.assertIn("local.json", str(ctx.exception))

    def test_broken_local_json(self):
        self.local.write_text("{")
        with self.assertRaises(catalog.CatalogError) as ctx:
            catalog.load(self.main, self.local)
        self.assertIn("local.json", str(ctx.exception))
