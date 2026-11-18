"""Turn the wizard's answers into the ordered list of commands to run."""
import os
import re
import shlex
import sys
from dataclasses import dataclass

MASK = "••••"

# What a restored production copy must not repeat: queued mail, mailbox pulls, webhooks, SMS and Slack alerts.
# The tables, fields and Email Queue statuses are the same in Frappe v15 and v16.
SANITIZE_SQL = (
    "SET SQL_SAFE_UPDATES=0; "  # `bench mariadb` starts the client with --safe-updates, which refuses these
    "UPDATE `tabEmail Queue` SET status='Error', error='Held back by Bench Wizard: site restored from a backup' "
    "WHERE status IN ('Not Sent','Partially Sent','Sending'); "
    "UPDATE `tabEmail Account` SET enable_incoming=0; "
    "UPDATE `tabWebhook` SET enabled=0; "
    "UPDATE `tabNotification` SET enabled=0 WHERE channel IN ('Slack','SMS');"
)


class PlanError(ValueError):
    pass


@dataclass
class Step:
    id: str
    title: str
    argv: list
    cwd: str
    secret_args: tuple = ()
    cleanup: list = None
    cleanup_secret_args: tuple = ()
    cleanup_required: bool = False
    retry_argv: list = None

    def display(self, argv=None, secret_args=None):
        """The command as a shell line, with secret arguments masked."""
        if argv is None:
            argv, secret_args = self.argv, self.secret_args
        secret_args = secret_args or ()
        return " ".join(MASK if i in secret_args else shlex.quote(a) for i, a in enumerate(argv))


def _secret_after(argv, *flags):
    return tuple(i + 1 for i, arg in enumerate(argv) if arg in flags)


def _get_app(app, folder):
    tail = ["--branch", app["branch"], app["repo"]]
    return Step(f"get-{app['name']}", f"get-app {app['name']}", ["bench", "get-app", "--skip-assets"] + tail,
                folder, retry_argv=["bench", "get-app", "--overwrite", "--skip-assets"] + tail)


def _nvmrc(node, folder):
    return Step("nvmrc", f"write .nvmrc (Node {node})", ["sh", "-c", 'printf "%s\\n" "$1" > .nvmrc', "sh", node],
                folder)


def _site_name(site):
    # The site is a folder the bench already has (the server checks); it only must not read as an option.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", site):
        raise PlanError(f"cannot use the site name {site!r}")
    return site


def plan_migrate(answers):
    """`bench migrate` for each site of an existing bench; the runner runs these side by side.

    With `backup`, each site is backed up first, in the same step, so its migrate waits for its own backup.
    """
    if answers.get("backup"):
        both = 'bench --site "$1" backup --with-files && bench --site "$1" migrate'
        return [Step(f"migrate-{site}", f"back up and migrate {site}", ["sh", "-c", both, "sh", _site_name(site)],
                     answers["folder"]) for site in answers["sites"]]
    return [Step(f"migrate-{site}", f"migrate {site}", ["bench", "--site", _site_name(site), "migrate"],
                 answers["folder"]) for site in answers["sites"]]


def plan_backup(folder, sites):
    return [Step(f"backup-{site}", f"back up {site}", ["bench", "--site", _site_name(site), "backup", "--with-files"],
                 folder) for site in sites]


def plan_drop(folder, site, root_password):
    """Drop a site. bench backs it up first and moves its folder to archived_sites, so it can be restored."""
    if not root_password:
        raise PlanError("the MariaDB root password is required to drop a site")
    argv = ["bench", "drop-site", _site_name(site), "--db-root-password", root_password]
    return [Step("drop-site", f"drop-site {site}", argv, folder, _secret_after(argv, "--db-root-password"))]


def plan_uninstall(folder, site, app):
    """Remove an app from one site; bench backs the site up first. The app stays in the bench."""
    return [Step(f"uninstall-{app}", f"uninstall-app {app} from {site}",
                 ["bench", "--site", _site_name(site), "uninstall-app", _site_name(app), "--yes"], folder)]


