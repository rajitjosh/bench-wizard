"""Local HTTP API and static files for the wizard."""
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import backup as backup_mod
from . import catalog as catalog_mod
from . import checks as checks_mod
from concurrent.futures import ThreadPoolExecutor

from . import deps, desktop, existing as existing_mod, gitauth, planner, preflight as preflight_mod, projects
from . import frappecloud, marketplace as market_mod, sites as sites_mod, upgrade as upgrade_mod
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
                 reveal=desktop.reveal, github_ssh_host=None, inspect_bench=existing_mod.inspect, home=None,
                 market=market_mod, port_free=projects.port_free, resources=projects.resources,
                 start_bench=projects.start, stop_bench=projects.stop):
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
        self.home = home or os.path.expanduser("~")  # benches are looked for under it
        self.market = market
        self.market_apps = None  # the marketplace's app list, fetched once a session
        self.port_free = port_free
        self.resources = resources
        self.start_bench = start_bench
        self.stop_bench = stop_bench
        self.extension = None  # {"version"} once the Frappe Cloud extension has checked in
        self.inbox = None  # the last app list the extension sent, until the page picks it up
        self.show_window = None  # set in app mode: brings back the wizard's window, False if it has none
        self.quit = None  # set by __main__: stops the wizard (and closes its window)
        self.git_creds = {}  # URL prefix -> "user:token"; memory only, for this session
        if github_ssh_host and not gitauth.SSH_HOST.fullmatch(github_ssh_host):
            raise ValueError(f"not an SSH host alias: {github_ssh_host!r}")
        self.github_ssh_host = github_ssh_host or None  # e.g. "github-work" from ~/.ssh/config
        self.pending = None
        self.upgrades = {}  # bench folder -> its last upgrade check, which an upgrade plan is built from
        self.runs = {}  # run id -> {"id", "runner", "summary"}, oldest first
        self.run_id = None  # the run started last: the one a request that names none is about

    def _learn(self, folder, version):
        """The bench's version profile; one the catalog does not list (13, 14, a newer one) is read from the bench
        and kept, so every step after this (apps, checks, plans) can work in it."""
        profile = self.catalog["frappe_versions"].get(version)
        if profile is None or profile.get("existing_only"):
            profile = self.catalog["frappe_versions"][version] = existing_mod.own_profile(folder, version)
        return profile

    def _supported(self):
        return ", ".join(v["title"] for v in self.catalog["frappe_versions"].values() if not v.get("existing_only"))

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
        node = str(body.get("node") or "")
        if node:  # from Review: the Node an app needs, which may not be the Frappe version's own
            if not node.isdigit() or body.get("id") not in ("node", "yarn"):
                raise ApiError(400, "only Node and Yarn can be installed for a given Node version, such as 20")
            # any version's profile will do: only its Node is used by these two installs
            profile = dict(next(iter(self.catalog["frappe_versions"].values())), node=node)
            return self.checks.install(body["id"], profile, "")
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
            bench = self.inspect_bench_fn(folder, self.catalog, self.checks.build_env(""))
        except existing_mod.BenchError as exc:
            raise ApiError(400, str(exc))
        self._learn(bench.folder, bench.version)
        return bench

    def post_inspect_bench(self, query, body):
        info = self._read_bench(body.get("folder", ""))
        return {"folder": info.folder, "version": info.version, "node": info.node, "apps": info.apps,
                "sites": info.sites, "profile": self._profile(info.version)}

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

    def post_quit(self, query, body):
        """The Quit button. Benches started from the wizard keep running; a run in progress would not."""
        if self.active_runs():
            raise ApiError(409, "a run is still working; wait for it to finish or cancel it, then quit")
        if self.quit:
            self.quit()
        return {"ok": True}

    # -- plan and run --------------------------------------------------
    def post_plan(self, query, body):
        if body.get("op"):
            return self._plan_op(body)
        if body.get("ports"):
            return self._plan_ports(body)
        if body.get("build"):
            return self._plan_build(body)
        if body.get("migrate"):
            return self._plan_migrate(body)
        if body.get("existing"):
            return self._plan_existing(body)
        version = body.get("version", "")
        profile = self._profile(version)
        if profile.get("existing_only"):
            raise ApiError(400, f"new benches are set up on {self._supported()} only; a bench on {profile['title']} "
                                "can be used as an existing bench")
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
                       node=resolution.node or profile["node"], ports=self._new_bench_ports(folder))
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
                "apps": apps, "existing": bool(answers.get("existing")), "ports": answers.get("ports"),
                "restore": {k: bool(restore.get(k)) for k in ("mute_emails", "pause_scheduler", "sanitize")}
                if restore else None}

    def _hold(self, steps, env, rows, summary, services, notices, backup_info=None, parallel=False):
        """Keep the plan for /api/run and describe it to the page."""
        ok = all(row["ok"] for row in rows)
        notices = list(notices) + self.resources(self.home)["warnings"]
        self.pending = {"steps": steps, "env": env, "ok": ok, "summary": summary, "services": services,
                        "parallel": parallel}
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
                   "ports": projects.read_ports(bench.folder),  # only shown: the site's URL on the Done screen
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

    def _plan_migrate(self, body):
        """`bench migrate` on several sites of an existing bench, side by side."""
        bench = self._read_bench(body.get("folder", ""))
        sites = body.get("sites")
        if not isinstance(sites, list) or not sites:
            raise ApiError(400, "pick at least one site to migrate")
        sites = list(dict.fromkeys(map(str, sites)))
        unknown = [s for s in sites if s not in bench.sites]
        if unknown:
            raise ApiError(400, f"not a site of this bench: {', '.join(unknown)}")
        profile = self._profile(bench.version)
        node = bench.node or existing_mod.nvm_default_node() or profile["node"]
        answers = {"folder": bench.folder, "sites": sites, "backup": bool(body.get("backup"))}
        steps = self._steps(planner.plan_migrate, answers)
        summary = dict(self._bench_summary(bench.folder, bench.version, node), site=", ".join(sites), migrate=sites,
                       title=f"Migrate {sites[0] if len(sites) == 1 else f'{len(sites)} sites'}")
        rows = [{"ok": True, "label": f"{len(sites)} site{'s' * (len(sites) != 1)} found in the bench"}]
        return self._hold(steps, self.checks.build_env(node), rows, summary,
                          planner.services(answers, before=steps[0].id), [], parallel=True)

    def _bench_summary(self, folder, version, node):
        profile = self._profile(version)
        return {"folder": folder, "site": "", "version": version, "version_title": profile["title"],
                "python": profile["python"], "node": node, "apps": [], "existing": True, "restore": None}

    def _light_bench(self, folder):
        """A bench read from its files only, for steps that need no site details."""
        folder = os.path.abspath(os.path.expanduser(str(folder or "")))
        if not projects.is_bench(folder):
            raise ApiError(400, f"{folder} is not a bench: apps/frappe or sites is missing")
        info = projects.bench_info(folder)
        profile = self._learn(folder, info["version"])
        node = existing_mod.nvmrc_node(folder) or existing_mod.nvm_default_node() or profile["node"]
        return info, node

    def _plan_build(self, body):
        info, node = self._light_bench(body.get("folder"))
        summary = dict(self._bench_summary(info["folder"], info["version"], node), title="Build assets")
        return self._hold(planner.plan_build(info["folder"]), self.checks.build_env(node), [], summary, [], [])

    def _plan_ports(self, body):
        info, node = self._light_bench(body.get("folder"))
        ports = body["ports"]
        if not isinstance(ports, dict):
            raise ApiError(400, "ports must be an object")
        steps = self._steps(planner.plan_ports, info["folder"], ports)
        summary = dict(self._bench_summary(info["folder"], info["version"], node), ports=ports,
                       title=f"Move to port {ports['webserver_port']}")
        return self._hold(steps, self.checks.build_env(node), [], summary, [], [])

    # -- projects ------------------------------------------------------
    def _list(self, name):
        """A list of folders kept in the state folder: benches.json (benches added by hand or created here)
        or folders.json (folders searched for benches besides the home folder)."""
        try:
            with open(os.path.join(self.state_root, name)) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        return [f for f in data if isinstance(f, str)] if isinstance(data, list) else []

    def _save_list(self, name, folders):
        os.makedirs(self.state_root, exist_ok=True)
        with open(os.path.join(self.state_root, name), "w") as fh:
            json.dump(folders, fh, indent=1)

    def _known(self):
        return self._list("benches.json")

    def _remember(self, folder):
        known = self._known()
        if folder not in known:
            self._save_list("benches.json", known + [folder])

    def _benches(self):
        found = [f for root in [self.home] + self._list("folders.json") for f in projects.discover(root)]
        scanned = set(found)
        found += [f for f in self._known() if projects.is_bench(f)]
        return [dict(projects.bench_info(f), added=f not in scanned) for f in dict.fromkeys(found)]

    def _planned_ports(self, benches):
        """(folder, ports) of new benches still being set up by a run, so no other bench gets their ports."""
        on_disk = {b["folder"] for b in benches}
        return [(run["summary"]["folder"], run["summary"]["ports"]) for run in self.active_runs()
                if run["summary"].get("ports") and run["summary"]["folder"] not in on_disk]

    def _new_bench_ports(self, folder):
        """The first port slot no bench uses now, nor a new bench another run is setting up."""
        benches = self._benches()
        taken = self._planned_ports(benches) + [(b["folder"], b["ports"]) for b in benches if b["folder"] != folder]
        return projects.free_slot({port for _, ports in taken for port in ports.values()}, self.port_free)

    def get_projects(self, query, body):
        benches = self._benches()
        used = projects.usage([b["folder"] for b in benches])
        shared = projects.clashes({b["folder"]: b["ports"] for b in benches})
        for bench in benches:
            profile = self.catalog["frappe_versions"].get(bench["version"])
            bench.update(version_title=(profile or {}).get("title") or existing_mod.version_title(bench["version"]), clashes=shared[bench["folder"]],
                         running=None if used is None else bench["folder"] in used,
                         rss=(used or {}).get(bench["folder"], {}).get("rss"))
        return {"benches": benches, "resources": self.resources(self.home), "home": self.home,
                "folders": self._list("folders.json"),
                "running": None if used is None else len(used)}

    def post_projects_add(self, query, body):
        """A bench folder joins the list; any other folder is searched for benches from now on."""
        folder = str(body.get("folder") or "").strip()
        if not folder:  # "" would mean the folder the wizard was started in
            raise ApiError(400, "enter a folder, or pick one with Browse…")
        folder = os.path.abspath(os.path.expanduser(folder))
        if projects.is_bench(folder):
            self._remember(folder)
            return {"ok": True, "folder": folder, "kind": "bench"}
        if not os.path.isdir(folder):
            raise ApiError(400, f"{folder} is not a folder on this machine")
        if folder == os.path.dirname(folder):
            raise ApiError(400, "searching the whole disk would take too long; add the folder that holds your benches")
        folders = self._list("folders.json")
        if folder != self.home and folder not in folders:
            self._save_list("folders.json", folders + [folder])
        return {"ok": True, "folder": folder, "kind": "folder"}

    def get_folders(self, query, body):
        """The folders in one folder, benches marked, for browsing to a folder on the Projects screen."""
        path = os.path.abspath(os.path.expanduser(query.get("path") or self.home))

        def is_dir(entry):
            try:
                return entry.is_dir()
            except OSError:
                return False
        try:
            entries = sorted((e for e in os.scandir(path) if not e.name.startswith(".") and is_dir(e)),
                             key=lambda e: e.name.lower())
        except OSError as exc:
            raise ApiError(400, f"cannot open {path}: {exc.strerror}")
        parent = os.path.dirname(path)
        return {"path": path, "parent": parent if parent != path else None, "bench": projects.is_bench(path),
                "folders": [{"name": e.name, "path": e.path, "bench": projects.is_bench(e.path)} for e in entries[:500]],
                "more": max(0, len(entries) - 500)}

    def post_projects_remove(self, query, body):
        """Forget a searched folder or a bench added by hand; nothing on disk is touched."""
        folder = str(body.get("folder", ""))
        for name in ("folders.json", "benches.json"):
            kept = self._list(name)
            if folder in kept:
                self._save_list(name, [f for f in kept if f != folder])
        return {"ok": True}

    def post_ports_allocate(self, query, body):
        """New ports for every bench that shares one with another; running benches keep theirs."""
        benches = self._benches()
        used = projects.usage([b["folder"] for b in benches]) or {}
        order = sorted(benches, key=lambda b: b["folder"] not in used)  # stable: running first, then as found
        taken = self._planned_ports(benches) + [(b["folder"], b["ports"]) for b in order]
        return {"moves": projects.allocate(taken, self.port_free)}

    def post_bench_start(self, query, body):
        info, node = self._light_bench(body.get("folder"))
        folder = info["folder"]
        if folder in (projects.usage([folder]) or {}):
            raise ApiError(409, "this bench is running already")
        busy = sorted(p for p in set(info["ports"].values()) if not self.port_free(p))
        if busy:
            raise ApiError(409, f"{'port' if len(busy) == 1 else 'ports'} {', '.join(map(str, busy))} {'is' if len(busy) == 1 else 'are'} in use, most "
                                "likely by another bench that is running. Stop that bench, or give this one ports "
                                "of its own: Auto-allocate ports, then Apply.")
        try:
            failed = self.start_bench(folder, self.checks.build_env(node))
        except OSError as exc:
            raise ApiError(400, f"cannot run bench start: {exc}") from exc
        if failed is not None:
            raise ApiError(409, f"bench start stopped right away. Its last lines ({projects.START_LOG}):\n{failed}")
        return {"ok": True, "port": info["ports"]["webserver_port"], "log": os.path.join(folder, projects.START_LOG)}

    def post_bench_stop(self, query, body):
        info, _ = self._light_bench(body.get("folder"))
        try:
            stopped = self.stop_bench(info["folder"])
        except PermissionError as exc:
            raise ApiError(403, "this bench runs as another user (a production bench under supervisor?); "
                                "stop it the way it was started") from exc
        if stopped is None:
            raise ApiError(400, "this system cannot tell which processes belong to the bench (no /proc, ps or lsof); press Ctrl+C where it runs")
        if not stopped:
            raise ApiError(409, "nothing of this bench is running")
        return {"ok": True}

    # -- one bench up close ----------------------------------------------
    def _site_of(self, info, site):
        if site not in info["sites"]:
            raise ApiError(400, f"{site or 'that'} is not a site of this bench")
        return site

    def _app_of(self, info, app):
        if not app or not os.path.isdir(os.path.join(info["folder"], "apps", str(app), ".git")):
            raise ApiError(400, f"{app or 'that'} is not an app of this bench")
        return app

    def get_bench_detail(self, query, body):
        info, node = self._light_bench(query.get("folder"))
        folder = info["folder"]
        used = projects.usage([folder])
        return dict(info, version_title=self._profile(info["version"])["title"], node=node,
                    running=None if used is None else folder in used, apps=sites_mod.apps(folder),
                    backups=sites_mod.backups(folder), logs=sites_mod.logs(folder),
                    reports=upgrade_mod.reports(self.state_root, folder),
                    cloud=sorted(self._cloud()))

    def post_bench_updates(self, query, body):
        """Fetch each app's remote branch, so the page can say how many commits each is behind."""
        info, _ = self._light_bench(body.get("folder"))
        return {"apps": sites_mod.apps(info["folder"], fetch=True)}

    def post_bench_branches(self, query, body):
        info, _ = self._light_bench(body.get("folder"))
        app = next((a for a in sites_mod.apps(info["folder"]) if a["name"] == self._app_of(info, body.get("app"))), None)
        local = sites_mod.local_branches(info["folder"], app["name"])
        remote, error = [], ""
        if app["remote"]:
            try:
                remote = market_mod.branches(app["remote"], app["branch"], env=self._git_env())
            except market_mod.MarketplaceError as exc:  # offline, say: its local branches can still be switched to
                error = str(exc)
        if not remote and not local:
            raise ApiError(400, error or f"{app['name']} has no branches to switch to")
        names = remote + [b for b in local if b not in remote]
        names.sort(key=lambda b: b != app["branch"])  # the current one first, as the pick that changes nothing
        return {"branches": names, "local_only": [b for b in local if b not in remote], "error": error}

    def post_bench_upgrade(self, query, body):
        """Which branch each app of the bench would move to for another Frappe version, and what else must change."""
        info, node = self._light_bench(body.get("folder"))
        version = str(body.get("version", ""))
        self._profile(version)
        git_env = self._git_env()
        seen = {}  # each branch is read once: when picked, and again when its requirements are followed

        def inspect(repo, branch):
            if (repo, branch) not in seen:
                seen[repo, branch] = self.inspect_app(repo, branch, env=git_env)
            return seen[repo, branch]
        try:
            result = upgrade_mod.check(info["folder"], version, self.catalog, inspect,
                                       lambda repo: market_mod.branches(repo, "", env=git_env), self.resolve)
        except ValueError as exc:
            raise ApiError(400, str(exc))
        result.update(current=info["version"], bench_node=node, claude=bool(self._claude(node)))
        self.upgrades[info["folder"]] = result
        return result

    def _claude(self, node):
        """Claude Code's path, where a bench command would find it, or None."""
        return shutil.which("claude", path=self.checks.build_env(node).get("PATH"))

    def _plan_report(self, info, node, body):
        """Scan custom apps for what they must change on the version the last upgrade check was for."""
        folder = info["folder"]
        check = self.upgrades.get(folder)
        if not check or check["version"] != body.get("version"):
            raise ApiError(409, "check the upgrade again first")
        names = body.get("apps")
        if not isinstance(names, list) or not names:
            raise ApiError(400, "pick at least one app to report on")
        claude = self._claude(node) if body.get("claude") else None
        if body.get("claude") and not claude:
            raise ApiError(400, "Claude Code was not found. Install it, run `claude` once in a terminal to sign in, then try again")
        trees, scans = {}, []
        for app in dict.fromkeys(map(str, names)):
            self._app_of(info, app)
            cores = upgrade_mod.core_apps(folder, app, check["apps"], self.catalog)
            for r in cores:
                trees[r["name"]] = (r["name"], r["repo"], r["branch"],
                                    os.path.join(upgrade_mod.trees_root(), f"{r['name']}-{r['branch']}"))
            sources = [(r["name"], os.path.join(folder, "apps", r["name"])) for r in cores]
            targets = [(r["name"], trees[r["name"]][3]) for r in cores]
            out = upgrade_mod.report_dir(self.state_root, folder, check["version"], app)
            scans.append({"app": app, "out": out, "rules": upgrade_mod.rules_for(info["version"], check["version"]),
                          "sources": sources, "targets": targets,
                          "prompt": upgrade_mod.claude_prompt(app, check["title"], sources, targets, out)})
        steps = self._steps(planner.plan_upgrade_report, folder, list(trees.values()), scans, upgrade_mod.SCANNER,
                            claude, upgrade_mod.PLUGIN)
        rows = [{"ok": True, "label": f"{len(scans)} app{'s' * (len(scans) != 1)} compared with {check['title']}"}]
        if claude:
            rows.append({"ok": True, "label": f"Claude Code at {claude} reviews the findings with read-only tools"})
        summary = dict(self._bench_summary(folder, info["version"], node), op="upgrade-report",
                       title=f"What {scans[0]['app'] if len(scans) == 1 else f'{len(scans)} apps'} must change for {check['title']}")
        return self._hold(steps, dict(self.checks.build_env(node), **self._git_env()), rows, summary, [], [])

    def get_bench_report(self, query, body):
        info, _ = self._light_bench(query.get("folder"))
        version, app = query.get("version", ""), self._app_of(info, query.get("app"))
        self._profile(version)
        report = upgrade_mod.read_report(upgrade_mod.report_dir(self.state_root, info["folder"], version, app))
        if report is None:
            raise ApiError(404, f"no report for {app} on {version} yet")
        return dict(report, app=app, version=version)

    def _plan_upgrade(self, info, body):
        folder = info["folder"]
        check = self.upgrades.get(folder)
        if not check or check["version"] != body.get("version"):
            raise ApiError(409, "check the upgrade again first")
        if check["errors"]:
            raise ApiError(400, "; ".join(check["errors"]))
        python, rebuild = check["python"], bool(body.get("rebuild"))
        if not python["ok"] and not rebuild:
            raise ApiError(400, f"{check['title']} needs Python {python['needs']} and this bench has {python['env'] or 'none'}; "
                                "choose a new Python environment for it, or a new bench")
        state = {a["name"]: a for a in sites_mod.apps(folder)}
        moving = [r for r in check["apps"] if r["branch"] != state[r["name"]]["branch"]]
        dirty = [r["name"] for r in moving if state[r["name"]]["changed"]]
        if dirty and not body.get("stash"):
            raise ApiError(409, f"{', '.join(dirty)}: changes that are not committed; commit them, or choose to stash them")
        moves = []
        for r in moving:
            remote = None
            if r["branch"] not in sites_mod.local_branches(folder, r["name"]):
                remote = state[r["name"]]["upstream"].split("/")[0] or None
                if not remote:
                    raise ApiError(400, f"{r['name']} has no branch {r['branch']} and no remote to fetch it from")
            moves.append({"name": r["name"], "branch": r["branch"], "remote": remote, "stash": r["name"] in dirty})
        # also when the bench has no .nvmrc, so `nvm use` there gives the Node the new version needs
        new_node = check["node"] if str(check["node"]) != str(existing_mod.nvmrc_node(folder)) else None
        steps = self._steps(planner.plan_bench_upgrade, folder, info["sites"], moves, check["new_apps"],
                            python["use"] if rebuild else None, new_node)
        env = dict(self.checks.build_env(check["node"]), **self._git_env())
        probe = preflight_mod.Probe()
        rows = [{"ok": True, "label": f"Every app has a branch for {check['title']}"}]
        if rebuild:
            exe = f"python{python['use']}"
            found = probe.tool_version([exe, "--version"], env)
            rows.append({"ok": bool(found), "label": f"{exe} is available ({found})" if found else
                         f"{exe} was not found. Fix: the System check for {check['title']} installs it"})
        found = probe.node_installed(check["node"])
        rows.append({"ok": bool(found), "label": f"Node {check['node']} is installed with nvm" if found else
                     f"Node {check['node']} is not installed with nvm. Fix: nvm install {check['node']}",
                     **({} if found else {"install_node": str(check["node"])})})
        summary = dict(self._bench_summary(folder, check["version"], check["node"]), title=f"Upgrade to {check['title']}",
                       op="upgrade")
        return self._hold(steps, env, rows, summary, planner.services({"folder": folder}, before="migrate"),
                          check["notices"])

    def get_bench_log(self, query, body):
        info, _ = self._light_bench(query.get("folder"))
        offset = query.get("offset", "")
        try:
            return sites_mod.read_log(info["folder"], query.get("name", ""), int(offset) if offset.isdigit() else None)
        except sites_mod.SiteError as exc:
            raise ApiError(400, str(exc))

    LOGIN_WAIT = 60  # seconds a bench just started gets to take a sign-in and serve its web port

    def post_site_login(self, query, body):
        """A URL that opens the site signed in as Administrator. A stopped bench is started first, since the
        sign-in is kept in its Redis and the desk is served by its web server."""
        info, node = self._light_bench(body.get("folder"))
        folder, site = info["folder"], self._site_of(info, body.get("site"))
        port = info["ports"]["webserver_port"]
        try:
            sites_mod.check_host(site)  # before starting anything: a name no browser can open is no use
        except sites_mod.SiteError as exc:
            raise ApiError(400, str(exc))
        used = projects.usage([folder])
        started = used is not None and folder not in used  # None: this system cannot tell, so just try
        if started:
            self.post_bench_start(query, {"folder": folder})  # its port checks and errors apply
        env = self.checks.build_env(node)
        deadline = time.monotonic() + (self.LOGIN_WAIT if started else 0)
        while True:  # a bench just started needs a few seconds before its Redis takes a sign-in
            try:
                url = sites_mod.login_url(folder, site, port, env)
                break
            except sites_mod.SiteError as exc:
                if time.monotonic() >= deadline:
                    raise ApiError(400, str(exc) + ("; the bench was started and may need longer, so try again"
                                                    if started else ""))
                time.sleep(2)
        while started and self.port_free(port) and time.monotonic() < deadline:
            time.sleep(1)  # and its web server a little longer before the desk answers
        return {"url": url, "started": started}

    def get_site_config(self, query, body):
        info, _ = self._light_bench(query.get("folder"))
        try:
            return {"text": sites_mod.read_config(info["folder"], self._site_of(info, query.get("site")))}
        except (sites_mod.SiteError, OSError, ValueError) as exc:
            raise ApiError(400, f"cannot read site_config.json: {exc}")

    def post_site_config(self, query, body):
        info, _ = self._light_bench(body.get("folder"))
        try:
            sites_mod.write_config(info["folder"], self._site_of(info, body.get("site")), str(body.get("text", "")))
        except sites_mod.SiteError as exc:
            raise ApiError(400, str(exc))
        return {"ok": True}

    def _plan_op(self, body):
        """The bench page's buttons that change something: each is a plan that runs like any other."""
        info, node = self._light_bench(body.get("folder"))
        folder, op = info["folder"], body.get("op")
        site, app = body.get("site"), body.get("app")
        if op == "backup":
            sites = body.get("sites") or info["sites"]
            steps = self._steps(planner.plan_backup, folder, [self._site_of(info, s) for s in sites])
            title = f"Back up {sites[0] if len(sites) == 1 else f'{len(sites)} sites'}"
        elif op == "drop":
            steps = self._steps(planner.plan_drop, folder, self._site_of(info, site), str(body.get("root_password", "")))
            title = f"Drop {site}"
        elif op == "uninstall":
            steps = self._steps(planner.plan_uninstall, folder, self._site_of(info, site), self._app_of(info, app))
            title = f"Uninstall {app} from {site}"
        elif op == "update-app":
            branch, stash = body.get("branch") or None, bool(body.get("stash"))
            state = next(a for a in sites_mod.apps(folder) if a["name"] == self._app_of(info, app))
            remote = None
            if branch:
                if state["changed"] and not stash:
                    raise ApiError(409, f"{app} has changes that are not committed; commit them, or choose Stash and switch")
                if str(branch) not in sites_mod.local_branches(folder, app):
                    remote = state["upstream"].split("/")[0] or None
                    if not remote:
                        raise ApiError(400, f"{app} has no branch {branch} and no remote to fetch it from")
            steps = self._steps(planner.plan_app_update, folder, app, branch, remote, stash, body.get("migrate", True) is not False)
            title = f"Switch {app} to {branch}" if branch else f"Update {app}"
        elif op == "upgrade":
            return self._plan_upgrade(info, body)
        elif op == "upgrade-report":
            return self._plan_report(info, node, body)
        elif op == "fc-download":
            domain = str(body.get("domain", ""))
            try:
                found = frappecloud.links(self._cloud_token(domain), str(body.get("backup", "")), url=self._cloud_url(domain))
            except frappecloud.CloudError as exc:
                raise ApiError(400, str(exc))
            steps = self._steps(planner.plan_download, folder, domain, found)
            title = f"Download {domain} backup"
        else:
            raise ApiError(400, f"unknown action: {op}")
        summary = dict(self._bench_summary(folder, info["version"], node), title=title, site=site or "", op=op)
        parallel = op == "backup"  # each site's backup is its own
        return self._hold(steps, self.checks.build_env(node), [], summary, [], [], parallel=parallel)

    # -- Frappe Cloud backups --------------------------------------------
    def _cloud_path(self):
        return os.path.join(self.state_root, "frappe-cloud.json")

    def _cloud(self):
        """{domain: {token, code, approval_url}}; the tokens stay in this file, readable by this user only."""
        try:
            with open(self._cloud_path()) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_cloud(self, data):
        os.makedirs(self.state_root, exist_ok=True)
        fd = os.open(self._cloud_path() + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(self._cloud_path() + ".new", self._cloud_path())

    def _cloud_token(self, domain):
        found = self._cloud().get(domain)
        if not found:
            raise ApiError(400, f"ask Frappe Cloud for access to {domain} first")
        return found["token"]

    def _cloud_url(self, domain):
        return (self._cloud().get(domain) or {}).get("url") or frappecloud.SITE  # saved before addresses were kept

    def _cloud_call(self, method, domain, **kw):
        try:
            return frappecloud.call(method, self._cloud_token(domain), url=self._cloud_url(domain), **kw)
        except frappecloud.CloudError as exc:
            raise ApiError(400, str(exc))

    def post_cloud_connect(self, query, body):
        try:
            access = frappecloud.request_access(str(body.get("domain", "")), str(body.get("url") or frappecloud.SITE))
        except frappecloud.CloudError as exc:
            raise ApiError(400, str(exc))
        domain = str(body["domain"]).strip().lower()
        self._save_cloud(dict(self._cloud(), **{domain: access}))
        return {"domain": domain, "code": access["code"], "approval_url": access["approval_url"], "url": access["url"],
                "status": "Pending"}

    def get_cloud_site(self, query, body):
        """The request's status and, once approved, the site's backups."""
        domain = query.get("domain", "")
        saved = self._cloud().get(domain) or {}
        status = (self._cloud_call("get_status", domain) or {}).get("status", "")
        backups = self._cloud_call("get_backups", domain, params={"start": 0}) if status == "Approved" else []
        return {"domain": domain, "status": status, "code": saved.get("code", ""), "approval_url": saved.get("approval_url", ""),
                "url": self._cloud_url(domain),
                "running": self._cloud_call("get_running_backup", domain) if status == "Approved" else None,
                "backups": [{"name": b.get("name"), "when": b.get("created_at"), "size": b.get("size")} for b in backups or []]}

    def post_cloud_backup(self, query, body):
        return {"name": self._cloud_call("take_backup", str(body.get("domain", "")), data={})}

    def post_cloud_disconnect(self, query, body):
        domain = str(body.get("domain", ""))
        saved = self._cloud()
        if domain in saved:
            try:
                frappecloud.call("revoke", saved[domain]["token"], data={}, url=self._cloud_url(domain))
            except frappecloud.CloudError:
                pass  # forgotten here either way; Frappe Cloud lets an unused request lapse
            del saved[domain]
            self._save_cloud(saved)
        return {"ok": True}

    # -- marketplace ---------------------------------------------------
    def get_marketplace(self, query, body):
        if self.market_apps is None:
            try:
                self.market_apps = self.market.apps()
            except market_mod.MarketplaceError as exc:
                raise ApiError(502, str(exc))
        return {"apps": self.market_apps}

    def post_marketplace_app(self, query, body):
        """An app's repository, versions and price, with its branches (the one for `version` first)."""
        try:
            info = self.market.app(str(body.get("name", "")))
        except market_mod.MarketplaceError as exc:
            return {"ok": False, "error": str(exc)}
        info.update(ok=True, branches=[], branch_error="")
        if info["repo"]:
            try:
                info["branches"] = self.market.branches(self._clean(info["repo"]), str(body.get("version", "")),
                                                        env=self._git_env())
            except (market_mod.MarketplaceError, deps.InspectError) as exc:
                info["branch_error"] = gitauth.scrub(str(exc))
        return info

    def active_runs(self):
        return [run for run in self.runs.values() if run["runner"].state == "running"]

    def post_run(self, query, body):
        if self.pending is None:
            raise ApiError(409, "nothing has been planned yet")
        if not self.pending["ok"]:
            raise ApiError(409, "pre-flight checks have not passed")
        folder = self.pending["summary"]["folder"]
        # Two runs in one bench would fight over its apps, assets and Redis.
        if any(run["summary"]["folder"] == folder for run in self.active_runs()):
            raise ApiError(409, "another run is still working in this bench folder")
        run_id = base = time.strftime("%Y-%m-%d-%H%M%S")
        n = 1
        while run_id in self.runs:  # two runs started in the same second
            n += 1
            run_id = f"{base}-{n}"
        runner = Runner(self.pending["steps"], os.path.join(self.state_root, run_id), self.pending["env"],
                        self.pending["services"], parallel=self.pending["parallel"])
        self.runs[run_id] = {"id": run_id, "runner": runner, "summary": self.pending["summary"]}
        self.run_id = run_id
        if not self.pending["summary"]["existing"]:
            self._remember(folder)
        self.pending = None
        runner.start()
        return {"ok": True, "run_id": run_id}

    def _find_run(self, query, body):
        """The run a request names (`run` in its body or query), else the run started last; None if neither."""
        return self.runs.get(body.get("run") or query.get("run") or self.run_id)

    def _need_run(self, query, body):
        run = self._find_run(query, body)
        if run is None:
            raise ApiError(409, "no such run")
        return run

    def _describe(self, run):
        runner = run["runner"]
        return dict(runner.snapshot(), run_id=run["id"], log_dir=runner.log_dir, summary=run["summary"])

    def get_runs(self, query, body):
        """Every run of this session, newest first, without their steps."""
        return {"runs": [{"run_id": run["id"], "state": run["runner"].state, "summary": run["summary"]}
                         for run in reversed(list(self.runs.values()))]}

    def get_run(self, query, body):
        run = self._find_run(query, body)
        return self._describe(run) if run else {"state": "none", "steps": []}

    def get_log(self, query, body):
        runner = self._need_run(query, body)["runner"]
        step = query.get("step", "")
        if step not in {s.id for s in runner.steps}:
            raise ApiError(404, "unknown step")
        return {"log": runner.read_log(step)}

    def post_retry(self, query, body):
        run = self._need_run(query, body)
        if run["runner"].state == "failed" and any(
                other["summary"]["folder"] == run["summary"]["folder"] for other in self.active_runs()):
            raise ApiError(409, "another run is still working in this bench folder")
        try:
            run["runner"].retry(bool(body.get("cleanup")))
        except RuntimeError as exc:
            raise ApiError(409, str(exc))
        return {"ok": True}

    def post_dismiss(self, query, body):
        run = self._need_run(query, body)
        if run["runner"].state == "running":
            raise ApiError(409, "a run is in progress")
        del self.runs[run["id"]]
        if self.run_id == run["id"]:
            self.run_id = next(reversed(list(self.runs)), None)
        return {"ok": True}

    def post_cancel(self, query, body):
        self._need_run(query, body)["runner"].cancel()
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
        ("POST", "/api/quit"): post_quit,
        ("POST", "/api/plan"): post_plan,
        ("POST", "/api/run"): post_run,
        ("GET", "/api/runs"): get_runs,
        ("GET", "/api/projects"): get_projects,
        ("POST", "/api/projects/add"): post_projects_add,
        ("POST", "/api/projects/remove"): post_projects_remove,
        ("GET", "/api/folders"): get_folders,
        ("GET", "/api/bench/detail"): get_bench_detail,
        ("POST", "/api/bench/updates"): post_bench_updates,
        ("POST", "/api/bench/branches"): post_bench_branches,
        ("POST", "/api/bench/upgrade"): post_bench_upgrade,
        ("GET", "/api/bench/report"): get_bench_report,
        ("GET", "/api/bench/log"): get_bench_log,
        ("POST", "/api/site/login"): post_site_login,
        ("GET", "/api/site/config"): get_site_config,
        ("POST", "/api/site/config"): post_site_config,
        ("POST", "/api/cloud/connect"): post_cloud_connect,
        ("GET", "/api/cloud/site"): get_cloud_site,
        ("POST", "/api/cloud/backup"): post_cloud_backup,
        ("POST", "/api/cloud/disconnect"): post_cloud_disconnect,
        ("POST", "/api/ports/allocate"): post_ports_allocate,
        ("POST", "/api/bench/start"): post_bench_start,
        ("POST", "/api/bench/stop"): post_bench_stop,
        ("GET", "/api/marketplace"): get_marketplace,
        ("POST", "/api/marketplace/app"): post_marketplace_app,
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
            return self._events(query)
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

    def _events(self, query):
        run = self.app._find_run(query, {})
        runner = run["runner"] if run else None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = runner.subscribe() if runner else queue.Queue()
        try:
            first = dict(self.app._describe(run) if run else {"state": "none", "steps": []}, type="snapshot")
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
