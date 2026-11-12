"""Local HTTP API and static files for the wizard."""
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import backup as backup_mod
from . import catalog as catalog_mod
from . import checks as checks_mod
from concurrent.futures import ThreadPoolExecutor

from . import deps, desktop, existing as existing_mod, gitauth, planner, preflight as preflight_mod
from .runner import Runner

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_BODY = 1024 * 1024
EXTENSION_DIR = os.path.join(os.path.dirname(os.path.dirname(WEB_DIR)), "extension")
# The Frappe Cloud extension finds the wizard by trying these ports (extension/wizard.js), so they come first.
EXTENSION_PORTS = range(47615, 47620)
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://")
EXPORT_KEYS = {"app": str, "url": str, "branch": str, "commit": str, "tag": str, "private": bool}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, catalog, state_root, token=None, checks=checks_mod, resolve=deps.resolve,
                 preflight=preflight_mod.run, inspect_app=deps.inspect, inspect_backup=backup_mod.inspect,
                 reveal=desktop.reveal, github_ssh_host=None, inspect_bench=existing_mod.inspect):
        self.catalog = catalog
        self.state_root = state_root
        self.token = token or secrets.token_urlsafe(24)
        self.checks = checks
        self.resolve = resolve
        self.preflight = preflight
        self.inspect_app = inspect_app
        self.inspect_backup = inspect_backup
        self.inspect_bench_fn = inspect_bench
        self.reveal = reveal
        self.extension = None  # {"version"} once the Frappe Cloud extension has checked in
        self.inbox = None  # the last app list the extension sent, until the page picks it up
        self.show_window = None  # set in app mode: brings back the wizard's window, False if it has none
        self.git_creds = {}  # URL prefix -> "user:token"; memory only, for this session
        if github_ssh_host and not gitauth.SSH_HOST.fullmatch(github_ssh_host):
            raise ValueError(f"not an SSH host alias: {github_ssh_host!r}")
        self.github_ssh_host = github_ssh_host or None  # e.g. "github-work" from ~/.ssh/config
        self.pending = None
        self.runner = None
        self.run_id = None
        self.summary = None

    def _profile(self, version):
        profile = self.catalog["frappe_versions"].get(version)
        if profile is None:
            raise ApiError(400, f"unknown Frappe version: {version}")
        return profile

    # -- wizard data ---------------------------------------------------
    def get_catalog(self, query, body):
        return {
            "frappe_versions": self.catalog["frappe_versions"],
            "apps": self.catalog["apps"],
            "presets": self.catalog["presets"],
            "default_version": catalog_mod.default_version(self.catalog),
            "default_preset": catalog_mod.default_preset(self.catalog),
            "home": os.path.expanduser("~"),
            "wizard_dir": os.path.dirname(os.path.dirname(WEB_DIR)),
            "github_ssh_host": self.github_ssh_host,
        }

    def get_checks(self, query, body):
        return {"rows": self.checks.detect_all(self._profile(query.get("version", ""))),
                "mac": sys.platform == "darwin"}

    def post_install(self, query, body):
        profile = self._profile(body.get("version", ""))
        result = self.checks.install(body.get("id", ""), profile, body.get("sudo_password", ""))
        return dict(result, rows=self.checks.detect_all(profile))

    def post_mariadb_verify(self, query, body):
        return {"ok": self.checks.verify_mariadb_root(body.get("password", ""))}

    def post_mariadb_set(self, query, body):
        return self.checks.set_mariadb_root(body.get("sudo_password", ""), body.get("password", ""))

    def _clean(self, url):
        """Take any credentials out of a Git URL and keep them for this session."""
        clean, cred = gitauth.split(url)
        if cred:
            self.git_creds[cred["prefix"]] = cred["userinfo"]
        return clean

    def _git_env(self):
        return gitauth.env(self.git_creds, self.github_ssh_host)

    def _inspect(self, url, branch):
        repo = self._clean(url)
        try:
            info = self.inspect_app(repo, branch, env=self._git_env())
        except deps.InspectError as exc:
            return {"ok": False, "repo": repo, "branch": branch, "error": gitauth.scrub(str(exc))}
        return {"ok": True, "name": info.app_name, "title": info.app_title, "description": info.app_description,
                "requires": sorted(info.requires), "repo": repo, "branch": branch,
                "credentials": any(repo.startswith(prefix) for prefix in self.git_creds)}

    def post_inspect_app(self, query, body):
        result = self._inspect(body.get("repo", ""), body.get("branch", ""))
        if not result.pop("ok"):
            return {"ok": False, "error": result["error"]}
        return {"ok": True, "app": result}

    def post_import_apps(self, query, body):
        """Inspect a pasted list of {"url", "branch"}; results come back in the same order."""
        items = body.get("apps")
        if not isinstance(items, list) or not all(isinstance(i, dict) and i.get("url") for i in items):
            raise ApiError(400, 'expected a list of {"url": ..., "branch": ...}')
        default = body.get("version") or catalog_mod.default_version(self.catalog)
        pairs = [(str(i["url"]), str(i.get("branch") or default)) for i in items[:200]]
        for url, _ in pairs:
            self._clean(url)  # collect every credential before the parallel reads start
        with ThreadPoolExecutor(max_workers=8) as pool:
            return {"apps": list(pool.map(lambda pair: self._inspect(*pair), pairs))}

    def post_inspect_backup(self, query, body):
        try:
            info = self.inspect_backup(os.path.expanduser(body.get("path", "")))
        except backup_mod.BackupError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "apps": info.apps, "frappe_major": info.frappe_major}

    def _read_bench(self, folder):
        try:
            return self.inspect_bench_fn(folder, self.catalog, self.checks.build_env(""))
        except existing_mod.BenchError as exc:
            raise ApiError(400, str(exc))

    def post_inspect_bench(self, query, body):
        info = self._read_bench(body.get("folder", ""))
        return {"folder": info.folder, "version": info.version, "node": info.node, "apps": info.apps,
                "sites": info.sites}

    # -- Frappe Cloud extension ----------------------------------------
    # hello and send come from the extension, not the page: they need no token but must carry an extension
    # Origin, which web pages cannot set. send only fills the page's import box; nothing is cloned until
    # the user clicks "Add apps from list".
    def post_extension_hello(self, query, body):
        self.extension = {"version": str(body.get("version", ""))[:20]}
        return {"app": "bench-wizard"}

    def post_extension_send(self, query, body):
        items = body.get("apps")
        if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get("url"), str)
                                                  for i in items):
            raise ApiError(400, 'expected a list of {"url": ..., "branch": ...}')
        apps = [{k: i[k] for k, kind in EXPORT_KEYS.items() if isinstance(i.get(k), kind)} for i in items[:200]]
        self.inbox = {"site": str(body.get("site", ""))[:200], "apps": apps}
        if self.extension is None:
            self.extension = {"version": ""}
        return {"ok": True}

    def get_extension(self, query, body):
        inbox, self.inbox = self.inbox, None
        return {"connected": self.extension is not None,
                "version": (self.extension or {}).get("version", ""), "folder": EXTENSION_DIR, "inbox": inbox}

    def post_extension_reveal(self, query, body):
        return {"ok": self.reveal(EXTENSION_DIR) is not False, "folder": EXTENSION_DIR}

    # -- window --------------------------------------------------------
    def post_window_show(self, query, body):
        """Opening the app again asks this first; it opens a new window only when this says False."""
        return {"shown": bool(self.show_window and self.show_window())}

    # -- plan and run --------------------------------------------------
    def post_plan(self, query, body):
        if body.get("existing"):
            return self._plan_existing(body)
        version = body.get("version", "")
        profile = self._profile(version)
        folder = body.get("folder", "")
        if folder:
            folder = os.path.abspath(os.path.expanduser(folder))
        selected = {a["name"]: {"repo": self._clean(a["repo"]), "branch": a["branch"],
                                "title": a.get("title") or a["name"], "added_for": None}
                    for a in body.get("apps", []) if a.get("name") != "frappe"}
        git_env = self._git_env()
        resolution = self.resolve(selected, self.catalog, version,
                                  inspect_fn=lambda repo, branch: self.inspect_app(repo, branch, env=git_env))
        restore, backup_info = self._restore(body.get("restore"))
        answers = dict(self._new_site(body, restore), version=version, folder=folder,
                       apps=[dict(resolution.apps[name], name=name) for name in resolution.order],
                       node=resolution.node or profile["node"])
        steps = self._steps(planner.plan, answers, self.catalog)
        env = dict(self.checks.build_env(answers["node"]), **git_env)
        rows = self.preflight(answers, resolution, backup_info, self.catalog, env,
                              probe=preflight_mod.Probe(git_env))
        return self._hold(steps, env, rows, self._summary(answers, profile, resolution),
                          planner.services(answers), resolution.notices, backup_info)

    def _restore(self, restore):
        """The backup paths made absolute, plus what the backup itself says about its apps."""
        if not restore:
            return None, None
        restore = dict(restore)
        for key in ("sql", "public", "private"):
            if restore.get(key):
                restore[key] = os.path.abspath(os.path.expanduser(restore[key]))
        try:
            backup_info = self.inspect_backup(restore.get("sql", ""))
        except backup_mod.BackupError:
            backup_info = None
        restore["backup_apps"] = list(backup_info.apps) if backup_info else []
        restore["partial"] = bool(backup_info and backup_info.partial)
        return restore, backup_info

    @staticmethod
    def _new_site(body, restore):
        return {"site": body.get("site", "").strip(), "db_name": body.get("db_name", "").strip(),
                "admin_password": body.get("admin_password", ""),
                "mariadb_root_password": body.get("mariadb_root_password", ""), "restore": restore}

    def _steps(self, plan_fn, *args):
        try:
            return plan_fn(*args)
        except planner.PlanError as exc:
            raise ApiError(400, str(exc))

    @staticmethod
    def _summary(answers, profile, resolution, local=()):
        """What the Review and Done screens need; holds no passwords or keys."""
        apps = [{"name": n, "title": n, "branch": "already in the bench", "repo": "", "added_for": None}
                for n in local]
        apps += [{"name": n, "title": resolution.apps[n]["title"], "branch": resolution.apps[n]["branch"],
                  "repo": resolution.apps[n]["repo"], "added_for": resolution.apps[n].get("added_for")}
                 for n in resolution.order]
        restore = answers.get("restore")
        return {"folder": answers["folder"], "site": answers["site"], "version": answers["version"],
                "version_title": profile["title"], "python": profile["python"], "node": answers["node"],
                "apps": apps, "existing": bool(answers.get("existing")),
                "restore": {k: bool(restore.get(k)) for k in ("mute_emails", "pause_scheduler", "sanitize")}
                if restore else None}

    def _hold(self, steps, env, rows, summary, services, notices, backup_info=None):
        """Keep the plan for /api/run and describe it to the page."""
        ok = all(row["ok"] for row in rows)
        self.pending = {"steps": steps, "env": env, "ok": ok, "summary": summary, "services": services}
        backup = {"apps": backup_info.apps, "frappe_major": backup_info.frappe_major} if backup_info else None
        return dict(summary, ok=ok, checks=rows, notices=notices, backup=backup,
                    steps=[{"id": s.id, "title": s.title, "command": s.display()} for s in steps])

    def _plan_existing(self, body):
        """Add apps to a site of an existing bench, or with `restore` add a new site from a backup.

        The bench is read again here, not trusted from the page.
        """
        bench = self._read_bench(body.get("folder", ""))
        site = body.get("site", "").strip()
        restore, backup_info = self._restore(body.get("restore"))
        if restore:
            if site and (site in bench.sites or os.path.lexists(os.path.join(bench.folder, "sites", site))):
                raise ApiError(400, f"{site} already exists in this bench; pick a new site name")
            installed = []
        else:
            installed = bench.sites.get(site, {}).get("apps")
            if installed is None:
                raise ApiError(400, f"cannot read the apps installed on {site}: "
                                    f"{bench.sites.get(site, {}).get('error', 'no such site in this bench')}")
        version, profile = bench.version, self._profile(bench.version)
        wanted = [a for a in body.get("apps", []) if a.get("name") not in installed and a.get("name") != "frappe"]
        local = [a["name"] for a in wanted if a["name"] in bench.apps]
        selected = {a["name"]: {"repo": self._clean(a["repo"]), "branch": a["branch"],
                                "title": a.get("title") or a["name"], "added_for": None}
                    for a in wanted if a["name"] not in bench.apps}
        git_env = self._git_env()
        resolution = self.resolve(selected, self.catalog, version, present=set(bench.apps),
                                  inspect_fn=lambda repo, branch: self.inspect_app(repo, branch, env=git_env))
        node = bench.node or existing_mod.nvm_default_node() or profile["node"]
        if int(resolution.node or 0) > int(node):
            node = resolution.node
        answers = {"existing": True, "version": version, "folder": bench.folder, "site": site, "node": node,
                   "write_nvmrc": node != bench.node,  # also when there was none, so `nvm use` works after
                   "apps": [{"name": n, "on_bench": True} for n in local]
                           + [dict(resolution.apps[n], name=n) for n in resolution.order]}
        if restore:
            answers.update(self._new_site(body, restore), site=site)
            steps = self._steps(planner.plan, answers, self.catalog)
            services = planner.services(answers)
        else:
            steps = self._steps(planner.plan_existing, answers)
            services = planner.services(answers, before=next((s.id for s in steps if s.id.startswith("install-")), ""))
        env = dict(self.checks.build_env(node), **git_env)
        rows = self.preflight(answers, resolution, backup_info, self.catalog, env,
                              probe=preflight_mod.Probe(git_env))
        return self._hold(steps, env, rows, self._summary(answers, profile, resolution, local),
                          services, resolution.notices, backup_info)

    def _active(self):
        return self.runner is not None and self.runner.state == "running"

    def post_run(self, query, body):
        if self.pending is None:
            raise ApiError(409, "nothing has been planned yet")
        if not self.pending["ok"]:
            raise ApiError(409, "pre-flight checks have not passed")
        if self._active():
            raise ApiError(409, "a run is already in progress")
        self.run_id = time.strftime("%Y-%m-%d-%H%M%S")
        self.runner = Runner(self.pending["steps"], os.path.join(self.state_root, self.run_id),
                             self.pending["env"], self.pending["services"])
        self.summary = self.pending["summary"]
        self.pending = None
        self.runner.start()
        return {"ok": True, "run_id": self.run_id}

    def _need_runner(self):
        if self.runner is None:
            raise ApiError(409, "no run has been started")
        return self.runner

    def get_run(self, query, body):
        if self.runner is None:
            return {"state": "none", "steps": []}
        return dict(self.runner.snapshot(), run_id=self.run_id, log_dir=self.runner.log_dir,
                    summary=self.summary)

    def get_log(self, query, body):
        runner = self._need_runner()
        step = query.get("step", "")
        if step not in {s.id for s in runner.steps}:
            raise ApiError(404, "unknown step")
        return {"log": runner.read_log(step)}

    def post_retry(self, query, body):
        try:
            self._need_runner().retry(bool(body.get("cleanup")))
        except RuntimeError as exc:
            raise ApiError(409, str(exc))
        return {"ok": True}

    def post_dismiss(self, query, body):
        if self._active():
            raise ApiError(409, "a run is in progress")
        self.runner = self.run_id = self.summary = None
        return {"ok": True}

    def post_cancel(self, query, body):
        self._need_runner().cancel()
        return {"ok": True}

    ROUTES = {
        ("GET", "/api/catalog"): get_catalog,
        ("GET", "/api/checks"): get_checks,
        ("POST", "/api/checks/install"): post_install,
        ("POST", "/api/mariadb/verify"): post_mariadb_verify,
        ("POST", "/api/mariadb/set-password"): post_mariadb_set,
        ("POST", "/api/apps/inspect"): post_inspect_app,
        ("POST", "/api/apps/import"): post_import_apps,
        ("POST", "/api/backup/inspect"): post_inspect_backup,
        ("POST", "/api/bench/inspect"): post_inspect_bench,
        ("POST", "/api/extension/hello"): post_extension_hello,
        ("POST", "/api/extension/send"): post_extension_send,
        ("GET", "/api/extension"): get_extension,
        ("POST", "/api/extension/reveal"): post_extension_reveal,
        ("POST", "/api/window/show"): post_window_show,
        ("POST", "/api/plan"): post_plan,
        ("POST", "/api/run"): post_run,
        ("GET", "/api/run"): get_run,
        ("GET", "/api/run/log"): get_log,
        ("POST", "/api/run/retry"): post_retry,
        ("POST", "/api/run/cancel"): post_cancel,
        ("POST", "/api/run/dismiss"): post_dismiss,
    }
    EXTENSION_ROUTES = {("POST", "/api/extension/hello"), ("POST", "/api/extension/send")}


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "BenchWizard"

    def log_message(self, format, *args):
        pass

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, token):
        return bool(token) and hmac.compare_digest(token.encode(), self.app.token.encode())

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not url.path.startswith("/api/"):
            return self._static(url.path) if method == "GET" else self._send(404, {"error": "not found"})
        if url.path == "/api/run/events" and method == "GET":
            if not self._token_ok(query.get("token", "")):
                return self._send(403, {"error": "forbidden"})
            return self._events()
        if (method, url.path) in App.EXTENSION_ROUTES:
            if not self.headers.get("Origin", "").startswith(EXTENSION_ORIGINS):
                return self._send(403, {"error": "forbidden"})
        elif not self._token_ok(self.headers.get("X-Token", "")):
            return self._send(403, {"error": "forbidden"})
        route = App.ROUTES.get((method, url.path))
        if route is None:
            return self._send(404, {"error": "not found"})
        body = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
        try:
            self._send(200, route(self.app, query, body))
        except ApiError as exc:
            self._send(exc.status, {"error": str(exc)})
        except (KeyError, TypeError, AttributeError) as exc:
            self._send(400, {"error": f"malformed request: {exc}"})

    def _static(self, path):
        name = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.join(WEB_DIR, name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith(".") or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        with open(full, "rb") as fh:
            self._send(200, fh.read(), mimetypes.guess_type(name)[0] or "application/octet-stream")

    def _events(self):
        runner = self.app.runner
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = runner.subscribe() if runner else queue.Queue()
        try:
            first = dict(self.app.get_run({}, {}), type="snapshot")
            self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if runner:
                runner.unsubscribe(q)


def listen(app, port=0):
    """A server on `port`, or when that is 0 on the first free extension port, else any free port."""
    if port:
        return make_server(app, port)
    for candidate in EXTENSION_PORTS:
        try:
            return make_server(app, candidate)
        except OSError:
            pass
    return make_server(app, 0)


def make_server(app, port=0):
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    return httpd