def _branch_name(branch):
    # Branch names may hold "/" (feature/x); none may read as an option or climb out with "..".
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./+-]*", branch or "") or ".." in branch or branch.endswith((".lock", "/")):
        raise PlanError(f"cannot use the branch name {branch!r}")
    return branch


# Fetch one branch. get-app clones one branch: the remote is told to follow this one too (or the checkout cannot track
# it, and bench update would not pull it), and a shallow clone stays shallow instead of downloading the whole history.
FETCH_ONE = ('git config --get-all "remote.$1.fetch" | grep -qxF -e "+refs/heads/*:refs/remotes/$1/*" '
             '-e "+refs/heads/$2:refs/remotes/$1/$2" || git remote set-branches --add "$1" "$2" || exit 1; '
             'd=; [ -f "$(git rev-parse --git-dir)/shallow" ] && d=--depth=1; '
             'exec git fetch $d "$1" "+refs/heads/$2:refs/remotes/$1/$2"')


def switch_steps(folder, app, branch, remote=None, stash=False):
    """Move an app to another branch with git itself, never `checkout -f` (which is what bench switch-to-branch
    does, throwing away uncommitted work). `remote` names the remote when the branch is not local yet."""
    app, branch = _site_name(app), _branch_name(branch)
    app_dir = os.path.join(folder, "apps", app)
    steps = [Step(f"stash-{app}", f"stash {app}'s local changes",
                  ["git", "stash", "push", "-m", f"bench-wizard: before switching to {branch}"], app_dir)] if stash else []
    if remote:
        remote = _site_name(remote)
        steps += [Step(f"fetch-{app}", f"fetch {branch} of {app}", ["sh", "-c", FETCH_ONE, "sh", remote, branch], app_dir),
                  Step(f"switch-{app}", f"switch {app} to {branch}",
                       ["git", "checkout", "-b", branch, "--track", f"{remote}/{branch}"], app_dir)]
    else:
        steps.append(Step(f"switch-{app}", f"switch {app} to {branch}", ["git", "checkout", branch, "--"], app_dir))
    return steps


def plan_app_update(folder, app, branch=None, remote=None, stash=False, migrate=True):
    """Pull an app's new commits, or move it to another branch, then bring the bench up to it."""
    app = _site_name(app)
    first = (switch_steps(folder, app, branch, remote, stash) if branch else
             [Step("pull", f"pull {app}", ["bench", "update", "--pull", "--apps", app], folder)])
    return first + [Step("requirements", "bench setup requirements", ["bench", "setup", "requirements"], folder)] + (
        [Step("migrate", "migrate every site", ["bench", "--site", "all", "migrate"], folder)] if migrate else []) + [
        Step("build", f"build {app}", ["bench", "build", "--app", app], folder)]


def plan_bench_upgrade(folder, sites, moves, new_apps, python=None, node=None):
    """Move every app of a bench to another Frappe version, in place: back the sites up, switch the apps, give the
    bench a new Python environment if it needs one, add apps the new branches require, then requirements, migrate
    and build. `moves` are {"name", "branch", "remote", "stash"}; `python` ("3.14") rebuilds env/ with it."""
    steps = plan_backup(folder, sites)
    for move in moves:
        steps += switch_steps(folder, move["name"], move["branch"], move.get("remote"), move.get("stash"))
    if python:
        if not re.fullmatch(r"\d+\.\d+", python):
            raise PlanError(f"cannot use Python {python!r}")
        old = "env-before-upgrade"  # kept, so going back is: rm -rf env && mv env-before-upgrade env
        steps += [Step("env-aside", f"keep the old Python environment as {old}",
                       ["sh", "-c", 'if [ -e "$1" ]; then echo "$1 exists: remove it or rename it first" >&2; exit 1; fi; '
                                    'mv env "$1"', "sh", old], folder),
                  Step("env", f"new Python {python} environment", ["bench", "setup", "env", "--python", f"python{python}"],
                       folder)]
    if node:
        steps.append(_nvmrc(node, folder))
    steps += [_get_app(app, folder) for app in new_apps]
    steps.append(Step("requirements", "bench setup requirements", ["bench", "setup", "requirements"], folder))
    if sites:  # bench refuses `--site all` on a bench that has none
        steps.append(Step("migrate", "migrate every site", ["bench", "--site", "all", "migrate"], folder))
    return steps + [Step("build", "build every app's assets", ["bench", "build"], folder)]


