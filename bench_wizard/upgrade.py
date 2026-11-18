"""Move a whole bench to another Frappe version: pick each app's branch for it, and check what the bench must change."""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import deps, sites

GUESSES = ("main", "master", "develop")  # where apps without version branches (Helpdesk, CRM, ...) keep their code


def pick_branch(repo, current, target, major, names, inspect_fn):
    """(branch, why) for an app the catalog does not know. Each likely branch's pyproject.toml says which Frappe it
    supports; the first that takes `major` wins. branch is "" when none does."""
    tried, unstated = [], ""
    for branch in dict.fromkeys(b for b in (target, *GUESSES, current) if b in names):
        try:
            info = inspect_fn(repo, branch)
        except deps.InspectError as exc:
            tried.append(f"{branch}: {exc}")
            continue
        declared = info.requires.get("frappe", "")
        if not declared:
            if branch == target:
                return branch, f"named for {target}; declares no Frappe range"
            unstated = unstated or branch
        elif deps.admits_major(declared, major):
            return branch, f"declares frappe {declared}"
        else:
            tried.append(f"{branch} declares frappe {declared}")
    if unstated:
        return unstated, "declares no Frappe range; picked by its name, so check it"
    return "", f"no branch supports Frappe {major}" + (f" ({'; '.join(tried)})" if tried else "")


def env_python(folder):
    """The bench's own Python as (3, 11, 9), or None."""
    try:
        out = subprocess.run([os.path.join(folder, "env", "bin", "python"), "-c",
                              "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                             capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return tuple(int(n) for n in out.split(".")) if out else None


def check(folder, target, catalog, inspect_fn, branches_fn, resolve_fn=deps.resolve):
    """What moving the bench to `target` (e.g. "version-16") takes: a branch per app and why, apps to add, errors,
    and whether the bench's Python can run it. `branches_fn(repo)` lists a repository's branches."""
    major = deps._branch_major(target)
    if major is None or target not in catalog["frappe_versions"]:
        raise ValueError(f"unknown Frappe version: {target}")
    errors = []

    def one(app):
        row = {"name": app["name"], "now": app["branch"], "branch": "", "repo": app["remote"], "why": "",
               "changed": app["changed"]}
        known = catalog["apps"].get(app["name"])
        if app["error"]:
            return dict(row, why=app["error"])
        if known:
            branch = known["branches"].get(target, "")
            return dict(row, branch=branch, repo=app["remote"] or known["repo"],
                        why="its branch for this version" if branch else f"not available for {target}")
        if not app["remote"]:
            return dict(row, branch=app["branch"], why="has no remote, so it stays on its branch; check it yourself")
        try:
            names = branches_fn(app["remote"])
        except Exception as exc:  # a private or vanished repository: say so on its row
            return dict(row, why=f"cannot list its branches: {exc}")
        branch, why = pick_branch(app["remote"], app["branch"], target, major, names, inspect_fn)
        return dict(row, branch=branch, why=why)

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, sites.apps(folder)))
    errors += [f"{r['name']}: {r['why']}" for r in rows if not r["branch"]]

    on_bench = {r["name"] for r in rows}
    selected = {r["name"]: {"repo": r["repo"], "branch": r["branch"], "title": r["name"], "added_for": None}
                for r in rows if r["branch"] and r["repo"] and r["name"] != "frappe"}
    res = resolve_fn(selected, catalog, target, inspect_fn=inspect_fn,
                     present={r["name"] for r in rows if not r["repo"]})
    errors += res.errors

    frappe = catalog["apps"]["frappe"]
    try:
        needs = inspect_fn(frappe["repo"], frappe["branches"][target]).requires_python
    except deps.InspectError as exc:
        needs, errors = "", errors + [f"could not read which Python Frappe {major} needs: {exc}"]
    have = env_python(folder)
    profile = catalog["frappe_versions"][target]
    return {"version": target, "title": profile["title"], "apps": rows, "errors": errors, "notices": res.notices,
            "new_apps": [dict(res.apps[n], name=n) for n in res.order if n not in on_bench],
            "node": res.node or profile["node"],
            "python": {"env": ".".join(map(str, have)) if have else "", "needs": needs, "use": profile["python"],
                       "ok": bool(have) and deps.admits_version(needs, have)}}
