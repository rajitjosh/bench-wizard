"""Credentials that arrive inside a Git URL (https://user:token@host/owner/repo).

They are taken out of the URL and handed to git through its environment, so they never
appear in a command line, a log, an API response or a cloned repo's .git/config.
"""
import re

_URL = re.compile(r"^(https?://)([^/@\s]+)@([^/\s]+)/(.*)$")
_USERINFO = re.compile(r"(https?://)[^/@\s'\"]+@")


def split(url):
    """Return (url without credentials, credentials or None)."""
    url = (url or "").strip()
    match = _URL.match(url)
    if not match:
        return url, None
    scheme, userinfo, host, path = match.groups()
    owner = path.split("/", 1)[0]
    return f"{scheme}{host}/{path}", {"prefix": f"{scheme}{host}/{owner}/", "userinfo": userinfo}


GITHUB_URLS = ("https://github.com/", "git@github.com:", "ssh://git@github.com/")
SSH_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def env(creds, github_ssh_host=None):
    """Environment that makes git use each credential for URLs under its prefix.

    `creds` maps a URL prefix (https://host/owner/) to "user:token". With `github_ssh_host`, an SSH host
    alias from ~/.ssh/config, every other github.com URL goes through that alias and so its key. A token
    still wins for its owner: git applies the longest matching prefix.
    """
    rules = []
    for prefix, userinfo in creds.items():
        scheme, rest = prefix.split("://", 1)
        rules.append((f"url.{scheme}://{userinfo}@{rest}.insteadOf", prefix))
    if github_ssh_host:
        rules += [(f"url.git@{github_ssh_host}:.insteadOf", url) for url in GITHUB_URLS]
    out = {}
    for i, (key, value) in enumerate(rules):
        out[f"GIT_CONFIG_KEY_{i}"] = key
        out[f"GIT_CONFIG_VALUE_{i}"] = value
    if out:
        out["GIT_CONFIG_COUNT"] = str(len(rules))
    return out


def scrub(text):
    """Remove credentials from any http(s) URL in a piece of text."""
    return _USERINFO.sub(r"\1", text or "")