# A cached copy of one branch: cloned the first time, moved to the branch's newest commit after that.
FETCH_TREE = ('if [ -d "$3/.git" ]; then git -C "$3" fetch -q --depth 1 origin "$2" && exec git -C "$3" checkout -q -f FETCH_HEAD; '
              'fi; mkdir -p "$(dirname "$3")" && exec git clone -q --depth 1 --branch "$2" -- "$1" "$3"')
# Claude's review is kept only when it finished, so a stopped run never leaves half a report.
KEEP_REVIEW = 'out=$1; shift; "$@" > "$out.part"; rc=$?; cat "$out.part"; [ $rc -eq 0 ] && mv "$out.part" "$out"; exit $rc'


def plan_upgrade_report(folder, trees, scans, scanner, claude=None, plugin=None):
    """What custom apps must change for another Frappe version. `trees` are (name, repo, branch, path) of the target
    versions; each scan is {"app", "out", "rules", "sources", "targets", "prompt"}, sources and targets being
    (name, path). With `claude` (its path), Claude reviews the scanner's findings with read-only tools."""
    steps = [Step(f"tree-{name}", f"fetch {name} {branch} to compare with", ["sh", "-c", FETCH_TREE, "sh", repo,
                                                                             _branch_name(branch), path], folder)
             for name, repo, branch, path in trees]
    for scan in scans:
        app = _site_name(scan["app"])
        app_dir, out = os.path.join(folder, "apps", app), scan["out"]
        argv = [sys.executable, scanner, "--app", app_dir, "--rules", scan["rules"], "--out", out]
        argv += [a for name, path in scan["sources"] for a in ("--source", f"{name}={path}")]
        argv += [a for name, path in scan["targets"] for a in ("--target", f"{name}={path}")]
        steps.append(Step(f"scan-{app}", f"scan {app}",
                          ["sh", "-c", 'rm -f "$1/claude.md"; shift; exec "$@"', "sh", out] + argv, folder))
        if claude:
            dirs = [out] + [path for _, path in scan["sources"] + scan["targets"]]
            review = [claude, "-p", scan["prompt"], "--plugin-dir", plugin, "--restricted", "--strict-mcp-config",
                      "--allowed-tools", "Read", "Grep", "Glob", "Skill", "--no-session-persistence"]
            review += [a for d in dirs for a in ("--add-dir", d)]
            steps.append(Step(f"review-{app}", f"Claude reviews {app}",
                              ["sh", "-c", KEEP_REVIEW, "sh", os.path.join(out, "claude.md")] + review, app_dir))
    return steps


def plan_download(folder, domain, links):
    """Download a Frappe Cloud backup's files into fc-backups/<domain>/ of the bench, under their own names."""
    target = os.path.join(folder, "fc-backups", _site_name(domain))
    steps = [Step("fc-folder", "make the download folder", ["mkdir", "-p", target], folder)]
    for kind, (name, url) in links.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not url.startswith("https://"):
            raise PlanError("Frappe Cloud sent a download this wizard will not fetch")
        argv = ["curl", "-fsSL", "--retry", "3", "-w", "%{size_download} bytes in %{time_total} s\\n",
                "-o", os.path.join(target, name), url]
        # the link is signed, so it is a credential while it lasts
        steps.append(Step(f"download-{kind}", f"download {name}", argv, folder, (len(argv) - 1,)))
    return steps


PROCFILE_PORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "procfile_port.py")
PORT_KEYS = ("webserver_port", "socketio_port", "file_watcher_port", "redis_cache", "redis_queue")


