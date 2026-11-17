"""One bench up close: its apps' git state, its backups and logs, a site's config, an admin login link and why a
migrate failed. Nothing here runs a long bench command; those go through the planner and the runner."""
import json
import os
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .gitauth import scrub

MASK = "********"
SECRET = re.compile(r"password|secret|token|(^|_)key$", re.I)  # db_password, encryption_key, ...
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOG_TAIL = 200_000  # bytes of a log sent when the page opens it
BACKUP_DB = "-database.sql.gz"


class SiteError(Exception):
    pass


def _site_dir(folder, site):
    path = os.path.join(folder, "sites", site)
    if "/" in site or site.startswith(".") or not os.path.isfile(os.path.join(path, "site_config.json")):
        raise SiteError(f"{site} is not a site of this bench")
    return path


def _git(app_dir, *args, timeout=15):
    try:
        result = subprocess.run(["git", "-C", app_dir, *args], capture_output=True, text=True, errors="replace",
                                timeout=timeout, stdin=subprocess.DEVNULL, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr).strip()


def _upstream(app_dir, branch):
    """The remote branch the app follows: its tracking branch, else the same name on its first remote."""
    ok, out = _git(app_dir, "rev-parse", "--abbrev-ref", "@{u}")
    if ok:
        return out
    ok, remotes = _git(app_dir, "remote")
    return f"{remotes.split()[0]}/{branch}" if ok and remotes else ""


def _app(folder, name, fetch):
    app_dir = os.path.join(folder, "apps", name)
    info = {"name": name, "branch": "", "commit": "", "date": "", "remote": "", "upstream": "", "changed": False,
            "behind": None, "error": ""}
    ok, branch = _git(app_dir, "rev-parse", "--abbrev-ref", "HEAD")
    if not ok:
        return dict(info, error=branch.splitlines()[-1] if branch else "not a git repository")
    ok, log = _git(app_dir, "log", "-1", "--format=%h%x00%cs")
    commit, date = log.split("\0") if ok and "\0" in log else ("", "")
    upstream = _upstream(app_dir, branch)
    remote = _git(app_dir, "remote", "get-url", upstream.split("/")[0])[1] if upstream else ""
    info.update(branch=branch, commit=commit, date=date, upstream=upstream, remote=scrub(remote),
                changed=bool(_git(app_dir, "status", "--porcelain", "--untracked-files=no")[1]))
    if fetch and upstream and branch != "HEAD":
        ok, out = _git(app_dir, "fetch", "--quiet", upstream.split("/")[0], upstream.split("/", 1)[1], timeout=90)
        if not ok:
            return dict(info, error=scrub(out.splitlines()[-1] if out else "git fetch failed"))
    if upstream:
        ok, out = _git(app_dir, "rev-list", "--count", f"HEAD..{upstream}")
        info["behind"] = int(out) if ok and out.isdigit() else None
    return info


def apps(folder, fetch=False):
    """[{name, branch, commit, date, remote, upstream, changed, behind, error}] of the bench's apps. With `fetch`,
    each app's remote branch is fetched first, so `behind` counts the commits it has not pulled yet."""
    apps_dir = os.path.join(folder, "apps")
    try:
        names = sorted(n for n in os.listdir(apps_dir) if os.path.isdir(os.path.join(apps_dir, n, ".git")))
    except OSError:
        return []
    names.sort(key=lambda n: n != "frappe")
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda n: _app(folder, n, fetch), names))


def backups(folder, limit=60):
    """Backups of the bench's sites, and ones downloaded from Frappe Cloud into fc-backups/, newest first:
    [{site, name, when, db, public, private, size}], each file a path or ""."""
    found = []
    roots = [(site, os.path.join(folder, "sites", site, "private", "backups"))
             for site in _listdir(os.path.join(folder, "sites"))]
    roots += [(f"{domain} (Frappe Cloud)", os.path.join(folder, "fc-backups", domain))
              for domain in _listdir(os.path.join(folder, "fc-backups"))]
    for site, path in roots:
        for name in _listdir(path):
            if not name.endswith(BACKUP_DB):
                continue
            prefix = os.path.join(path, name[:-len(BACKUP_DB)])
            files = {"db": prefix + BACKUP_DB, "public": prefix + "-files.tar", "private": prefix + "-private-files.tar"}
            files = {k: p if os.path.isfile(p) else "" for k, p in files.items()}
            stat = os.stat(files["db"])
            found.append(dict(files, site=site, name=os.path.basename(prefix), when=stat.st_mtime,
                              size=sum(os.path.getsize(p) for p in files.values() if p)))
    return sorted(found, key=lambda b: -b["when"])[:limit]


def _listdir(path):
    try:
        return sorted(n for n in os.listdir(path) if os.path.isdir(os.path.join(path, n)) or n.endswith(BACKUP_DB))
    except OSError:
        return []


def logs(folder):
    """[{name, size, when}] of the files in the bench's logs folder, most recently written first."""
    path = os.path.join(folder, "logs")
    try:
        entries = [e for e in os.scandir(path) if e.is_file()]
    except OSError:
        return []
    return [{"name": e.name, "size": e.stat().st_size, "when": e.stat().st_mtime}
            for e in sorted(entries, key=lambda e: -e.stat().st_mtime)]


