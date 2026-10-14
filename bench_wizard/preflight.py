"""Checks shown on the Review screen before anything is written."""
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

from . import checks
from .deps import _git

BASE_BYTES = 3 * 1024 ** 3


class Probe:
    """Everything pre-flight asks of the machine, so tests can substitute it."""

    def __init__(self, git_env=None):
        self.git_env = git_env or {}

    def free_bytes(self, path):
        return shutil.disk_usage(path).free

    def root_password_ok(self, password):
        return checks.verify_mariadb_root(password)

    def database_exists(self, name, password):
        env = dict(os.environ, MYSQL_PWD=password or "")
        rc, out = checks.run_cmd(["mariadb", "-uroot", "-N", "-e", "SHOW DATABASES"], env, timeout=20)
        return rc == 0 and name in out.split()

    def ls_remote(self, repo, branch):
        """'ok', 'no_branch' or 'denied'. `branch` may be a branch or a tag."""
        try:
            result = _git(["ls-remote", "--exit-code", repo, f"refs/heads/{branch}", f"refs/tags/{branch}"],
                          timeout=30, env=self.git_env)
        except RuntimeError:
            return "denied"
        return {0: "ok", 2: "no_branch"}.get(result.returncode, "denied")

    def tool_version(self, argv, env):
        rc, out = checks.run_cmd(argv, env, timeout=30)
        return checks._first_version(out) if rc == 0 else ""


def _existing_ancestor(path):
    path = os.path.dirname(path)
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path or "/"


def _gb(n):
    return f"{n / 1024 ** 3:.1f} GB"


def run(answers, resolution, backup_info, catalog, env, probe=None):
    probe = probe or Probe()
    rows = []
    add = lambda ok, label: rows.append({"ok": bool(ok), "label": label})
    folder, version = answers["folder"], answers["version"]
    profile = catalog["frappe_versions"][version]
    restore = answers.get("restore")

    ancestor = _existing_ancestor(folder)
    if os.path.lexists(folder):
        add(False, f"Bench folder {folder} already exists")
    elif not os.access(ancestor, os.W_OK | os.X_OK):
        add(False, f"Cannot write to {ancestor}, so bench folder {folder} cannot be created")
    else:
        add(True, f"Bench folder {folder} does not exist")

    password = answers.get("mariadb_root_password", "")
    if not probe.root_password_ok(password):
        add(False, "MariaDB root password was not accepted")
    elif answers.get("db_name"):
        taken = probe.database_exists(answers["db_name"], password)
        add(not taken, f"Database {answers['db_name']} already exists in MariaDB" if taken
            else f"No database named {answers['db_name']} in MariaDB")
    else:
        add(True, "MariaDB root password accepted; bench will choose the database name")

    need = BASE_BYTES
    if restore:
        for key, label in (("sql", "Database backup"), ("public", "Public files"), ("private", "Private files")):
            path = restore.get(key)
            if not path:
                continue
            readable = os.path.isfile(path) and os.access(path, os.R_OK)
            add(readable, f"{label} {path} is readable" if readable else f"{label} {path} cannot be read")
            if readable:
                size = os.path.getsize(path)
                need += size * (10 if path.endswith(".gz") else 2) if key == "sql" else size
    free = probe.free_bytes(ancestor)
    add(free >= need, f"{_gb(free)} free on the target filesystem ({_gb(need)} needed)")

    frappe = catalog["apps"]["frappe"]
    remotes = [("Frappe", frappe["repo"], frappe["branches"].get(version, version))]
    remotes += [(resolution.apps[n]["title"], resolution.apps[n]["repo"], resolution.apps[n]["branch"])
                for n in resolution.order]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda r: probe.ls_remote(r[1], r[2]), remotes))
    for (title, repo, branch), result in zip(remotes, results):
        if result == "ok":
            add(True, f"git ls-remote succeeded for {title} ({branch})")
        elif result == "no_branch":
            add(False, f"{title}: no branch or tag named {branch} in {repo}")
        else:
            add(False, f"Your Git credentials lack access to {repo}. Fix: a credential helper or token "
                       f"for the HTTPS URL, or add the app by its SSH URL as a custom app.")

    for error in resolution.errors:
        add(False, error)

    python = f"python{profile['python']}"
    found = probe.tool_version([python, "--version"], env)
    add(found, f"{python} is available ({found})" if found else f"{python} was not found")
    found = probe.tool_version(["bench", "--version"], env)
    add(found, f"Bench CLI {found} is available" if found else "Bench CLI was not found")

    if restore:
        apps = backup_info.apps if backup_info else {}
        if not apps:
            add(True, "Could not read the app list from this backup; app checks skipped")
        else:
            for name in apps:
                if name != "frappe" and name not in resolution.apps:
                    add(True, f"The backup contains {name}, which is not selected. It will be removed "
                              f"from the site's installed apps after the restore; its data stays.")
            target = int(version.split("-")[-1]) if version.split("-")[-1].isdigit() else None
            major = backup_info.frappe_major
            if major and target and major > target:
                add(False, f"The backup is from Frappe {major}; Frappe cannot downgrade a database to {target}")
            elif major and target and major < target:
                add(True, f"This backup will be upgraded from {major} to {target}")
            else:
                add(True, f"Backup apps: {', '.join(apps)}")
    return rows
