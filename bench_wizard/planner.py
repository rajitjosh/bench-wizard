"""Turn the wizard's answers into the ordered list of commands to run."""
import os
import re
import shlex
from dataclasses import dataclass

MASK = "••••"


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


def plan(answers, catalog):
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

    add("init", "bench init",
        ["bench", "init", folder, "--frappe-branch", version, "--python", f"python{profile['python']}"],
        cwd="/", cleanup=["rm", "-rf", folder], cleanup_required=True)
    add("nvmrc", f"write .nvmrc (Node {profile['node']})",
        ["sh", "-c", 'printf "%s\\n" "$1" > .nvmrc', "sh", profile["node"]])

    for app in apps:
        tail = ["--branch", app["branch"], app["repo"]]
        add(f"get-{app['name']}", f"get-app {app['name']}", ["bench", "get-app", "--skip-assets"] + tail,
            retry_argv=["bench", "get-app", "--overwrite", "--skip-assets"] + tail)

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
        if restore.get("mute_emails"):
            add("mute-emails", "mute outgoing email", on_site + ["set-config", "mute_emails", "1"])
        if restore.get("pause_scheduler"):
            add("pause-scheduler", "pause scheduler", on_site + ["set-config", "pause_scheduler", "1"])
    else:
        for app in apps:
            install(app)

    add("use", "bench use", ["bench", "use", site])
    add("developer-mode", "set-config developer_mode", on_site + ["set-config", "developer_mode", "1"])
    add("build", "bench build", ["bench", "build"])
    if not restore:
        add("migrate", "migrate", on_site + ["migrate"])
    return steps


def services(answers):
    """Background processes the site steps need: the bench's own Redis.

    install-app and migrate queue jobs, which fails unless the bench's Redis is up. Outside
    the wizard `bench start` runs it; here it lives only while the site steps run.
    """
    folder = answers["folder"]
    return [{"argv": ["redis-server", f"config/redis_{name}.conf"], "cwd": folder, "before": "new-site"}
            for name in ("queue", "cache")]