def plan_ports(folder, ports):
    """Move a bench to new ports: its common config, its Redis config files and its Procfile's web port.

    They take effect the next time the bench is started.
    """
    values = [ports.get(key) for key in PORT_KEYS]
    if not all(isinstance(v, int) and not isinstance(v, bool) and 1024 <= v <= 65535 for v in values):
        raise PlanError("every port must be a number from 1024 to 65535")
    if len(set(values)) != len(values):
        raise PlanError("a bench needs a different port for each service")
    argv = ["bench", "config", "set-common-config"]
    for key in PORT_KEYS[:3]:
        argv += ["-c", key, str(ports[key])]
    # set-common-config reads each value as a Python literal, so a URL goes in quoted; redis_socketio
    # follows redis_cache, as bench itself keeps it.
    for key, port in (("redis_cache", ports["redis_cache"]), ("redis_queue", ports["redis_queue"]),
                      ("redis_socketio", ports["redis_cache"])):
        argv += ["-c", key, repr(f"redis://127.0.0.1:{port}")]
    return [Step("ports", "set ports in common_site_config.json", argv, folder),
            Step("setup-redis", "bench setup redis", ["bench", "setup", "redis"], folder),
            Step("procfile", f"web port {ports['webserver_port']} in Procfile",
                 [sys.executable, PROCFILE_PORT, str(ports["webserver_port"])], folder)]


def plan_build(folder):
    return [Step("build", "bench build", ["bench", "build"], folder)]


def plan_existing(answers):
    """Add apps to a site of a bench that already exists, then rebuild the assets.

    Apps marked `on_bench` are in the bench already and are only installed on the site.
    Nothing here removes anything, so no step has a cleanup.
    """
    folder, site = answers["folder"], _site_name(answers["site"])
    on_site = ["bench", "--site", site]
    steps = [_nvmrc(answers["node"], folder)] if answers.get("write_nvmrc") else []
    steps += [_get_app(app, folder) for app in answers["apps"] if not app.get("on_bench")]
    if len(steps) > bool(answers.get("write_nvmrc")):
        # A running bench's Redis still holds the app -> modules map from before get-app; without
        # this, install-app syncs none of the new app's doctypes and its after_install fails.
        steps.append(Step("clear-cache", "clear-cache", on_site + ["clear-cache"], folder))
    steps += [Step(f"install-{app['name']}", f"install-app {app['name']}", on_site + ["install-app", app["name"]],
                   folder) for app in answers["apps"]]
    steps.append(Step("build", "bench build", ["bench", "build"], folder))
    return steps


