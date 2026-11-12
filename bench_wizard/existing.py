"""Read a bench that already exists: its Frappe version, apps, sites and what each site has installed."""
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


class BenchError(ValueError):
    pass


@dataclass
class BenchInfo:
    folder: str
    version: str  # catalog key, e.g. "version-15"
    node: str  # Node major from .nvmrc, "" when there is none
    apps: list  # apps in the bench, in sites/apps.txt order
    sites: dict  # site -> {"apps": [installed apps]} or {"error": why they could not be read}


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def nvm_default_node(home=None):
    """Major of nvm's default Node, which a bench without .nvmrc has been running on; "" when unknown."""
    text = _read(os.path.join(home or os.path.expanduser("~"), ".nvm", "alias", "default")) or ""
    match = re.match(r"v?(\d+)", text.strip())  # "lts/*" or "node" stay unknown
    return match.group(1) if match else ""


def installed_apps(folder, site, env):
    """The apps installed on a site, read through the bench itself."""
    try:
        result = subprocess.run(["bench", "--site", site, "list-apps", "--format", "json"], cwd=folder, env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                                timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"bench list-apps did not run: {exc}"}
    out = result.stdout
    try:
        return {"apps": json.loads(out[out.index("{"):])[site]}
    except (ValueError, KeyError, TypeError):
        lines = (result.stderr or out).strip().splitlines()
        return {"error": lines[-1] if lines else f"bench list-apps exited with {result.returncode}"}


def inspect(folder, catalog, env):
    folder = os.path.abspath(os.path.expanduser(folder))
    text = _read(os.path.join(folder, "apps", "frappe", "frappe", "__init__.py"))
    if text is None or not os.path.isdir(os.path.join(folder, "sites")):
        raise BenchError(f"{folder} is not a bench: apps/frappe or sites is missing")
    match = re.search(r"""__version__\s*=\s*["'](\d+)\.""", text)
    version = f"version-{match.group(1)}" if match else ""
    if version not in catalog["frappe_versions"]:
        found = f"Frappe {match.group(1)}" if match else "an unknown Frappe version"
        supported = ", ".join(v["title"] for v in catalog["frappe_versions"].values())
        raise BenchError(f"this bench runs {found}; the wizard supports {supported}")
    node = re.match(r"v?(\d+)", (_read(os.path.join(folder, ".nvmrc")) or "").strip())
    apps = (_read(os.path.join(folder, "sites", "apps.txt")) or "").split()
    sites_dir = os.path.join(folder, "sites")
    names = sorted(d for d in os.listdir(sites_dir) if os.path.isfile(os.path.join(sites_dir, d, "site_config.json")))
    if not names:
        raise BenchError(f"{folder} has no sites")
    with ThreadPoolExecutor(max_workers=4) as pool:
        sites = dict(zip(names, pool.map(lambda site: installed_apps(folder, site, env), names)))
    return BenchInfo(folder, version, node.group(1) if node else "", apps, sites)
