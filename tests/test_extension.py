import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from bench_wizard import server

EXT = Path(__file__).resolve().parent.parent / "extension"

ROWS = [
    {"app": "erpnext", "repository_url": "https://github.com/frappe/erpnext", "branch": "version-15",
     "hash": "abc123", "public": 1, "tag": "v15.40.0"},
    {"app": "frappe", "repository_url": "https://github.com/frappe/frappe", "branch": "version-15",
     "hash": "def456", "public": 1, "tag": None},
    {"app": "acme", "repository_url": "https://github.com/acme/acme", "branch": "main",
     "hash": "0f0f0f", "public": 0},
]

SCRIPT = """
const { toExport, siteFromPath } = require(process.argv[1]);
const rows = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
  apps: toExport(rows),
  paths: ['/dashboard/sites/a.frappe.cloud/insights/apps', '/dashboard/sites/new', '/dashboard/groups/x', '']
    .map(siteFromPath),
}));
"""


class ManifestTest(unittest.TestCase):
    def test_manifest_files_exist(self):
        manifest = json.loads((EXT / "manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["host_permissions"], ["https://cloud.frappe.io/*", "https://frappecloud.com/*", "http://127.0.0.1/*"])
        files = [manifest["action"]["default_popup"], manifest["background"]["service_worker"],
                 *manifest["icons"].values()]
        for name in files:
            self.assertTrue((EXT / name).is_file(), name)

    def test_extension_tries_the_ports_the_wizard_prefers(self):
        found = re.search(r"WIZARD_PORTS = \[([\d, ]+)\]", (EXT / "wizard.js").read_text())
        self.assertEqual([int(p) for p in found.group(1).split(",")], list(server.EXTENSION_PORTS))


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ExportTest(unittest.TestCase):
    def run_node(self):
        out = subprocess.run(["node", "-e", SCRIPT, str(EXT / "export.js"), json.dumps(ROWS)],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_to_export(self):
        apps = self.run_node()["apps"]
        self.assertEqual([a["app"] for a in apps], ["frappe", "erpnext", "acme"])
        self.assertEqual(apps[1], {"app": "erpnext", "url": "https://github.com/frappe/erpnext",
                                   "branch": "version-15", "commit": "abc123", "tag": "v15.40.0"})
        self.assertNotIn("tag", apps[0])
        self.assertTrue(apps[2]["private"])
        self.assertNotIn("private", apps[1])

    def test_site_from_path(self):
        self.assertEqual(self.run_node()["paths"], ["a.frappe.cloud", "", "", ""])
