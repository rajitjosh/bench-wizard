import http.client
import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

from bench_wizard import catalog, server
from bench_wizard.backup import BackupError, BackupInfo
from bench_wizard.deps import AppInfo, InspectError, Resolution
from bench_wizard.existing import BenchError, BenchInfo

ROOT = Path(__file__).resolve().parent.parent
ROWS = [{"id": "python", "title": "Python", "note": "", "required": "3.11", "status": "ok", "found": "3.11.9"}]


class FakeChecks:
    def __init__(self):
        self.installed = []
        self.root_password = "rootpw"

    def detect_all(self, profile):
        return [dict(r, required=profile["python"]) for r in ROWS]

    def install(self, check_id, profile, sudo_password):
        self.installed.append((check_id, profile["node"] if check_id in ("node", "yarn") else profile["python"],
                               sudo_password))
        return {"ok": True, "log": "done", "error": None}

    def verify_mariadb_root(self, password):
        return password == self.root_password

    def set_mariadb_root(self, sudo_password, password):
        self.root_password = password
        return {"ok": True, "error": None, "log": ""}

    def build_env(self, node):
        return {"PATH": "/usr/bin:/bin", "NODE": node}


def fake_resolve(selected, cat, version, inspect_fn=None, **kw):
    apps = {n: dict(a) for n, a in selected.items()}
    errors = ["Broken needs nothing good"] if "broken" in apps else []
    return Resolution(order=list(apps), apps=apps, notices=["a notice"], errors=errors)


def fake_preflight(answers, resolution, backup_info, cat, env, probe=None, **kw):
    return [{"ok": True, "label": "folder ok"}] + [{"ok": False, "label": e} for e in resolution.errors]


SEEN_ENV = []


def fake_inspect_app(repo, branch, env=None):
    SEEN_ENV.append((repo, dict(env or {})))
    if "bad" in repo:
        raise InspectError(f"repository not found: {repo}")
    name = repo.rstrip("/").rsplit("/", 1)[-1].replace(".git", "").replace("-", "_")
    if name == "app":
        name = "custom_app"
    return AppInfo(name, "Custom App", "Does things", "", {"frappe": "", "erpnext": ""})


def fake_inspect_backup(path):
    if "bad" in path:
        raise BackupError("cannot read backup")
    return BackupInfo(apps={"frappe": "15.1.0", "erpnext": "15.2.0"}, frappe_major=15)


class ServerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.checks = FakeChecks()
        self.revealed = []
        self.app = server.App(
            catalog.load(ROOT / "catalog.json"), state_root=str(self.tmp / "state"), token="tok",
            checks=self.checks, resolve=fake_resolve, preflight=fake_preflight,
            inspect_app=fake_inspect_app, inspect_backup=fake_inspect_backup, reveal=self.revealed.append,
            home=str(self.tmp / "home"), port_free=lambda port: True, resources=self.fake_resources)
        self.httpd = server.make_server(self.app, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    warnings = []

    def fake_resources(self, path):
        return {"memory": {"total": 16, "available": 8}, "disk": {"path": path, "total": 100, "free": 50},
                "warnings": list(self.warnings)}

    def request(self, method, path, body=None, token="tok", host=None, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Token"] = token
        if origin:
            headers["Origin"] = origin
        data = json.dumps(body).encode() if body is not None else None
        if data:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        ctype = resp.getheader("Content-Type", "")
        return resp.status, (json.loads(raw) if "json" in ctype else raw.decode())

    def answers(self, **over):
        base = {"version": "version-15", "folder": str(self.tmp / "bench"), "site": "s.localhost",
                "db_name": "", "admin_password": "adm in'pw", "mariadb_root_password": "rootpw",
                "apps": [{"name": "erpnext", "repo": "https://github.com/frappe/erpnext.git",
                          "branch": "version-15", "title": "ERPNext"}],
                "restore": None}
        base.update(over)
        return base

    def wait_run(self, *states):
        deadline = time.time() + 10
        while time.time() < deadline:
            status, snap = self.request("GET", "/api/run")
            if snap["state"] in states:
                return snap
            time.sleep(0.05)
        self.fail(f"run stuck in {snap['state']}")

    # -- access control ------------------------------------------------
    def test_api_needs_token(self):
        self.assertEqual(self.request("GET", "/api/catalog", token=None)[0], 403)
        self.assertEqual(self.request("GET", "/api/catalog", token="wrong")[0], 403)

    def test_token_in_query_only_for_event_stream(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/catalog?token=tok")
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_foreign_host_is_refused(self):
        self.assertEqual(self.request("GET", "/api/catalog", host="evil.example:80")[0], 403)
        self.assertEqual(self.request("GET", "/", host="evil.example")[0], 403)

    def test_localhost_host_is_accepted(self):
        self.assertEqual(self.request("GET", "/api/catalog", host=f"localhost:{self.port}")[0], 200)

    def test_static_files(self):
        status, body = self.request("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn("Bench Wizard", body)
        self.assertEqual(self.request("GET", "/app.js", token=None)[0], 200)
        self.assertEqual(self.request("GET", "/style.css", token=None)[0], 200)

    def test_path_traversal_is_not_served(self):
        self.assertEqual(self.request("GET", "/../catalog.json", token=None)[0], 404)
        self.assertEqual(self.request("GET", "/%2e%2e/catalog.json", token=None)[0], 404)

    def test_unknown_route_and_bad_json(self):
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/plan", body=b"{not json", headers={"X-Token": "tok"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    # -- wizard data ---------------------------------------------------
    def test_catalog(self):
        status, data = self.request("GET", "/api/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(data["default_version"], "version-15")
        self.assertEqual(data["default_preset"], "plain-erpnext")
        self.assertIn("erpnext", data["apps"])
        self.assertTrue(data["home"].startswith("/"))
        self.assertTrue(data["wizard_dir"].startswith("/"))

    def test_checks_for_a_version(self):
        status, data = self.request("GET", "/api/checks?version=version-16")
        self.assertEqual(data["rows"][0]["required"], "3.14")
        self.assertEqual(self.request("GET", "/api/checks?version=nope")[0], 400)

    def test_install_returns_fresh_rows(self):
        status, data = self.request("POST", "/api/checks/install",
                                    {"id": "python", "version": "version-15", "sudo_password": "sp"})
        self.assertTrue(data["ok"])
        self.assertEqual(self.checks.installed, [("python", "3.11", "sp")])
        self.assertEqual(data["rows"][0]["id"], "python")

    def test_review_installs_the_node_an_app_needs(self):
        status, data = self.request("POST", "/api/checks/install", {"id": "node", "node": "22"})
        self.assertEqual((status, data["ok"]), (200, True))
        self.assertEqual(self.checks.installed, [("node", "22", "")])  # never with a sudo password
        for bad in ({"id": "node", "node": "22; rm -rf ~"}, {"id": "python", "node": "22"}):
            self.assertEqual(self.request("POST", "/api/checks/install", bad)[0], 400, bad)
        self.assertEqual(len(self.checks.installed), 1)

    def test_mariadb_verify_and_set(self):
        self.assertFalse(self.request("POST", "/api/mariadb/verify", {"password": "x"})[1]["ok"])
        self.assertTrue(self.request("POST", "/api/mariadb/verify", {"password": "rootpw"})[1]["ok"])
        self.request("POST", "/api/mariadb/set-password", {"sudo_password": "sp", "password": "new"})
        self.assertTrue(self.request("POST", "/api/mariadb/verify", {"password": "new"})[1]["ok"])

    def test_inspect_custom_app(self):
        status, data = self.request("POST", "/api/apps/inspect", {"repo": "git@x:me/app.git", "branch": "main"})
        self.assertEqual(data["app"], {"name": "custom_app", "title": "Custom App", "description": "Does things",
                                       "requires": ["erpnext", "frappe"], "repo": "git@x:me/app.git",
                                       "branch": "main", "credentials": False})
        status, data = self.request("POST", "/api/apps/inspect", {"repo": "bad", "branch": "main"})
        self.assertFalse(data["ok"])
        self.assertIn("repository not found", data["error"])

    TOKEN_URL = "https://alice:ghp_exampletoken123@github.com/acme/private-app.git"

    def test_credentials_in_a_url_are_held_in_memory_and_never_returned(self):
        status, data = self.request("POST", "/api/apps/inspect", {"repo": self.TOKEN_URL, "branch": "main"})
        self.assertEqual(data["app"]["repo"], "https://github.com/acme/private-app.git")
        self.assertTrue(data["app"]["credentials"])
        self.assertNotIn("ghp_", json.dumps(data))
        repo, env = SEEN_ENV[-1]
        self.assertEqual(repo, "https://github.com/acme/private-app.git")
        self.assertIn("alice:ghp_exampletoken123@github.com/acme/", env["GIT_CONFIG_KEY_0"])

    def test_inspect_error_does_not_echo_credentials(self):
        url = "https://alice:ghp_exampletoken123@github.com/acme/bad.git"
        status, data = self.request("POST", "/api/apps/inspect", {"repo": url, "branch": "main"})
        self.assertFalse(data["ok"])
        self.assertNotIn("ghp_", json.dumps(data))

    def test_plan_and_run_use_held_credentials_without_showing_them(self):
        self.request("POST", "/api/apps/inspect", {"repo": self.TOKEN_URL, "branch": "main"})
        app = {"name": "private_app", "repo": self.TOKEN_URL, "branch": "main", "title": "Private"}
        status, data = self.request("POST", "/api/plan", self.answers(apps=[app]))
        self.assertEqual(status, 200)
        self.assertNotIn("ghp_", json.dumps(data))
        get = next(i for i, s in enumerate(data["steps"]) if s["id"] == "get-private_app")
        self.assertIn("https://github.com/acme/private-app.git", data["steps"][get]["command"])
        self.assertIn("ghp_exampletoken123", self.app.pending["env"]["GIT_CONFIG_KEY_0"])
        self.assertNotIn("ghp_", " ".join(self.app.pending["steps"][get].argv))

    def test_import_list_keeps_order_and_reports_failures(self):
        body = {"apps": [{"url": "https://github.com/x/zeta", "branch": "v1.0.0"},
                         {"url": "https://github.com/x/bad", "branch": "main"},
                         {"url": self.TOKEN_URL, "branch": "main"},
                         {"url": "https://github.com/x/alpha.git"}]}
        status, data = self.request("POST", "/api/apps/import", body)
        self.assertEqual(status, 200)
        self.assertEqual([r.get("name") for r in data["apps"]], ["zeta", None, "private_app", "alpha"])
        self.assertEqual([r["ok"] for r in data["apps"]], [True, False, True, True])
        self.assertEqual(data["apps"][0]["branch"], "v1.0.0")
        self.assertEqual(data["apps"][3]["branch"], "version-15")
        self.assertIn("not found", data["apps"][1]["error"])
        self.assertNotIn("ghp_", json.dumps(data))

    def test_import_rejects_something_that_is_not_a_list(self):
        self.assertEqual(self.request("POST", "/api/apps/import", {"apps": {"url": "x"}})[0], 400)
        self.assertEqual(self.request("POST", "/api/apps/import", {"apps": [{"branch": "x"}]})[0], 400)

    def test_inspect_backup(self):
        status, data = self.request("POST", "/api/backup/inspect", {"path": "/b/db.sql.gz"})
        self.assertEqual(data["apps"], {"frappe": "15.1.0", "erpnext": "15.2.0"})
        self.assertEqual(data["frappe_major"], 15)
        self.assertFalse(self.request("POST", "/api/backup/inspect", {"path": "/bad.sql"})[1]["ok"])

    # -- plan and run --------------------------------------------------
    def test_plan_returns_masked_commands(self):
        status, data = self.request("POST", "/api/plan", self.answers())
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        text = json.dumps(data)
        self.assertNotIn("rootpw", text)
        self.assertNotIn("adm in", text)
        commands = {s["id"]: s["command"] for s in data["steps"]}
        self.assertTrue(data["steps"][0]["command"].startswith("bench init"))
        self.assertIn("••••", commands["new-site"])
        self.assertEqual(data["notices"], ["a notice"])
        self.assertEqual(data["apps"][0]["name"], "erpnext")

    def test_plan_expands_home_in_folder(self):
        status, data = self.request("POST", "/api/plan", self.answers(folder="~/xy/bench"))
        self.assertTrue(data["folder"].startswith("/"))
        self.assertTrue(data["folder"].endswith("/xy/bench"))

    def test_plan_rejects_folder_with_a_space(self):
        status, data = self.request("POST", "/api/plan", self.answers(folder="~/x y/bench"))
        self.assertEqual(status, 400)
        self.assertIn("space", data["error"])

    def test_plan_for_an_existing_bench(self):
        bench = BenchInfo(str(self.tmp / "b"), "version-15", "", ["frappe", "erpnext", "crm"],
                          {"s.localhost": {"apps": ["frappe", "erpnext"]}, "x.localhost": {"error": "no database"}})
        self.app.inspect_bench_fn = lambda folder, cat, env: bench
        status, res = self.request("POST", "/api/bench/inspect", {"folder": "~/b"})
        self.assertEqual((status, res["version"], res["apps"]), (200, "version-15", ["frappe", "erpnext", "crm"]))
        apps = [{"name": "erpnext", "repo": "", "branch": ""}, {"name": "crm", "repo": "", "branch": ""},
                {"name": "hrms", "repo": "https://github.com/frappe/hrms.git", "branch": "version-15"}]
        body = {"existing": True, "folder": "~/b", "site": "s.localhost", "apps": apps}
        status, plan = self.request("POST", "/api/plan", body)
        self.assertEqual(status, 200)
        # erpnext is installed already; crm is in the bench; hrms must be fetched; no .nvmrc yet, so one is written
        self.assertEqual([s["id"] for s in plan["steps"]], ["nvmrc", "get-hrms", "clear-cache", "install-crm", "install-hrms", "build"])
        self.assertTrue(plan["existing"])
        self.assertEqual(self.app.pending["services"][0]["before"], "install-crm")
        status, res = self.request("POST", "/api/plan", dict(body, site="x.localhost"))
        self.assertEqual(status, 400)
        self.assertIn("no database", res["error"])

    def test_new_site_from_a_backup_in_an_existing_bench(self):
        bench = BenchInfo(str(self.tmp / "b"), "version-15", "18", ["frappe", "erpnext"],
                          {"s.localhost": {"apps": ["frappe", "erpnext"]}})
        self.app.inspect_bench_fn = lambda folder, cat, env: bench
        body = {"existing": True, "folder": "~/b", "site": "copy.localhost", "admin_password": "adm",
                "mariadb_root_password": "rootpw", "db_name": "",
                "apps": [{"name": "erpnext", "repo": "", "branch": ""}],
                "restore": {"sql": "/b/db.sql.gz", "mute_emails": True, "pause_scheduler": True, "sanitize": True}}
        status, plan = self.request("POST", "/api/plan", body)
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in plan["steps"]], [
            "new-site", "restore", "mute-emails", "pause-scheduler", "sanitize", "migrate", "admin-password", "build"])
        self.assertEqual(plan["restore"], {"mute_emails": True, "pause_scheduler": True, "sanitize": True})
        self.assertEqual(self.app.pending["services"][0]["before"], "new-site")
        status, res = self.request("POST", "/api/plan", dict(body, site="s.localhost"))
        self.assertEqual(status, 400)
        self.assertIn("already", res["error"])

    def test_inspect_bench_error_is_reported(self):
        def fail(folder, cat, env):
            raise BenchError("not a bench")
        self.app.inspect_bench_fn = fail
        self.assertEqual(self.request("POST", "/api/bench/inspect", {"folder": "/x"}), (400, {"error": "not a bench"}))

    def test_plan_with_missing_answer_is_rejected(self):
        status, data = self.request("POST", "/api/plan", self.answers(site=""))
        self.assertEqual(status, 400)
        self.assertIn("site name", data["error"])

    def test_plan_with_failed_preflight_is_not_ok(self):
        broken = {"name": "broken", "repo": "r", "branch": "main", "title": "Broken"}
        status, data = self.request("POST", "/api/plan", self.answers(apps=[broken]))
        self.assertFalse(data["ok"])

    def test_run_without_plan_is_refused(self):
        self.assertEqual(self.request("POST", "/api/run", {})[0], 409)

    def test_run_refused_when_preflight_failed(self):
        broken = {"name": "broken", "repo": "r", "branch": "main", "title": "Broken"}
        self.request("POST", "/api/plan", self.answers(apps=[broken]))
        self.assertEqual(self.request("POST", "/api/run", {})[0], 409)
        self.assertEqual(self.request("GET", "/api/run")[1]["state"], "none")

    def start_real_run(self):
        """Plan normally, then swap the steps for harmless ones before running."""
        from bench_wizard.planner import Step
        self.request("POST", "/api/plan", self.answers())
        cwd = str(self.tmp)
        self.app.pending["steps"] = [Step("a", "a", ["sh", "-c", "echo hello"], cwd),
                                     Step("b", "b", ["sh", "-c", "exit 1"], cwd)]
        return self.request("POST", "/api/run", {})

    def test_run_executes_and_reports(self):
        status, data = self.start_real_run()
        self.assertEqual(status, 200)
        snap = self.wait_run("failed")
        self.assertEqual([s["state"] for s in snap["steps"]], ["done", "failed"])
        self.assertEqual(snap["failed"], "b")
        self.assertEqual(snap["summary"]["site"], "s.localhost")
        self.assertEqual(snap["summary"]["folder"], str(self.tmp / "bench"))
        self.assertEqual(snap["summary"]["apps"][0]["name"], "erpnext")
        self.assertEqual(snap["summary"]["node"], "18")
        self.assertIsNone(snap["summary"]["restore"])
        self.assertNotIn("rootpw", json.dumps(snap))
        self.assertNotIn("adm in", json.dumps(snap))
        status, log = self.request("GET", "/api/run/log?step=a")
        self.assertIn("hello", log["log"])
        self.assertTrue((self.tmp / "state" / data["run_id"] / "a.log").exists())

    def test_second_run_refused_while_one_is_active(self):
        from bench_wizard.planner import Step
        self.request("POST", "/api/plan", self.answers())
        self.app.pending["steps"] = [Step("a", "a", ["sleep", "5"], str(self.tmp))]
        self.assertEqual(self.request("POST", "/api/run", {})[0], 200)
        self.assertEqual(self.request("POST", "/api/run", {})[0], 409)
        self.request("POST", "/api/run/cancel", {})
        self.wait_run("cancelled")

    def test_plan_migrate_for_several_sites(self):
        bench = BenchInfo(str(self.tmp / "b"), "version-15", "18", ["frappe"],
                          {"a.localhost": {"apps": ["frappe"]}, "b.localhost": {"error": "no database"}})
        self.app.inspect_bench_fn = lambda folder, cat, env: bench
        body = {"migrate": True, "folder": "~/b", "sites": ["a.localhost", "b.localhost", "a.localhost"]}
        status, plan = self.request("POST", "/api/plan", body)
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in plan["steps"]], ["migrate-a.localhost", "migrate-b.localhost"])
        self.assertEqual((plan["ok"], plan["migrate"], plan["node"]), (True, ["a.localhost", "b.localhost"], "18"))
        self.assertTrue(self.app.pending["parallel"])
        self.assertEqual(self.app.pending["services"][0]["before"], "migrate-a.localhost")
        for sites in ([], ["nope.localhost"], None):
            self.assertEqual(self.request("POST", "/api/plan", dict(body, sites=sites))[0], 400, sites)

    def plan_sleep(self, folder):
        from bench_wizard.planner import Step
        self.request("POST", "/api/plan", self.answers(folder=folder))
        self.app.pending["steps"] = [Step("a", "a", ["sleep", "5"], str(self.tmp))]
        status, data = self.request("POST", "/api/run", {})
        return status, data.get("run_id")

    def test_runs_in_different_benches_go_side_by_side_but_not_in_one_bench(self):
        status, first = self.plan_sleep(str(self.tmp / "one"))
        status2, second = self.plan_sleep(str(self.tmp / "two"))
        self.assertEqual((status, status2), (200, 200))
        self.assertNotEqual(first, second)
        self.assertEqual(self.plan_sleep(str(self.tmp / "one"))[0], 409)
        runs = self.request("GET", "/api/runs")[1]["runs"]
        self.assertEqual([(r["run_id"], r["state"]) for r in runs], [(second, "running"), (first, "running")])
        self.assertEqual(self.request("GET", "/api/run")[1]["run_id"], second)  # the last one by default
        self.assertEqual(self.request("GET", f"/api/run?run={first}")[1]["summary"]["folder"], str(self.tmp / "one"))
        self.assertEqual(self.request("POST", "/api/run/cancel", {"run": first})[0], 200)
        self.wait_run_id(first, "cancelled")
        self.assertEqual(self.request("GET", f"/api/run?run={second}")[1]["state"], "running")
        self.assertEqual(self.request("POST", "/api/run/dismiss", {"run": first})[0], 200)
        self.assertEqual(self.request("GET", f"/api/run?run={first}")[1]["state"], "none")
        self.assertEqual(self.request("POST", "/api/run/cancel", {"run": first})[0], 409)
        self.request("POST", "/api/run/cancel", {"run": second})
        self.wait_run_id(second, "cancelled")

    def wait_run_id(self, run_id, state):
        deadline = time.time() + 10
        while self.request("GET", f"/api/run?run={run_id}")[1]["state"] != state:
            self.assertLess(time.time(), deadline, f"{run_id} never reached {state}")
            time.sleep(0.05)

    def test_dismiss_forgets_a_finished_run_but_not_a_running_one(self):
        from bench_wizard.planner import Step
        self.start_real_run()
        self.wait_run("failed")
        self.assertEqual(self.request("POST", "/api/run/dismiss", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/run")[1]["state"], "none")
        self.request("POST", "/api/plan", self.answers())
        self.app.pending["steps"] = [Step("a", "a", ["sleep", "5"], str(self.tmp))]
        self.request("POST", "/api/run", {})
        self.assertEqual(self.request("POST", "/api/run/dismiss", {})[0], 409)
        self.request("POST", "/api/run/cancel", {})
        self.wait_run("cancelled")

    def test_retry(self):
        self.start_real_run()
        self.wait_run("failed")
        self.assertEqual(self.request("POST", "/api/run/retry", {"cleanup": False})[0], 200)
        self.wait_run("failed")
        self.assertEqual(self.request("POST", "/api/run/cancel", {})[0], 200)

    def test_log_step_name_cannot_escape_the_log_folder(self):
        self.start_real_run()
        self.wait_run("failed")
        self.assertEqual(self.request("GET", "/api/run/log?step=../../x")[0], 404)

    def test_event_stream_sends_snapshot_on_connect(self):
        self.start_real_run()
        self.wait_run("failed")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/run/events?token=tok")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.getheader("Content-Type"))
        line = resp.fp.readline().decode()
        conn.close()
        self.assertTrue(line.startswith("data: "))
        event = json.loads(line[6:])
        self.assertEqual(event["type"], "snapshot")
        self.assertEqual(event["state"], "failed")

    def test_event_stream_needs_token(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/run/events")
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    # -- projects, ports, marketplace ----------------------------------
    def two_clashing_benches(self):
        from bench_wizard import projects
        from tests.test_projects import make_bench
        base = projects.slot(0)
        ports = dict(base, redis_cache="redis://127.0.0.1:13000", redis_queue="redis://127.0.0.1:11000")
        return (make_bench(self.tmp / "home" / "git" / "a" / "frappe-bench", ports),
                make_bench(self.tmp / "home" / "git" / "b" / "frappe-bench", ports, sites=("x.localhost", "y.localhost")))

    def test_projects_lists_benches_ports_clashes_and_resources(self):
        a, b = self.two_clashing_benches()
        status, data = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual([x["folder"] for x in data["benches"]], [a, b])
        self.assertEqual(data["benches"][1]["sites"], ["x.localhost", "y.localhost"])
        self.assertIn(8000, data["benches"][0]["clashes"])
        self.assertEqual(data["benches"][0]["version_title"], "Version 15")
        self.assertEqual(data["resources"]["disk"]["free"], 50)

    def test_projects_add_remembers_a_bench_outside_home(self):
        from tests.test_projects import make_bench
        outside = make_bench(self.tmp / "elsewhere" / "bench")
        self.assertEqual(self.request("POST", "/api/projects/add", {"folder": str(self.tmp / "nope")})[0], 400)
        self.assertEqual(self.request("POST", "/api/projects/add", {"folder": "/"})[0], 400)
        for empty in ("", "  ", None):
            self.assertEqual(self.request("POST", "/api/projects/add", {"folder": empty})[0], 400)
        self.assertFalse((self.tmp / "state" / "folders.json").exists())
        self.assertEqual(self.request("POST", "/api/projects/add", {"folder": outside})[1]["kind"], "bench")
        bench = self.request("GET", "/api/projects")[1]["benches"][0]
        self.assertEqual((bench["folder"], bench["added"]), (outside, True))
        self.request("POST", "/api/projects/remove", {"folder": outside})
        self.assertEqual(self.request("GET", "/api/projects")[1]["benches"], [])

    def test_browsing_lists_folders_and_marks_benches(self):
        from tests.test_projects import make_bench
        root = self.tmp / "home"
        bench = make_bench(root / "acme-bench")
        (root / "Zeta").mkdir()
        (root / ".hidden").mkdir()
        (root / "notes.txt").write_text("")
        status, d = self.request("GET", "/api/folders")  # the home folder first
        self.assertEqual((status, d["path"], d["parent"]), (200, str(root), str(self.tmp)))
        self.assertEqual([(f["name"], f["bench"]) for f in d["folders"]], [("acme-bench", True), ("Zeta", False)])
        self.assertTrue(self.request("GET", "/api/folders?path=" + bench)[1]["bench"])
        self.assertIsNone(self.request("GET", "/api/folders?path=/")[1]["parent"])
        self.assertEqual(self.request("GET", "/api/folders?path=" + str(root / "nope"))[0], 400)

    def test_folders_added_are_searched_for_benches_until_removed(self):
        from tests.test_projects import make_bench
        srv = self.tmp / "srv"
        one, two = make_bench(srv / "client-a" / "frappe-bench"), make_bench(srv / "client-b")
        self.assertEqual(self.request("POST", "/api/projects/add", {"folder": str(srv)})[1]["kind"], "folder")
        self.request("POST", "/api/projects/add", {"folder": str(srv)})  # twice: listed once
        data = self.request("GET", "/api/projects")[1]
        self.assertEqual((data["folders"], [b["folder"] for b in data["benches"]]), ([str(srv)], [one, two]))
        self.assertFalse(any(b["added"] for b in data["benches"]))
        self.request("POST", "/api/projects/remove", {"folder": str(srv)})
        data = self.request("GET", "/api/projects")[1]
        self.assertEqual((data["folders"], data["benches"]), ([], []))

    def test_start_bench_checks_ports_then_starts_it(self):
        from tests.test_projects import make_bench
        folder = make_bench(self.tmp / "home" / "bench")
        started = []
        self.app.start_bench = lambda f, env: started.append(f)
        self.app.port_free = lambda port: port != 13000
        status, data = self.request("POST", "/api/bench/start", {"folder": folder})
        self.assertEqual(status, 409)
        self.assertIn("port 13000 is in use", data["error"])
        self.assertEqual(started, [])
        self.app.port_free = lambda port: True
        status, data = self.request("POST", "/api/bench/start", {"folder": folder})
        self.assertEqual((status, data["port"], started), (200, 8000, [folder]))
        self.app.start_bench = lambda f, env: "Address already in use"
        status, data = self.request("POST", "/api/bench/start", {"folder": folder})
        self.assertEqual(status, 409)
        self.assertIn("Address already in use", data["error"])
        self.assertEqual(self.request("POST", "/api/bench/start", {"folder": str(self.tmp)})[0], 400)

    def test_stop_bench(self):
        from tests.test_projects import make_bench
        folder = make_bench(self.tmp / "home" / "bench")
        for result, status in ((2, 200), (0, 409), (None, 400)):
            self.app.stop_bench = lambda f, result=result: result
            self.assertEqual(self.request("POST", "/api/bench/stop", {"folder": folder})[0], status)

    def test_allocate_then_plan_ports_for_a_clashing_bench(self):
        from bench_wizard import projects
        a, b = self.two_clashing_benches()
        moves = self.request("POST", "/api/ports/allocate", {})[1]["moves"]
        self.assertEqual(moves, {b: projects.slot(1)})
        status, plan = self.request("POST", "/api/plan", {"folder": b, "ports": moves[b]})
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in plan["steps"]], ["ports", "setup-redis", "procfile"])
        self.assertEqual(plan["title"], "Move to port 8001")
        bad = dict(moves[b], webserver_port=80)
        self.assertEqual(self.request("POST", "/api/plan", {"folder": b, "ports": bad})[0], 400)
        self.assertEqual(self.request("POST", "/api/plan", {"folder": str(self.tmp), "ports": moves[b]})[0], 400)

    def test_bench_on_an_older_frappe_can_still_move_ports_build_and_start(self):
        from bench_wizard import projects
        from tests.test_projects import make_bench
        old = make_bench(self.tmp / "home" / "old" / "frappe-bench", projects.slot(0), version="14.2.0")
        (Path(old) / "env").mkdir()
        (Path(old) / "env" / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.10.12\n")
        status, plan = self.request("POST", "/api/plan", {"folder": old, "ports": projects.slot(3)})
        self.assertEqual((status, plan["version_title"], plan["python"]), (200, "Version 14", "3.10"))
        self.assertEqual(self.request("POST", "/api/plan", {"folder": old, "build": True})[0], 200)
        self.app.start_bench = lambda f, env: None
        self.assertEqual(self.request("POST", "/api/bench/start", {"folder": old})[0], 200)
        projects_list = self.request("GET", "/api/projects")[1]["benches"]
        self.assertEqual([b["version_title"] for b in projects_list], ["Version 14"])

    def test_bench_on_any_frappe_version_loads_and_migrates_but_no_new_bench_on_it(self):
        bench = BenchInfo(str(self.tmp / "b"), "version-13", "14", ["frappe"], {"a.localhost": {"apps": ["frappe"]}})
        self.app.inspect_bench_fn = lambda folder, cat, env: bench
        status, res = self.request("POST", "/api/bench/inspect", {"folder": "~/b"})
        self.assertEqual((status, res["profile"]["title"], res["profile"]["existing_only"]), (200, "Version 13", True))
        status, plan = self.request("POST", "/api/plan", {"migrate": True, "folder": "~/b", "sites": ["a.localhost"]})
        self.assertEqual((status, plan["version_title"], plan["node"]), (200, "Version 13", "14"))
        self.assertEqual(self.request("GET", "/api/checks?version=version-13")[0], 200)
        status, res = self.request("POST", "/api/plan", dict(self.answers(), version="version-13"))
        self.assertEqual(status, 400)
        self.assertIn("Version 15, Version 16 only", res["error"])

    def test_bench_page_reads_the_bench_and_plans_its_actions(self):
        from bench_wizard import projects
        from tests.test_projects import make_bench
        folder = make_bench(self.tmp / "home" / "b", projects.slot(0), sites=("a.localhost", "b.localhost"))
        (Path(folder) / "logs").mkdir()
        (Path(folder) / "logs" / "web.log").write_text("hello\n")
        status, d = self.request("GET", "/api/bench/detail?folder=" + folder)
        self.assertEqual((status, d["sites"], [l["name"] for l in d["logs"]], d["backups"]), (200, ["a.localhost", "b.localhost"], ["web.log"], []))
        self.assertEqual(self.request("GET", f"/api/bench/log?folder={folder}&name=web.log")[1]["text"], "hello\n")
        plan = lambda **body: self.request("POST", "/api/plan", dict(body, folder=folder))
        status, p = plan(op="backup")
        self.assertEqual((status, [s["id"] for s in p["steps"]], p["title"]), (200, ["backup-a.localhost", "backup-b.localhost"], "Back up 2 sites"))
        self.assertTrue(self.app.pending["parallel"])
        status, p = plan(op="drop", site="a.localhost", root_password="rootpw")
        self.assertEqual(status, 200)
        self.assertNotIn("rootpw", json.dumps(p))
        self.assertEqual(plan(op="drop", site="nope.localhost", root_password="x")[0], 400)
        self.assertEqual(plan(op="uninstall", site="a.localhost", app="hrms")[0], 400)  # not in the bench
        (Path(folder) / "apps" / "hrms" / ".git").mkdir(parents=True)
        self.assertEqual(plan(op="uninstall", site="a.localhost", app="hrms")[1]["title"], "Uninstall hrms from a.localhost")
        self.assertEqual(plan(op="update-app", app="hrms", branch="version-16")[0], 400)  # no such branch, no remote
        self.assertEqual(plan(op="explode")[0], 400)

    def test_change_branch_lists_local_branches_and_refuses_to_lose_local_changes(self):
        from bench_wizard import projects
        from tests.test_projects import make_bench
        from tests.test_sites import git
        folder = make_bench(self.tmp / "home" / "b", projects.slot(0), sites=("a.localhost",))
        remote, app = self.tmp / "remote", Path(folder) / "apps" / "custom"
        git(self.tmp, "init", "-q", "-b", "main", str(remote))
        (remote / "f.txt").write_text("1\n")
        git(remote, "add", "f.txt")
        git(remote, "commit", "-q", "-m", "one")
        git(remote, "branch", "develop")
        git(self.tmp, "clone", "-q", str(remote), str(app))  # remote "origin", as after bench new-app and a push
        git(app, "checkout", "-q", "-b", "feature/mine")  # never pushed
        status, res = self.request("POST", "/api/bench/branches", {"folder": folder, "app": "custom"})
        self.assertEqual((status, res["branches"][0], res["local_only"]), (200, "feature/mine", ["feature/mine"]))
        self.assertEqual(sorted(res["branches"]), ["develop", "feature/mine", "main"])
        plan = lambda **body: self.request("POST", "/api/plan", dict(body, folder=folder, op="update-app", app="custom"))
        status, p = plan(branch="develop", migrate=False)
        self.assertEqual([s["id"] for s in p["steps"]], ["fetch-custom", "switch-custom", "requirements", "build"])
        self.assertIn("origin", p["steps"][0]["command"])
        self.assertEqual([s["id"] for s in plan(branch="main")[1]["steps"]][:2], ["switch-custom", "requirements"])
        (app / "f.txt").write_text("my work\n")
        status, res = plan(branch="main")
        self.assertEqual(status, 409)
        self.assertIn("Stash and switch", res["error"])
        self.assertEqual(plan(branch="main", stash=True)[1]["steps"][0]["id"], "stash-custom")
        self.assertEqual(plan(branch="--upload-pack=x", stash=True)[0], 400)

    def test_upgrade_is_planned_from_its_check_and_asks_about_python_and_local_changes(self):
        from unittest import mock
        from bench_wizard import projects, upgrade
        from tests.test_projects import make_bench
        from tests.test_sites import git
        folder = make_bench(self.tmp / "home" / "b", projects.slot(0), sites=("a.localhost",))
        remote, app = self.tmp / "remote", Path(folder) / "apps" / "helpdesk"
        git(self.tmp, "init", "-q", "-b", "main", str(remote))
        git(remote, "commit", "-q", "--allow-empty", "-m", "one")
        git(remote, "branch", "develop")
        git(self.tmp, "clone", "-q", "--origin", "upstream", str(remote), str(app))
        canned = {"version": "version-16", "title": "Version 16", "errors": [], "notices": [], "new_apps": [], "node": "24",
                  "apps": [{"name": "helpdesk", "now": "main", "branch": "develop", "repo": str(remote), "why": "", "changed": False}],
                  "python": {"env": "3.11.9", "needs": ">=3.14", "use": "3.14", "ok": False}}
        plan = lambda **body: self.request("POST", "/api/plan", dict(body, folder=folder, op="upgrade", version="version-16"))
        self.assertEqual(plan()[0], 409)  # not checked yet
        with mock.patch.object(upgrade, "check", lambda *a, **k: dict(canned)):
            status, res = self.request("POST", "/api/bench/upgrade", {"folder": folder, "version": "version-16"})
        self.assertEqual((status, res["current"]), (200, "version-15"))
        status, res = plan()
        self.assertEqual(status, 400)
        self.assertIn("needs Python >=3.14", res["error"])
        (app / "f.txt").write_text("x")
        git(app, "add", "f.txt")  # staged, not committed
        self.assertEqual(plan(rebuild=True)[0], 409)
        status, p = plan(rebuild=True, stash=True)
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in p["steps"]], [
            "backup-a.localhost", "stash-helpdesk", "fetch-helpdesk", "switch-helpdesk", "env-aside", "env", "nvmrc",
            "requirements", "migrate", "build"])
        self.assertEqual(p["title"], "Upgrade to Version 16")
        self.assertTrue(any("python3.14" in c["label"] for c in p["checks"]))
        self.assertEqual(self.request("POST", "/api/plan", {"folder": folder, "op": "upgrade", "version": "version-15"})[0], 409)

    def test_upgrade_report_is_planned_per_app_and_read_back(self):
        from unittest import mock
        from bench_wizard import projects, upgrade
        from tests.test_projects import make_bench
        from tests.test_sites import git
        folder = make_bench(self.tmp / "home" / "b", projects.slot(0))
        app = Path(folder) / "apps" / "mine"
        git(self.tmp, "init", "-q", str(app))
        (app / "mine").mkdir()
        (app / "mine" / "hooks.py").write_text('app_name = "mine"\n')
        canned = {"version": "version-16", "title": "Version 16", "errors": [], "notices": [], "new_apps": [], "node": "24",
                  "apps": [{"name": "frappe", "now": "version-15", "branch": "version-16", "repo": "https://github.com/frappe/frappe.git", "why": "", "changed": False},
                           {"name": "mine", "now": "main", "branch": "main", "repo": "", "why": "", "changed": False}],
                  "python": {"env": "3.14.0", "needs": ">=3.14", "use": "3.14", "ok": True}}
        plan = lambda **body: self.request("POST", "/api/plan", dict(body, folder=folder, op="upgrade-report", version="version-16"))
        self.assertEqual(plan(apps=["mine"])[0], 409)
        with mock.patch.object(upgrade, "check", lambda *a, **k: dict(canned)):
            self.assertFalse(self.request("POST", "/api/bench/upgrade", {"folder": folder, "version": "version-16"})[1]["claude"])
        self.assertEqual(plan(apps=["mine"], claude=True)[0], 400)  # Claude Code is not on the bench's PATH
        self.assertEqual(plan(apps=["nope"])[0], 400)
        status, p = plan(apps=["mine"])
        self.assertEqual((status, [s["id"] for s in p["steps"]]), (200, ["tree-frappe", "scan-mine"]))
        self.assertIn("--rules v15-v16", p["steps"][1]["command"])
        report = lambda: self.request("GET", f"/api/bench/report?folder={folder}&version=version-16&app=mine")
        self.assertEqual(report()[0], 404)
        out = Path(upgrade.report_dir(self.app.state_root, folder, "version-16", "mine"))
        out.mkdir(parents=True)
        (out / "findings.json").write_text('[{"id": "X", "severity": "high"}]')
        self.assertEqual(report()[1]["counts"]["high"], 1)
        self.assertEqual(self.request("GET", f"/api/bench/report?folder={folder}&version=../x&app=mine")[0], 400)
        self.assertEqual(self.request("GET", "/api/bench/detail?folder=" + folder)[1]["reports"][0]["app"], "mine")

    def test_site_config_and_login_need_a_site_of_the_bench(self):
        from tests.test_projects import make_bench
        folder = make_bench(self.tmp / "home" / "b")
        (Path(folder) / "sites" / "a.localhost" / "site_config.json").write_text('{"db_name": "x", "db_password": "pw"}')
        status, c = self.request("GET", f"/api/site/config?folder={folder}&site=a.localhost")
        self.assertEqual(json.loads(c["text"]), {"db_name": "x", "db_password": "********"})
        self.assertEqual(self.request("POST", "/api/site/config", {"folder": folder, "site": "a.localhost", "text": "{"})[0], 400)
        self.assertEqual(self.request("GET", f"/api/site/config?folder={folder}&site=zz")[0], 400)

    def test_open_as_administrator_starts_a_stopped_bench_and_waits_for_it(self):
        from unittest import mock
        from bench_wizard import projects, sites
        from tests.test_projects import make_bench
        folder = make_bench(self.tmp / "home" / "b")
        running, tries, waits = {}, [], []
        self.app.start_bench = lambda f, env: running.setdefault(f, True) and None
        self.app.port_free = lambda port: not running or len(waits) < 2  # the web port answers a moment later

        def login_url(f, site, port, env=None):
            tries.append(site)
            if len(tries) < 3:
                raise sites.SiteError("could not sign in: redis.ConnectionError")  # Redis still starting
            return f"http://{site}:{port}/app?sid=x"
        with mock.patch.object(projects, "usage", lambda folders: {f: {} for f in folders if f in running}), \
                mock.patch.object(sites, "login_url", login_url), \
                mock.patch("bench_wizard.server.time.sleep", waits.append):
            status, res = self.request("POST", "/api/site/login", {"folder": folder, "site": "a.localhost"})
            self.assertEqual((status, res["started"], res["url"]), (200, True, "http://a.localhost:8000/app?sid=x"))
            self.assertEqual((list(running), len(tries)), ([folder], 3))
            status, res = self.request("POST", "/api/site/login", {"folder": folder, "site": "a.localhost"})
            self.assertEqual((status, res["started"]), (200, False))  # running now: signed in at once
        (Path(folder) / "sites" / "x.invalid").mkdir()
        (Path(folder) / "sites" / "x.invalid" / "site_config.json").write_text("{}")
        running.clear()
        status, res = self.request("POST", "/api/site/login", {"folder": folder, "site": "x.invalid"})
        self.assertEqual(status, 400)
        self.assertIn("/etc/hosts", res["error"])
        self.assertEqual(running, {})  # nothing was started for a site no browser can open

    def test_frappe_cloud_access_backups_and_download_plan(self):
        from unittest import mock
        from bench_wizard import frappecloud
        from tests.test_projects import make_bench
        folder = make_bench(self.tmp / "home" / "b")
        replies = {"request_access": {"token": "T", "approval_url": "https://cloud/a"}, "get_status": {"status": "Approved"},
                   "get_running_backup": None, "get_backups": [{"name": "b1", "created_at": "2026-11-17", "size": 5}],
                   "get_download_links": {"database": "https://s3/20261117-shop-database.sql.gz?sig=S"}, "revoke": None}
        urls = []

        def fake_call(method, token="", url=frappecloud.SITE, **kw):
            urls.append(url)
            return replies[method]
        with mock.patch.object(frappecloud, "call", fake_call):
            self.assertEqual(self.request("GET", "/api/cloud/site?domain=shop.frappe.cloud")[0], 400)  # no access yet
            status, c = self.request("POST", "/api/cloud/connect", {"domain": "shop.frappe.cloud", "url": "https://press.example.org"})
            self.assertEqual((status, c["status"], len(c["code"]), c["url"]), (200, "Pending", 8, "https://press.example.org"))
            self.assertEqual(os.stat(self.tmp / "state" / "frappe-cloud.json").st_mode & 0o777, 0o600)
            site = self.request("GET", "/api/cloud/site?domain=shop.frappe.cloud")[1]
            self.assertEqual((site["status"], site["backups"][0]["name"]), ("Approved", "b1"))
            status, p = self.request("POST", "/api/plan", {"op": "fc-download", "folder": folder, "domain": "shop.frappe.cloud", "backup": "b1"})
            self.assertEqual((status, [s["id"] for s in p["steps"]]), (200, ["fc-folder", "download-database"]))
            self.assertNotIn("sig=S", json.dumps(p))
            self.assertEqual(set(urls), {"https://press.example.org"})  # every later call goes to the site's own address
            self.request("POST", "/api/cloud/disconnect", {"domain": "shop.frappe.cloud"})
            self.assertEqual(self.request("GET", f"/api/bench/detail?folder={folder}")[1]["cloud"], [])

    def test_build_plan(self):
        a, _ = self.two_clashing_benches()
        status, plan = self.request("POST", "/api/plan", {"folder": a, "build": True})
        self.assertEqual((status, [s["command"] for s in plan["steps"]]), (200, ["bench build"]))
        self.assertTrue(plan["ok"])

    def test_new_bench_gets_a_port_slot_no_other_bench_uses(self):
        from bench_wizard import projects
        self.two_clashing_benches()
        status, plan = self.request("POST", "/api/plan", self.answers())
        self.assertEqual(plan["ports"], projects.slot(1))
        self.assertEqual([s["id"] for s in plan["steps"]][:4], ["init", "ports", "setup-redis", "procfile"])

    def test_low_memory_or_disk_shows_as_a_notice(self):
        self.warnings = ["Only 1.0 GB of memory is free."]
        self.assertIn("Only 1.0 GB of memory is free.", self.request("POST", "/api/plan", self.answers())[1]["notices"])

    def test_marketplace_list_and_app(self):
        from bench_wizard.marketplace import MarketplaceError

        class Market:
            calls = 0

            def apps(self):
                Market.calls += 1
                return [{"name": "tool", "title": "Tool"}]

            def app(self, name):
                if name != "tool":
                    raise MarketplaceError("no such app")
                return {"name": name, "repo": "https://github.com/acme/tool.git", "versions": ["version-15"],
                        "nightly": False, "free": True}

            def branches(self, repo, version, env=None):
                return [version, "main"]

        self.app.market = Market()
        self.assertEqual(self.request("GET", "/api/marketplace")[1]["apps"], [{"name": "tool", "title": "Tool"}])
        self.request("GET", "/api/marketplace")
        self.assertEqual(Market.calls, 1)  # fetched once a session
        data = self.request("POST", "/api/marketplace/app", {"name": "tool", "version": "version-15"})[1]
        self.assertEqual((data["ok"], data["branches"], data["free"]), (True, ["version-15", "main"], True))
        self.assertEqual(self.request("POST", "/api/marketplace/app", {"name": "nope"})[1],
                         {"ok": False, "error": "no such app"})

    # -- Frappe Cloud extension ----------------------------------------
    EXT = "chrome-extension://abcdefghijklmnop"

    def test_extension_calls_need_an_extension_origin_not_the_token(self):
        for origin in (None, "http://evil.example", f"http://127.0.0.1:{self.port}"):
            self.assertEqual(self.request("POST", "/api/extension/hello", {"version": "1"}, token=None,
                                          origin=origin)[0], 403, origin)
        self.assertEqual(self.request("POST", "/api/extension/hello", {}, token="tok")[0], 403)
        status, body = self.request("POST", "/api/extension/hello", {"version": "1.1.0"}, token=None, origin=self.EXT)
        self.assertEqual((status, body), (200, {"app": "bench-wizard"}))
        self.assertEqual(self.request("GET", "/api/extension", token=None)[0], 403)

    def test_extension_status(self):
        status = self.request("GET", "/api/extension")[1]
        self.assertFalse(status["connected"])
        self.assertTrue(status["folder"].endswith("extension"))
        self.request("POST", "/api/extension/hello", {"version": "1.1.0"}, token=None, origin="moz-extension://x")
        status = self.request("GET", "/api/extension")[1]
        self.assertEqual((status["connected"], status["version"], status["inbox"]), (True, "1.1.0", None))

    def test_extension_send_is_cleaned_and_delivered_once(self):
        apps = [{"app": "frappe", "url": "https://github.com/frappe/frappe", "branch": "version-15", "commit": "a1"},
                {"app": "acme", "url": "https://github.com/acme/acme", "branch": "main", "private": True,
                 "extra": "dropped", "tag": 5}]
        status, _ = self.request("POST", "/api/extension/send", {"site": "a.frappe.cloud", "apps": apps},
                                 token=None, origin=self.EXT)
        self.assertEqual(status, 200)
        got = self.request("GET", "/api/extension")[1]
        self.assertTrue(got["connected"])
        self.assertEqual(got["inbox"], {"site": "a.frappe.cloud", "apps": [
            apps[0], {"app": "acme", "url": "https://github.com/acme/acme", "branch": "main", "private": True}]})
        self.assertIsNone(self.request("GET", "/api/extension")[1]["inbox"])

    def test_extension_send_rejects_a_bad_list(self):
        for apps in (None, "x", [{"branch": "main"}], [{"url": 3}]):
            self.assertEqual(self.request("POST", "/api/extension/send", {"apps": apps}, token=None,
                                          origin=self.EXT)[0], 400, apps)

    def test_window_show_uses_the_app_mode_hook(self):
        self.assertEqual(self.request("POST", "/api/window/show", {})[1], {"shown": False})
        self.app.show_window = lambda: True
        self.assertEqual(self.request("POST", "/api/window/show", {})[1], {"shown": True})
        self.assertEqual(self.request("POST", "/api/window/show", {}, token=None)[0], 403)

    def test_quit_stops_the_wizard_unless_a_run_is_working(self):
        quits = []
        self.app.quit = lambda: quits.append(1)
        self.assertEqual(self.request("POST", "/api/quit", {}, token=None)[0], 403)
        self.app.runs = {"r": {"id": "r", "runner": types.SimpleNamespace(state="running")}}
        status, body = self.request("POST", "/api/quit", {})
        self.assertEqual(status, 409)
        self.assertIn("run is still working", body["error"])
        self.assertEqual(quits, [])
        self.app.runs["r"]["runner"].state = "done"
        self.assertEqual(self.request("POST", "/api/quit", {}), (200, {"ok": True}))
        self.assertEqual(quits, [1])

    def test_reveal_extension_folder(self):
        status, body = self.request("POST", "/api/extension/reveal", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.revealed, [server.EXTENSION_DIR])
        self.assertTrue(Path(server.EXTENSION_DIR, "manifest.json").is_file())
        self.app.reveal = lambda folder: False  # no file manager, e.g. WSL
        self.assertEqual(self.request("POST", "/api/extension/reveal", {})[1]["ok"], False)


class ListenTest(unittest.TestCase):
    def test_prefers_extension_ports_and_falls_back(self):
        servers = []
        try:
            for _ in range(len(server.EXTENSION_PORTS) + 1):
                servers.append(server.listen(None))
            ports = [s.server_address[1] for s in servers]
            self.assertEqual(len(set(ports)), len(ports))
            self.assertIn(ports[0], server.EXTENSION_PORTS)
            self.assertNotIn(ports[-1], server.EXTENSION_PORTS)  # every preferred port is taken by now
        finally:
            for s in servers:
                s.server_close()


class GithubSshHostServerTest(unittest.TestCase):
    def make(self, host):
        return server.App(catalog.load(ROOT / "catalog.json"), state_root="/nonexistent", token="tok",
                          inspect_app=self.record, github_ssh_host=host)

    def record(self, repo, branch, env=None):
        self.envs.append(env)
        return AppInfo("custom_app", "Custom App", "", "", {})

    def test_alias_reaches_git_and_catalog(self):
        self.envs = []
        app = self.make("github-work")
        self.assertEqual(app.get_catalog({}, {})["github_ssh_host"], "github-work")
        app.post_inspect_app({}, {"repo": "https://github.com/acme/app", "branch": "main"})
        self.assertIn("url.git@github-work:.insteadOf", self.envs[0].values())

    def test_without_alias_git_env_is_unchanged(self):
        self.envs = []
        self.make(None).post_inspect_app({}, {"repo": "https://github.com/acme/app", "branch": "main"})
        self.assertEqual(self.envs[0], {})

    def test_bad_alias_is_refused(self):
        for host in ("github work", "-oProxyCommand=x", "a:b"):
            with self.assertRaises(ValueError):
                self.make(host)