def plan(answers, catalog):
    """Set up a new bench and site, or with `existing` only a new site in that bench.

    In an existing bench, apps marked `on_bench` are not fetched, and its default site and
    developer mode are left alone.
    """
    existing = answers.get("existing")
    version = answers.get("version")
    profile = catalog["frappe_versions"].get(version)
    if profile is None:
        raise PlanError(f"unknown Frappe version: {version}")
    for key, label in (("folder", "bench folder"), ("site", "site name"),
                       ("admin_password", "Administrator password")):
        if not answers.get(key):
            raise PlanError(f"{label} is required")
    folder, site = answers["folder"], answers["site"]
    if not os.path.isabs(folder):
        raise PlanError("bench folder must be an absolute path")
    if any(ch.isspace() for ch in folder):
        raise PlanError("bench folder path must not contain spaces; bench cannot work in such a folder")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", site):
        raise PlanError("site name may use lowercase letters, digits, dots and hyphens")
    if answers.get("db_name") and not re.fullmatch(r"[A-Za-z0-9_]+", answers["db_name"]):
        raise PlanError("database name may use letters, digits and underscores")
    root_pw, admin_pw = answers.get("mariadb_root_password", ""), answers["admin_password"]
    apps = [a for a in answers.get("apps", []) if a["name"] != "frappe"]
    restore = answers.get("restore")
    if restore and not restore.get("sql"):
        raise PlanError("database backup path is required to restore")
    # These are passed to bench as bare arguments; a leading "-" would be read as an option.
    bare = [("Administrator password", admin_pw)]
    if restore:
        bare += [("database backup path", restore["sql"]), ("source site encryption key", restore.get("site_key"))]
    for label, value in bare:
        if value and value.startswith("-"):
            raise PlanError(f'{label} must not start with "-"')
    on_site = ["bench", "--site", site]
    steps = []

    def add(id, title, argv, secrets=(), **extra):
        steps.append(Step(id, title, argv, extra.pop("cwd", folder), secrets, **extra))

    def install(app):
        add(f"install-{app['name']}", f"install-app {app['name']}", on_site + ["install-app", app["name"]])

    if not existing:
        add("init", "bench init",
            ["bench", "init", folder, "--frappe-branch", version, "--python", f"python{profile['python']}"],
            cwd="/", cleanup=["rm", "-rf", folder], cleanup_required=True)
        if answers.get("ports"):  # a port slot no other bench uses, so it can run next to them
            steps += plan_ports(folder, answers["ports"])
    if not existing or answers.get("write_nvmrc"):
        steps.append(_nvmrc(answers.get("node") or profile["node"], folder))
    steps += [_get_app(app, folder) for app in apps if not app.get("on_bench")]

    new_site = ["bench", "new-site", site]
    if answers.get("db_name"):
        new_site += ["--db-name", answers["db_name"]]
    new_site += ["--mariadb-root-password", root_pw, "--admin-password", admin_pw]
    drop = ["bench", "drop-site", site, "--force", "--no-backup", "--db-root-password", root_pw]
    add("new-site", f"new-site {site}", new_site,
        _secret_after(new_site, "--mariadb-root-password", "--admin-password"),
        cleanup=drop, cleanup_secret_args=_secret_after(drop, "--db-root-password"))

    if restore:
        flags = (("--with-public-files", "public"), ("--with-private-files", "private"),
                 ("--encryption-key", "backup_key"))
        argv = on_site + ["restore", restore["sql"], "--db-root-password", root_pw]
        if restore.get("partial"):  # bench refuses `restore` for these; loaded over the new site instead
            if restore.get("public") or restore.get("private"):
                raise PlanError("a partial backup cannot be restored together with file archives")
            argv, flags = on_site + ["partial-restore", restore["sql"]], flags[2:]
        for flag, key in flags:
            if restore.get(key):
                argv += [flag, restore[key]]
        add("restore", "restore backup", argv, _secret_after(argv, "--db-root-password", "--encryption-key"))
        if restore.get("site_key"):
            argv = on_site + ["set-config", "encryption_key", restore["site_key"]]
            add("site-key", "set encryption key", argv, (len(argv) - 1,))
        # Before migrate: nothing from the source may send or sync once the site can run code.
        if restore.get("mute_emails"):
            add("mute-emails", "mute outgoing email", on_site + ["set-config", "mute_emails", "1"])
        if restore.get("pause_scheduler"):
            add("pause-scheduler", "pause scheduler", on_site + ["set-config", "pause_scheduler", "1"])
        if restore.get("sanitize"):
            add("sanitize", "hold back queued email, webhooks and alerts", on_site + ["mariadb", "-e", SANITIZE_SQL])
        in_backup = set(restore.get("backup_apps") or [])
        # migrate imports every installed app, so one the bench lacks must be forgotten first
        for name in restore.get("backup_apps") or []:
            if name != "frappe" and name not in {app["name"] for app in apps}:
                add(f"forget-{name}", f"remove {name} from installed apps",
                    on_site + ["remove-from-installed-apps", name])
        add("migrate", "migrate", on_site + ["migrate"])
        for app in apps:
            if app["name"] not in in_backup:
                install(app)
        argv = on_site + ["set-admin-password", admin_pw]
        add("admin-password", "set Administrator password", argv, (len(argv) - 1,))
    else:
        for app in apps:
            install(app)

    if not existing:
        add("use", "bench use", ["bench", "use", site])
        add("developer-mode", "set-config developer_mode", on_site + ["set-config", "developer_mode", "1"])
    add("build", "bench build", ["bench", "build"])
    if not restore:
        add("migrate", "migrate", on_site + ["migrate"])
    return steps


def services(answers, before="new-site"):
    """Background processes the site steps need: the bench's own Redis.

    install-app and migrate queue jobs, which fails unless the bench's Redis is up. Outside
    the wizard `bench start` runs it; here it lives only while the site steps run.
    """
    folder = answers["folder"]
    return [{"argv": ["redis-server", f"config/redis_{name}.conf"], "cwd": folder, "before": before}
            for name in ("queue", "cache")]
