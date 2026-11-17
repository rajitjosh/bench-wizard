import os
import tempfile
import unittest
from pathlib import Path

from bench_wizard import catalog, existing

CATALOG = catalog.load(Path(__file__).resolve().parent.parent / "catalog.json")
# Answers list-apps for a.localhost; fails for any other site, as bench does for a site whose database is gone.
FAKE_BENCH = """#!/bin/sh
if [ "$2" = a.localhost ]; then echo "some warning"; printf '{\\n "a.localhost": ["frappe", "erpnext"]\\n}\\n'
else echo "pymysql.err.OperationalError: Unknown database" >&2; exit 1; fi
"""


class InspectBenchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bench = self.root / "bench"
        (self.bench / "apps/frappe/frappe").mkdir(parents=True)
        (self.bench / "apps/frappe/frappe/__init__.py").write_text('__version__ = "15.108.0"\n')
        (self.bench / "sites").mkdir()
        (self.bench / "sites/apps.txt").write_text("frappe\nerpnext\ncrm")
        for site in ("a.localhost", "b.localhost"):
            (self.bench / "sites" / site).mkdir()
            (self.bench / "sites" / site / "site_config.json").write_text("{}")
        (self.bench / "sites/assets").mkdir()
        (self.bench / ".nvmrc").write_text("v20.11.0\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        (bin_dir / "bench").write_text(FAKE_BENCH)
        (bin_dir / "bench").chmod(0o755)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")

    def test_reads_version_node_apps_and_sites(self):
        info = existing.inspect(str(self.bench), CATALOG, self.env)
        self.assertEqual((info.version, info.node, info.apps), ("version-15", "20", ["frappe", "erpnext", "crm"]))
        self.assertEqual(info.sites["a.localhost"], {"apps": ["frappe", "erpnext"]})
        self.assertIn("Unknown database", info.sites["b.localhost"]["error"])
        self.assertNotIn("assets", info.sites)

    def test_folder_that_is_not_a_bench(self):
        with self.assertRaises(existing.BenchError):
            existing.inspect(str(self.root), CATALOG, self.env)

    def test_any_frappe_version_is_read_and_described_from_the_bench(self):
        (self.bench / "apps/frappe/frappe/__init__.py").write_text('__version__ = "14.2.0"\n')
        self.assertEqual(existing.inspect(str(self.bench), CATALOG, self.env).version, "version-14")
        (self.bench / "env").mkdir(exist_ok=True)
        (self.bench / "env/pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.10.12.final.0\n")
        profile = existing.own_profile(str(self.bench), "version-14")
        self.assertEqual((profile["title"], profile["python"], profile["existing_only"]), ("Version 14", "3.10", True))


class NvmDefaultTest(unittest.TestCase):
    def test_reads_the_major_or_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(existing.nvm_default_node(home), "")
            (Path(home) / ".nvm/alias").mkdir(parents=True)
            for text, major in (("v24.14.0\n", "24"), ("20", "20"), ("lts/*", "")):
                (Path(home) / ".nvm/alias/default").write_text(text)
                self.assertEqual(existing.nvm_default_node(home), major)
