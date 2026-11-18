import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path

from bench_wizard import frappecloud, planner, sites
from tests.test_projects import make_bench


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))


class SitesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bench = make_bench(self.root / "bench", sites=("a.localhost", "b.localhost"))

    def test_apps_report_branch_changes_and_commits_behind_after_a_fetch(self):
        remote = self.root / "remote"
        git(self.root, "init", "-q", "-b", "version-15", str(remote))
        git(remote, "commit", "-q", "--allow-empty", "-m", "one")
        app = Path(self.bench) / "apps" / "erpnext"
        git(self.root, "clone", "-q", "--origin", "upstream", str(remote), str(app))
        for n in ("two", "three"):
            git(remote, "commit", "-q", "--allow-empty", "-m", n)
        (Path(self.bench) / "apps" / "frappe" / ".git").mkdir()  # broken: reported, not fatal
        before = {a["name"]: a for a in sites.apps(self.bench)}
        self.assertEqual(list(before), ["frappe", "erpnext"])
        self.assertTrue(before["frappe"]["error"])
        e = before["erpnext"]
        self.assertEqual((e["branch"], e["upstream"], e["behind"], e["changed"]), ("version-15", "upstream/version-15", 0, False))
        after = {a["name"]: a for a in sites.apps(self.bench, fetch=True)}
        self.assertEqual(after["erpnext"]["behind"], 2)

    def test_switch_steps_move_a_shallow_clone_and_keep_local_work(self):
        remote = self.root / "remote"
        git(self.root, "init", "-q", "-b", "version-15", str(remote))
        (remote / "f.txt").write_text("15\n")
        git(remote, "add", "f.txt")
        git(remote, "commit", "-q", "-m", "one")
        git(remote, "checkout", "-q", "-b", "version-16")
        (remote / "f.txt").write_text("16\n")
        git(remote, "commit", "-q", "-am", "two")
        git(remote, "checkout", "-q", "version-15")
        app = Path(self.bench) / "apps" / "erpnext"
        # how bench get-app clones: one branch, one commit deep, remote named upstream
        git(self.root, "clone", "-q", "--depth", "1", "--branch", "version-15", "--origin", "upstream", f"file://{remote}", str(app))
        (app / "f.txt").write_text("my work\n")
        run = lambda steps: [subprocess.run(s.argv, cwd=s.cwd, check=True, capture_output=True,
                                            env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                                     GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")) for s in steps]
        run(planner.switch_steps(self.bench, "erpnext", "version-16", "upstream", stash=True))
        state = {a["name"]: a for a in sites.apps(self.bench)}["erpnext"]
        self.assertEqual((state["branch"], state["upstream"], state["changed"]), ("version-16", "upstream/version-16", False))
        self.assertEqual((app / "f.txt").read_text(), "16\n")
        self.assertTrue((app / ".git" / "shallow").exists())  # still shallow: not the app's whole history
        self.assertEqual(sorted(sites.local_branches(self.bench, "erpnext")), ["version-15", "version-16"])
        run(planner.switch_steps(self.bench, "erpnext", "version-15"))  # back, to the branch it already has
        git(app, "stash", "pop", "-q")
        self.assertEqual((app / "f.txt").read_text(), "my work\n")

    def test_backups_pair_their_files_newest_first_including_frappe_cloud_downloads(self):
        backups = Path(self.bench) / "sites" / "a.localhost" / "private" / "backups"
        backups.mkdir(parents=True)
        for name in ("20260101_000000-a_localhost-database.sql.gz", "20260101_000000-a_localhost-files.tar"):
            (backups / name).write_bytes(b"x" * 10)
        cloud = Path(self.bench) / "fc-backups" / "shop.frappe.cloud"
        cloud.mkdir(parents=True)
        (cloud / "20260202_000000-shop_frappe_cloud-database.sql.gz").write_bytes(b"y")
        os.utime(backups / "20260101_000000-a_localhost-database.sql.gz", (1, 1))
        found = sites.backups(self.bench)
        self.assertEqual([b["site"] for b in found], ["shop.frappe.cloud (Frappe Cloud)", "a.localhost"])
        self.assertTrue(found[1]["public"].endswith("-files.tar"))
        self.assertEqual((found[1]["private"], found[1]["size"]), ("", 20))

    def test_logs_are_read_from_an_offset_and_again_after_rotation(self):
        logs = Path(self.bench) / "logs"
        logs.mkdir()
        (logs / "web.log").write_text("one\n\x1b[31mERROR\x1b[0m two\n")
        first = sites.read_log(self.bench, "web.log")
        self.assertEqual(first["text"], "one\nERROR two\n")
        with open(logs / "web.log", "a") as fh:
            fh.write("three\n")
        self.assertEqual(sites.read_log(self.bench, "web.log", first["offset"])["text"], "three\n")
        (logs / "web.log").write_text("new\n")  # rotated: shorter than the offset
        self.assertEqual(sites.read_log(self.bench, "web.log", first["offset"] + 6)["text"], "new\n")
        self.assertEqual([l["name"] for l in sites.logs(self.bench)], ["web.log"])
        for bad in ("../sites/a.localhost/site_config.json", ".hidden", ""):
            with self.assertRaises(sites.SiteError):
                sites.read_log(self.bench, bad)

    def test_config_hides_secrets_and_keeps_them_when_saved_back(self):
        path = Path(self.bench) / "sites" / "a.localhost" / "site_config.json"
        path.write_text(json.dumps({"db_name": "_abc", "db_password": "pw", "encryption_key": "k", "developer_mode": 0}))
        text = sites.read_config(self.bench, "a.localhost")
        self.assertNotIn('"pw"', text)
        edited = json.loads(text)
        edited["developer_mode"] = 1
        sites.write_config(self.bench, "a.localhost", json.dumps(edited))
        self.assertEqual(json.loads(path.read_text()),
                         {"db_name": "_abc", "db_password": "pw", "encryption_key": "k", "developer_mode": 1})
        for bad in ("[1]", "{", json.dumps({"developer_mode": 1})):
            with self.assertRaises(sites.SiteError):
                sites.write_config(self.bench, "a.localhost", bad)
        with self.assertRaises(sites.SiteError):
            sites.read_config(self.bench, "../a.localhost")

    def test_login_url_from_the_session_frappe_makes(self):
        ran = []

        def run(argv, **kw):
            ran.append((argv, kw["cwd"]))
            return subprocess.CompletedProcess(argv, 0, "some output\nabc123sid456\n", "")
        self.assertEqual(sites.login_url(self.bench, "a.localhost", 8001, run=run), "http://a.localhost:8001/app?sid=abc123sid456")
        self.assertEqual(ran[0][1], os.path.join(self.bench, "sites"))
        failing = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "Traceback\nredis.ConnectionError: refused\n")
        with self.assertRaisesRegex(sites.SiteError, "ConnectionError: refused"):
            sites.login_url(self.bench, "a.localhost", 8001, run=failing)
        os.makedirs(Path(self.bench) / "sites" / "nowhere.invalid")
        (Path(self.bench) / "sites" / "nowhere.invalid" / "site_config.json").write_text("{}")
        with self.assertRaisesRegex(sites.SiteError, "/etc/hosts"):
            sites.login_url(self.bench, "nowhere.invalid", 8001, run=run)

    def test_diagnose_names_the_patch_and_the_kind_of_failure(self):
        out = ("Migrating a.localhost\nExecuting erpnext.patches.v15_0.one in a.localhost (_abc)\n"
               "Executing erpnext.patches.v15_0.two in a.localhost (_abc)\nTraceback (most recent call last):\n"
               "pymysql.err.OperationalError: (1054, \"Unknown column 'qty' in 'field list'\")\n")
        found = sites.diagnose(out)
        self.assertEqual((found["patch"], found["kind"]), ("erpnext.patches.v15_0.two", "Missing column"))
        self.assertIn("Unknown column", found["error"])
        self.assertIsNone(sites.diagnose("Migrating a.localhost\nall good\n"))


class PlansTest(unittest.TestCase):
    def test_migrate_can_back_up_each_site_first_in_the_same_step(self):
        steps = planner.plan_migrate({"folder": "/b", "sites": ["a.localhost"], "backup": True})
        self.assertEqual(steps[0].argv[:2], ["sh", "-c"])
        self.assertEqual(steps[0].argv[-1], "a.localhost")
        self.assertIn("backup --with-files && bench", steps[0].argv[2])

    def test_drop_hides_the_root_password_and_needs_one(self):
        step, = planner.plan_drop("/b", "a.localhost", "rootpw")
        self.assertNotIn("rootpw", step.display())
        with self.assertRaises(planner.PlanError):
            planner.plan_drop("/b", "a.localhost", "")

    def test_app_update_pulls_or_switches_then_brings_the_bench_along(self):
        pull = planner.plan_app_update("/b", "erpnext")
        self.assertEqual([s.id for s in pull], ["pull", "requirements", "migrate", "build"])
        self.assertEqual(pull[0].argv, ["bench", "update", "--pull", "--apps", "erpnext"])
        switch = planner.plan_app_update("/b", "erpnext", "version-16", "upstream", stash=True, migrate=False)
        self.assertEqual([s.id for s in switch], ["stash-erpnext", "fetch-erpnext", "switch-erpnext", "requirements", "build"])
        self.assertNotIn("-f", switch[2].argv)
        self.assertEqual(planner.plan_app_update("/b", "erpnext", "feature/x")[0].argv, ["git", "checkout", "feature/x", "--"])
        for bad in ("--force", "a/../b", "x.lock", ""):
            with self.assertRaises(planner.PlanError):
                planner.plan_app_update("/b", "erpnext", bad or None, "upstream") if bad else planner.switch_steps("/b", "erpnext", bad)
        with self.assertRaises(planner.PlanError):
            planner.plan_app_update("/b", "erpnext", "version-16", "--upload-pack=x")

    def test_download_keeps_the_signed_link_out_of_the_log_and_refuses_odd_ones(self):
        steps = planner.plan_download("/b", "shop.frappe.cloud", {"database": ("x-database.sql.gz", "https://s3/x?sig=SECRET")})
        self.assertTrue(steps[1].argv[-2].endswith("/b/fc-backups/shop.frappe.cloud/x-database.sql.gz"))
        self.assertNotIn("SECRET", steps[1].display())
        for name, url in (("../x", "https://s3/x"), ("x.sql.gz", "file:///etc/passwd")):
            with self.assertRaises(planner.PlanError):
                planner.plan_download("/b", "shop.frappe.cloud", {"database": (name, url)})


class FrappeCloudTest(unittest.TestCase):
    def reply(self, message):
        return lambda request, timeout: io.BytesIO(json.dumps({"message": message}).encode())

    def test_request_access_sends_a_code_and_keeps_the_token(self):
        sent = []

        def open_url(request, timeout):
            sent.append(request)
            return io.BytesIO(json.dumps({"message": {"token": "T", "approval_url": "https://cloud/approve"}}).encode())
        access = frappecloud.request_access("Shop.Frappe.Cloud", open_url=open_url)
        self.assertEqual(access["token"], "T")
        self.assertIn(f"code={access['code']}", sent[0].data.decode())
        self.assertIn("domain=shop.frappe.cloud", sent[0].data.decode())
        with self.assertRaises(frappecloud.CloudError):
            frappecloud.request_access("not a domain", open_url=open_url)

    def test_an_organisations_own_frappe_cloud_address_is_used_and_checked(self):
        sent = []

        def open_url(request, timeout):
            sent.append(request.full_url)
            return io.BytesIO(json.dumps({"message": {"token": "T"}}).encode())
        access = frappecloud.request_access("shop.example.com", "Cloud.Example.com/", open_url=open_url)
        self.assertEqual(access["url"], "https://cloud.example.com")
        self.assertTrue(sent[0].startswith("https://cloud.example.com/api/method/press.api.v1_migration.request_access"))
        for bad in ("http://cloud.example.com", "https://cloud.example.com/x", "https://user@cloud.example.com", "https://"):
            with self.assertRaises(frappecloud.CloudError):
                frappecloud.cloud_url(bad)

    def test_links_keep_only_https_and_name_each_file(self):
        found = frappecloud.links("T", "b1", open_url=self.reply({
            "database": "https://s3.example/b/20260101-shop-database.sql.gz?X-Sig=1", "public": "", "private": "http://x/y.tar"}))
        self.assertEqual(found, {"database": ("20260101-shop-database.sql.gz", "https://s3.example/b/20260101-shop-database.sql.gz?X-Sig=1")})

    def test_frappe_error_message_is_passed_on(self):
        def open_url(request, timeout):
            body = json.dumps({"_server_messages": json.dumps([json.dumps({"message": "<b>Invalid</b> token"})])}).encode()
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))
        with self.assertRaisesRegex(frappecloud.CloudError, "^Invalid token$"):
            frappecloud.call("get_status", "T", open_url=open_url)
