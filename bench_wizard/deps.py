"""Read what a Frappe app requires from its repository, and resolve a selection."""
import ast
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .gitauth import scrub
from .graph import CycleError, toposort


@dataclass
class AppInfo:
    app_name: str
    app_title: str
    app_description: str
    requires_python: str
    requires: dict  # required app -> version range, "" when unknown


@dataclass
class Resolution:
    order: list = field(default_factory=list)
    apps: dict = field(default_factory=dict)
    notices: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class InspectError(RuntimeError):
    pass


class NotAFrappeApp(InspectError):
    pass


_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_PAIR = re.compile(r"""^\s*([A-Za-z0-9_.-]+|"[^"]+")\s*=\s*(["'])(.*?)\2\s*(?:#.*)?$""")


def read_pyproject(text):
    """Read the few values we need. Python 3.10 has no tomllib."""
    out = {"name": "", "requires_python": "", "deps": {}}
    section = ""
    for line in text.splitlines():
        header = _SECTION.match(line)
        if header:
            section = header.group(1).strip()
            continue
        pair = _PAIR.match(line)
        if not pair:
            continue
        key, value = pair.group(1).strip('"'), pair.group(3)
        if section == "project" and key == "name":
            out["name"] = value
        elif section == "project" and key == "requires-python":
            out["requires_python"] = value
        elif section == "tool.bench.frappe-dependencies":
            out["deps"][key] = value
    return out


def read_hooks(text):
    """Read literal assignments from hooks.py without executing it."""
    out = {"app_name": "", "app_title": "", "app_description": "", "required_apps": []}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in out):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        name = node.targets[0].id
        if name == "required_apps":
            if isinstance(value, (list, tuple)):
                out[name] = [str(v).rsplit("/", 1)[-1] for v in value]
        elif isinstance(value, str):
            out[name] = value
    return out


_CLAUSE = re.compile(r"^(>=|<=|==|~=|!=|>|<)\s*(\d+(?:\.\d+)*)")


def _clauses(range_):
    for part in (range_ or "").split(","):
        match = _CLAUSE.match(part.strip())
        if match:
            yield match.group(1), tuple(int(n) for n in match.group(2).split("."))


def admits_major(range_, major):
    """True if some release with this major version can satisfy the range."""
    for op, version in _clauses(range_):
        first, rest = version[0], any(version[1:])
        if op in (">=", ">") and major < first:
            return False
        if op == "<" and not (major < first or (major == first and rest)):
            return False
        if op == "<=" and major > first:
            return False
        if op in ("==", "~=") and major != first:
            return False
    return True


def admits_version(range_, version):
    for op, bound in _clauses(range_):
        exact = version[:len(bound)]
        width = max(len(version), len(bound))
        version = tuple(version) + (0,) * (width - len(version))
        bound_padded = bound + (0,) * (width - len(bound))
        if op == ">=" and not version >= bound_padded:
            return False
        if op == ">" and not version > bound_padded:
            return False
        if op == "<" and not version < bound_padded:
            return False
        if op == "<=" and not version <= bound_padded:
            return False
        if op == "==" and exact != bound:
            return False
        if op == "~=" and not (version >= bound_padded and exact[:-1] == bound[:-1]):
            return False
    return True


def supported_majors(range_):
    return [major for major in range(10, 31) if admits_major(range_, major)]


def _git(args, cwd=None, timeout=120, env=None):
    env = dict(os.environ, **(env or {}), GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise InspectError(f"git {args[0]} timed out") from exc
    except OSError as exc:
        raise InspectError(f"cannot run git: {exc}") from exc


def _show(repo_dir, path, env=None):
    result = _git(["show", f"HEAD:{path}"], cwd=repo_dir, env=env)
    return result.stdout if result.returncode == 0 else None


def inspect(repo, branch, env=None):
    """Read pyproject.toml and hooks.py from a repo without checking it out.

    `branch` may be a branch or a tag. `env` carries Git credentials, if any.
    """
    with tempfile.TemporaryDirectory(prefix="bench-wizard-") as tmp:
        clone = os.path.join(tmp, "r")
        result = _git(["clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout",
                       "--branch", branch, repo, clone], env=env)
        if result.returncode != 0:
            lines = [l for l in result.stderr.strip().splitlines() if not l.startswith("warning:")]
            raise InspectError(scrub(lines[-1]) if lines else "git clone failed")
        files = _git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=clone, env=env).stdout.splitlines()
        hooks_dirs = [f.split("/")[0] for f in files if re.fullmatch(r"[^/]+/hooks\.py", f)]

        project = {"name": "", "requires_python": "", "deps": {}}
        if "pyproject.toml" in files:
            project = read_pyproject(_show(clone, "pyproject.toml", env) or "")
        if not hooks_dirs and not project["name"]:
            raise NotAFrappeApp("not a Frappe app: no pyproject.toml or hooks.py found")

        folder = ""
        for candidate in (project["name"], project["name"].replace("-", "_")):
            if candidate in hooks_dirs:
                folder = candidate
                break
        if not folder and len(hooks_dirs) == 1:
            folder = hooks_dirs[0]
        hooks = read_hooks(_show(clone, f"{folder}/hooks.py", env) or "") if folder else read_hooks("")

    name = hooks["app_name"] or folder or project["name"].replace("-", "_")
    requires = dict(project["deps"])
    for required in hooks["required_apps"]:
        requires.setdefault(required, "")
    requires.pop(name, None)
    return AppInfo(name, hooks["app_title"] or name, hooks["app_description"],
                   project["requires_python"], requires)