def read_log(folder, name, offset=None):
    """{text, offset, size}: the log from `offset` on, or its last part when no offset is given. A log that
    shrank (rotated) is read again from its start."""
    if not name or "/" in name or name.startswith("."):
        raise SiteError("not a log of this bench")
    path = os.path.join(folder, "logs", name)
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            start = max(0, size - LOG_TAIL) if offset is None or offset > size else offset
            fh.seek(start)
            data = fh.read(LOG_TAIL)
    except OSError as exc:
        raise SiteError(f"cannot read {name}: {exc.strerror}") from exc
    text = ANSI.sub("", data.decode("utf-8", "replace"))
    if start and offset is None:
        text = text.split("\n", 1)[-1]  # starts on a whole line
    return {"text": text, "offset": start + len(data), "size": size}


def read_config(folder, site):
    """The site's site_config.json as text, each secret shown as MASK."""
    with open(os.path.join(_site_dir(folder, site), "site_config.json")) as fh:
        config = json.load(fh)
    return json.dumps({k: MASK if SECRET.search(k) and v else v for k, v in config.items()}, indent=1)


def write_config(folder, site, text):
    """Save the site's config from the page's text; a secret still shown as MASK keeps its value."""
    path = os.path.join(_site_dir(folder, site), "site_config.json")
    try:
        new = json.loads(text)
    except ValueError as exc:
        raise SiteError(f"not valid JSON: {exc}") from exc
    if not isinstance(new, dict):
        raise SiteError("site_config.json must be one JSON object, {...}")
    with open(path) as fh:
        old = json.load(fh)
    new = {k: old.get(k, v) if v == MASK else v for k, v in new.items()}
    if not new.get("db_name"):
        raise SiteError("db_name is required: without it the site cannot find its database")
    tmp = path + ".bench-wizard"
    with open(tmp, "w") as fh:
        json.dump(new, fh, indent=1)
    os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)


# Signs Administrator in the way Frappe's own login does, and prints the new session's id.
LOGIN = """import sys, frappe
from frappe.auth import CookieManager, LoginManager
frappe.init(site=sys.argv[1], sites_path=".")
frappe.connect()
frappe.utils.set_request(path="/")
frappe.local.cookie_manager = CookieManager()
frappe.local.login_manager = LoginManager()
frappe.local.login_manager.login_as("Administrator")
frappe.db.commit()
sys.stdout.write("\\n" + frappe.session.sid)
"""


def _resolves(host):
    if host == "localhost" or host.endswith(".localhost"):  # browsers resolve these themselves
        return True
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def check_host(site):
    """Raise SiteError when a browser on this machine cannot open the site by its name."""
    if not _resolves(site):
        raise SiteError(f"{site} does not resolve on this machine, so a browser cannot open it. Add "
                        f"\"127.0.0.1 {site}\" to /etc/hosts, or use a site name that ends in .localhost.")


def login_url(folder, site, port, env=None, run=subprocess.run):
    """A URL that opens the site's desk signed in as Administrator. Needs the bench running (sessions live
    in its Redis)."""
    _site_dir(folder, site)
    check_host(site)
    try:
        result = run([os.path.join(folder, "env", "bin", "python"), "-c", LOGIN, site], cwd=os.path.join(folder, "sites"),
                     env=env, capture_output=True, text=True, errors="replace", timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SiteError(f"could not sign in: {exc}") from exc
    sid = (result.stdout or "").strip().splitlines()[-1:] or [""]
    if result.returncode != 0 or not re.fullmatch(r"[A-Za-z0-9._-]{8,}", sid[0]):
        lines = [l for l in (result.stderr or "").strip().splitlines() if l.strip()]
        raise SiteError("could not sign in" + (f": {lines[-1]}" if lines else ""))
    return f"http://{site}:{port}/app?sid={sid[0]}"


# (kind, what it usually means, signatures), most specific first.
FAILURES = [
    ("Text where a number goes", "a column now meant to hold numbers still has text in some rows; fix those rows, "
     "then migrate again", ("Incorrect integer value", "Incorrect decimal value", "Truncated incorrect")),
    ("Value too long", "some rows hold a value longer than the column's new size allows",
     ("Data truncated for column", "Data too long for column")),
    ("Missing column", "the patch reads a column the table does not have yet; a patch of another app may have to "
     "run first, or a doctype was not synced", ("Unknown column",)),
    ("Duplicate rows", "a unique index cannot be added because some rows repeat; remove the duplicates first",
     ("Duplicate entry",)),
    ("Missing table", "the patch needs a table that does not exist; a doctype it depends on was not created",
     ("doesn't exist",)),
]


def diagnose(output):
    """{patch, kind, hint, error} for a failed migrate's output; None when the output names no patch or error."""
    patches = re.findall(r"^\s*Executing (\S+) in ", output or "", re.M)
    errors = re.findall(r"^\s*(?:[\w.]+\.)?\w*(?:Error|Exception)\b: ?.*$", output or "", re.M)
    kind, hint = next(((k, h) for k, h, sigs in FAILURES if any(s in (output or "") for s in sigs)), ("", ""))
    if not (patches or errors or kind):
        return None
    return {"patch": patches[-1] if patches else "", "kind": kind, "hint": hint,
            "error": errors[-1].strip()[:500] if errors else ""}
