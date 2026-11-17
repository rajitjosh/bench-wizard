"""Apps from the Frappe Cloud marketplace: the published list, and one app's repository, versions and price.

The list comes from a public API method; the rest only from each app's public page, since the API that
returns an app's sources needs a Frappe Cloud login.
"""
import html as html_mod
import json
import re
import urllib.request

from . import deps

SITE = "https://cloud.frappe.io"
NAME = re.compile(r"[A-Za-z0-9_-]+")
# The page's "Source" link: exactly github.com/owner/repo, unlike its issues, licence and image links.
REPO_LINK = re.compile(r'href="https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?"')
FALLBACK_BRANCHES = ("main", "master", "develop")


class MarketplaceError(Exception):
    pass


def _get(path, timeout=20):
    request = urllib.request.Request(SITE + path, headers={"User-Agent": "bench-wizard"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except OSError as exc:
        raise MarketplaceError(f"cannot reach Frappe Cloud: {exc}") from exc


def apps(fetch=_get):
    """[{"name", "title"}] of every published app, by title."""
    try:
        items = json.loads(fetch("/api/method/press.api.marketplace.get_marketplace_apps"))["message"]
        found = [{"name": i["name"], "title": i.get("title") or i["name"]} for i in items if NAME.fullmatch(i["name"])]
    except (ValueError, KeyError, TypeError) as exc:
        raise MarketplaceError("Frappe Cloud sent an app list this wizard cannot read") from exc
    return sorted(found, key=lambda a: a["title"].lower())


def parse_page(page):
    """{"repo", "versions": ["version-15", ...], "nightly", "free"} from an app's marketplace page."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", page)
    tokens = [t for t in (html_mod.unescape(t).strip() for t in re.split(r"<[^>]+>", text)) if t]

    def after(label, stop=None):
        if label not in tokens:
            return []
        i = tokens.index(label) + 1
        end = tokens.index(stop, i) if stop in tokens[i:] else i + 1
        return tokens[i:end]

    versions = ",".join(after("Supported versions"))
    categories = after("Categories", "Resources")
    repos = REPO_LINK.findall(text)
    return {"repo": f"https://github.com/{repos[0]}.git" if repos else "",
            "versions": [f"version-{n}" for n in re.findall(r"Version (\d+)", versions)],
            "nightly": "Nightly" in versions,
            "free": "Paid" not in categories}


def app(name, fetch=_get):
    if not NAME.fullmatch(name or ""):
        raise MarketplaceError(f"not a marketplace app name: {name!r}")
    return dict(parse_page(fetch(f"/marketplace/apps/{name}")), name=name)


def branches(repo, version, env=None):
    """The repository's branches, the one to suggest for `version` (e.g. "version-15") first."""
    result = deps._git(["ls-remote", "--heads", repo], timeout=30, env=env)
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines()
        if lines and "not found" in lines[-1]:
            raise MarketplaceError("the repository its marketplace page links to is private or has been removed; "
                                   "ask the publisher for access, or for where the app lives now")
        raise MarketplaceError(lines[-1] if lines else "git ls-remote failed")
    names = sorted(line.split("refs/heads/", 1)[1] for line in result.stdout.splitlines() if "refs/heads/" in line)
    pick = next((b for b in (version, *FALLBACK_BRANCHES) if b in names), names[0] if names else "")
    return [pick] + [b for b in names if b != pick] if pick else []
