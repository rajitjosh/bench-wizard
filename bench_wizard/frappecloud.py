"""A Frappe Cloud site's backups, through Frappe Cloud's migration API, the way Frappe's Pilot reaches them.

The wizard asks for access to a site with a code it makes up; the site's owner approves the request on Frappe
Cloud, where the same code is shown. After that the token lists the site's backups and gives download links.
"""
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

SITE = "https://cloud.frappe.io"
DOMAIN = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I/L, so it reads the same when typed


class CloudError(Exception):
    pass


def make_code():
    while True:  # Frappe Cloud refuses a code of digits only
        code = "".join(secrets.choice(CODE_LETTERS) for _ in range(8))
        if not code.isdigit():
            return code


def cloud_url(url):
    """The Frappe Cloud a site lives on, as https://host[:port]: cloud.frappe.io, or an organisation's own Press."""
    url = (url or SITE).strip()
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.username:
        raise CloudError("the Frappe Cloud address must be https://, a host name only, such as https://cloud.frappe.io")
    return f"https://{parts.netloc.lower()}"


def call(method, token="", params=None, data=None, url=SITE, open_url=urllib.request.urlopen):
    """GET without `data`, POST with it; Frappe's own error message is raised as it is."""
    query = "?" + urllib.parse.urlencode(params) if params else ""
    request = urllib.request.Request(f"{url}/api/method/press.api.v1_migration.{method}{query}",
                                     data=urllib.parse.urlencode(data).encode() if data is not None else None,
                                     headers={"User-Agent": "bench-wizard"})  # Cloudflare turns urllib's away
    if token:
        request.add_header("X-Press-Migration-Token", token)
    try:
        with open_url(request, timeout=30) as resp:
            return json.loads(resp.read() or b"{}").get("message")
    except urllib.error.HTTPError as exc:
        raise CloudError(_message(exc)) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CloudError(f"cannot reach {url}: {exc}") from exc


def _message(exc):
    try:
        messages = json.loads(json.loads(exc.read() or b"{}").get("_server_messages") or "[]")
        return re.sub(r"<[^>]+>", "", json.loads(messages[0])["message"])
    except (ValueError, IndexError, KeyError, TypeError):
        return f"Frappe Cloud answered with HTTP {exc.code}"


def request_access(domain, url=SITE, **kw):
    """{token, code, approval_url, url}: the request the site's owner approves on Frappe Cloud at `url`."""
    url = cloud_url(url)
    domain = (domain or "").strip().lower()
    if not DOMAIN.fullmatch(domain):
        raise CloudError("enter the site's address, such as mysite.frappe.cloud")
    code = make_code()
    reply = call("request_access", data={"domain": domain, "code": code}, url=url, **kw) or {}
    if not reply.get("token"):
        raise CloudError("Frappe Cloud did not grant a request for this site")
    return {"token": reply["token"], "code": code, "approval_url": reply.get("approval_url", ""), "url": url}


def links(token, backup, url=SITE, **kw):
    """{kind: (file name, signed https URL)} of one backup's files; the name says which part a file holds."""
    found = {}
    for kind, link in (call("get_download_links", token, data={"backup": backup}, url=url, **kw) or {}).items():
        if link and urllib.parse.urlsplit(link).scheme == "https":
            found[kind] = (urllib.parse.unquote(urllib.parse.urlsplit(link).path.rsplit("/", 1)[-1]), link)
    if not found:
        raise CloudError("Frappe Cloud sent no download links for this backup")
    return found
