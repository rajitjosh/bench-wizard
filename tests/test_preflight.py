import os
import tempfile
import unittest
from pathlib import Path

from bench_wizard import preflight
from bench_wizard.backup import BackupInfo
from bench_wizard.deps import Resolution

CATALOG = {
    "frappe_versions": {"version-15": {"title": "Version 15", "python": "3.11", "node": "18"}},
    "apps": {"frappe": {"title": "Frappe", "repo": "r/frappe", "branches": {"version-15": "version-15"},
                        "private": False}},
}
GB = 1024 ** 3


class FakeProbe(preflight.Probe):
    def __init__(self, **over):
        self.free = over.get("free", 50 * GB)
        self.databases = over.get("databases", set())
        self.root_ok = over.get("root_ok", True)
        self.remote = over.get("remote", {})
        self.tools = over.get("tools", True)
        self.node = over.get("node", True)

    def free_bytes(self, path):
        return self.free

    def root_password_ok(self, password):
        return self.root_ok

    def database_exists(self, name, password):
        return name in self.databases

    def ls_remote(self, repo, branch):
        return self.remote.get(repo, "ok")

    def tool_version(self, argv, env):
        return "1.2.3" if self.tools else ""

    def node_installed(self, node):
        return self.node


class LsRemoteTest(unittest.TestCase):
    def test_branch_tag_and_missing_ref(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            run = lambda *a: subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True)
            run("init", "-q", "-b", "version-15")
            Path(tmp, "f.txt").write_text("x")
            run("add", ".")
            run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i")
            run("tag", "v15.1.0")
            probe = preflight.Probe()
            self.assertEqual(probe.ls_remote(tmp, "version-15"), "ok")
            self.assertEqual(probe.ls_remote(tmp, "v15.1.0"), "ok")
            self.assertEqual(probe.ls_remote(tmp, "nope"), "no_branch")
            self.assertEqual(probe.ls_remote(tmp + "/missing", "x"), "denied")


class PreflightTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def answers(self, **over):
        base = {"version": "version-15", "folder": str(self.dir / "bench"), "site": "s.localhost",
                "db_name": "", "mariadb_root_password": "pw", "restore": None}
        base.update(over)
        return base

    def resolution(self, **over):
        apps = {"erpnext": {"repo": "r/erpnext", "branch": "version-15", "title": "ERPNext",
                            "added_for": None, "private": False}}
        return Resolution(order=["erpnext"], apps=apps, **over)

    def run_checks(self, answers=None, resolution=None, backup=None, **probe):
        return preflight.run(answers or self.answers(), resolution or self.resolution(), backup,
                             CATALOG, {}, FakeProbe(**probe))

    def failed(self, rows):
        return [r["label"] for r in rows if not r["ok"]]

    def test_all_pass(self):
        rows = self.run_checks()
        self.assertEqual(self.failed(rows), [])
        self.assertGreaterEqual(len(rows), 5)

    def test_existing_bench_skips_the_new_bench_checks(self):
        (self.dir / "bench" / "apps").mkdir(parents=True)
        rows = self.run_checks(self.answers(existing=True), root_ok=False)
        self.assertEqual(self.failed(rows), [])
        labels = [r["label"] for r in rows]
        self.assertIn(f"Bench folder {self.dir / 'bench'} has site s.localhost", labels)
        self.assertFalse(any("for Frappe" in l or "MariaDB" in l for l in labels))

    def test_new_site_from_backup_in_existing_bench(self):
        (self.dir / "bench" / "apps").mkdir(parents=True)
        backup = BackupInfo(apps={"frappe": "15.1.0", "crm": "1.0.0"}, frappe_major=15)
        answers = self.answers(existing=True, apps=[{"name": "crm", "on_bench": True}],
                               restore={"sql": str(self.dir / "missing.sql")})
        failed = self.failed(self.run_checks(answers, backup=backup, root_ok=False))
        # a new site needs the root password; crm is on the bench, so it is not reported as dropped
        self.assertIn("MariaDB root password was not accepted", failed)
        labels = [r["label"] for r in self.run_checks(answers, backup=backup)]
        self.assertFalse(any("contains crm" in l for l in labels))

    def test_existing_bench_that_is_not_writable_fails(self):
        rows = self.run_checks(self.answers(existing=True))  # no apps/ folder at all
        self.assertTrue(any("Cannot write to" in l for l in self.failed(rows)))

    def test_existing_folder_fails(self):
        (self.dir / "bench").mkdir()
        self.assertTrue(any("already exists" in l for l in self.failed(self.run_checks())))

    def test_unwritable_parent_fails(self):
        locked = self.dir / "locked"
        locked.mkdir()
        os.chmod(locked, 0o500)
        self.addCleanup(os.chmod, locked, 0o700)
        rows = self.run_checks(self.answers(folder=str(locked / "sub" / "bench")))
        self.assertTrue(any("write" in l for l in self.failed(rows)))

    def test_parent_that_does_not_exist_yet_is_fine(self):
        rows = self.run_checks(self.answers(folder=str(self.dir / "new" / "bench")))
        self.assertEqual(self.failed(rows), [])

    def test_wrong_root_password_fails(self):
        self.assertTrue(any("root password" in l for l in self.failed(self.run_checks(root_ok=False))))

    def test_existing_database_fails(self):
        rows = self.run_checks(self.answers(db_name="taken"), databases={"taken"})
        self.assertTrue(any("taken" in l for l in self.failed(rows)))

    def test_low_disk_fails(self):
        self.assertTrue(any("free" in l for l in self.failed(self.run_checks(free=1 * GB))))

    def test_repo_without_access_fails_with_the_fix(self):
        rows = self.run_checks(remote={"r/erpnext": "denied"})
        label = self.failed(rows)[0]
        self.assertIn("r/erpnext", label)
        self.assertIn("SSH", label)

    def test_missing_branch_fails(self):
        rows = self.run_checks(remote={"r/frappe": "no_branch"})
        self.assertIn("version-15", self.failed(rows)[0])

    def test_resolution_errors_become_failing_rows(self):
        rows = self.run_checks(resolution=self.resolution(errors=["Lending needs ERPNext"]))
        self.assertIn("Lending needs ERPNext", self.failed(rows))

    def test_missing_node_names_the_resolved_version(self):
        rows = self.run_checks(resolution=self.resolution(node="22"), node=False)
        self.assertEqual(self.failed(rows), ["Node 22 is not installed with nvm. Fix: nvm install 22"])

    def test_missing_tools_fail(self):
        self.assertEqual(len(self.failed(self.run_checks(tools=False))), 2)

    def restore(self, **over):
        sql = self.dir / "db.sql.gz"
        sql.write_bytes(b"x" * 1000)
        base = {"sql": str(sql), "public": "", "private": ""}
        base.update(over)
        return base

    def test_restore_passes_when_backup_apps_are_selected(self):
        info = BackupInfo(apps={"frappe": "15.1.0", "erpnext": "15.2.0"}, frappe_major=15)
        rows = self.run_checks(self.answers(restore=self.restore()), backup=info)
        self.assertEqual(self.failed(rows), [])

    def test_restore_with_app_missing_from_selection_passes_and_says_it_will_be_removed(self):
        info = BackupInfo(apps={"frappe": "15.1.0", "hrms": "15.0.0"}, frappe_major=15)
        rows = self.run_checks(self.answers(restore=self.restore()), backup=info)
        self.assertEqual(self.failed(rows), [])
        self.assertTrue(any("hrms" in r["label"] and "removed" in r["label"] for r in rows))

    def test_restore_from_newer_frappe_fails(self):
        info = BackupInfo(apps={"frappe": "16.0.0"}, frappe_major=16)
        rows = self.run_checks(self.answers(restore=self.restore()), backup=info)
        self.assertTrue(any("downgrade" in l for l in self.failed(rows)))

    def test_restore_from_older_frappe_is_reported_as_upgrade(self):
        info = BackupInfo(apps={"frappe": "14.0.0"}, frappe_major=14)
        rows = self.run_checks(self.answers(restore=self.restore()), backup=info)
        self.assertEqual(self.failed(rows), [])
        self.assertTrue(any("upgraded from 14 to 15" in r["label"] for r in rows))

    def test_restore_with_unreadable_app_list_still_passes_with_a_notice(self):
        rows = self.run_checks(self.answers(restore=self.restore()), backup=BackupInfo())
        self.assertEqual(self.failed(rows), [])
        self.assertTrue(any("app checks skipped" in r["label"] for r in rows))

    def test_restore_with_missing_files_fails(self):
        r = self.restore(sql=str(self.dir / "nope.sql"), public=str(self.dir / "nope.tar"))
        rows = self.run_checks(self.answers(restore=r), backup=BackupInfo())
        self.assertEqual(len([l for l in self.failed(rows) if "nope" in l]), 2)

    def test_restore_counts_backup_size_in_disk_need(self):
        info = BackupInfo(apps={"frappe": "15.0.0"}, frappe_major=15)
        r = self.restore()
        Path(r["sql"]).write_bytes(b"x" * 2_000_000)
        rows = self.run_checks(self.answers(restore=r), backup=info, free=3 * GB + 1_000_000)
        self.assertTrue(any("free" in l for l in self.failed(rows)))
