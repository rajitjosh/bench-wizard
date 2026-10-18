import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from bench_wizard import catalog, server
from bench_wizard.backup import BackupError, BackupInfo
from bench_wizard.deps import AppInfo, InspectError, Resolution

ROOT = Path(__file__).resolve().parent.parent
ROWS = [{"id": "python", "title": "Python", "note": "", "required": "3.11", "status": "ok", "found": "3.11.9"}]


class FakeChecks:
    def __init__(self):
        self.installed = []
        self.root_password = "rootpw"

    def detect_all(self, profile):
        return [dict(r, required=profile["python"]) for r in ROWS]

    def install(self, check_id, profile, sudo_password):
        self.installed.append((check_id, profile["python"], sudo_password))
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
            inspect_app=fake_inspect_app, inspect_backup=fake_inspect_backup, reveal=self.revealed.append)
        self.httpd = server.make_server(self.app, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

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
        self.assertIn("https://github.com/acme/private-app.git", data["steps"][2]["command"])
        self.assertIn("ghp_exampletoken123", self.app.pending["env"]["GIT_CONFIG_KEY_0"])
        self.assertNotIn("ghp_", " ".join(self.app.pending["steps"][2].argv))

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
        commands = [s["command"] for s in data["steps"]]
        self.assertTrue(commands[0].startswith("bench init"))
        self.assertIn("••••", commands[3])
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