def _branch_major(branch):
    """The major version a ref names: `version-15` or a tag such as `v15.115.0`."""
    match = re.fullmatch(r"version-(\d+)|v(\d+)\.\d+.*", branch or "")
    return int(match.group(1) or match.group(2)) if match else None


def _names(names):
    return ", ".join(sorted(names)) or "nothing"


def resolve(selected, catalog, version, inspect_fn=inspect):
    """Follow each selected app's declared requirements and work out the install order.

    `selected` maps app name to {"repo", "branch", "title", "added_for"}; frappe is implied.
    """
    res = Resolution(apps={name: dict(app) for name, app in selected.items() if name != "frappe"})
    profile = catalog["frappe_versions"][version]
    version_major = _branch_major(version)
    python = tuple(int(n) for n in profile["python"].split("."))
    graph, infos = {}, {}

    def safe_inspect(name):
        app = res.apps[name]
        try:
            return inspect_fn(app["repo"], app["branch"])
        except InspectError as exc:
            return exc

    wave = list(res.apps)
    while wave:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(safe_inspect, wave))
        next_wave = []
        for name, info in zip(wave, results):
            app = res.apps[name]
            title = app["title"]
            listed = set(catalog["apps"].get(name, {}).get("requires", [])) - {"frappe"}
            if isinstance(info, Exception):
                res.errors.append(f"{title}: could not read its requirements ({info})")
                graph[name] = listed
                continue
            infos[name] = info
            declared = set(info.requires) - {"frappe", name}
            if name in catalog["apps"] and declared != listed:
                res.notices.append(f"{title}: the catalog lists {_names(listed)} as required, "
                                   f"but the app declares {_names(declared)}. Both are used.")
            graph[name] = declared | listed

            frappe_range = info.requires.get("frappe", "")
            if version_major is not None and not admits_major(frappe_range, version_major):
                majors = ", ".join(str(m) for m in supported_majors(frappe_range)) or frappe_range
                res.errors.append(f"{title} branch {app['branch']} supports Frappe {majors}, "
                                  f"not {version_major}")
            if not admits_version(info.requires_python, python):
                res.errors.append(f"{title} branch {app['branch']} needs Python {info.requires_python}, "
                                  f"but {profile['title']} uses Python {profile['python']}")

            for required in sorted(graph[name]):
                if required in res.apps:
                    continue
                known = catalog["apps"].get(required)
                if known is None:
                    res.errors.append(f"{title} needs {required}, which is not in the catalog; "
                                      f"add it as a custom app")
                elif version not in known["branches"]:
                    res.errors.append(f"{title} needs {known['title']}, which is not available "
                                      f"for {profile['title']}")
                else:
                    res.apps[required] = {"repo": known["repo"], "branch": known["branches"][version],
                                          "title": known["title"], "added_for": title}
                    next_wave.append(required)
        wave = next_wave

    for name, info in infos.items():
        for required, range_ in info.requires.items():
            if required == "frappe" or required not in res.apps:
                continue
            major = _branch_major(res.apps[required]["branch"])
            if major is not None and not admits_major(range_, major):
                res.errors.append(
                    f"{res.apps[name]['title']} branch {res.apps[name]['branch']} needs "
                    f"{res.apps[required]['title']} {range_}, but branch "
                    f"{res.apps[required]['branch']} is selected")

    names = list(res.apps)  # the order the apps were given in decides ties
    try:
        res.order = toposort(names, graph)
    except CycleError as exc:
        res.errors.append(str(exc))
        res.order = names
    return res
