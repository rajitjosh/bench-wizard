import json
import subprocess
import tempfile
import ssl
import unittest
import urllib.error
from unittest import mock

from bench_wizard import marketplace

PAGE = """<html><head><script>var x = "Supported versions";</script></head><body>
<a href="https://github.com/frappe">Frappe</a><img src="https://github.com/acme/tool/raw/develop/x.png">
<p>Uses <a href="https://github.com/acme/other-lib/issues">other</a></p>
<div><p>Supported versions</p></div><div><p>Version 16, Version 15, Nightly</p></div>
<div><p>Categories</p></div><a><p>Paid</p></a><a><p>Utilities</p></a><div><p>Resources</p></div>
<a href="https://github.com/acme/tool/issues">Issues</a><a href="https://github.com/acme/tool">Source</a>
</body></html>"""


class MarketplaceTest(unittest.TestCase):
    def test_parse_page(self):
        info = marketplace.parse_page(PAGE)
        self.assertEqual(info, {"repo": "https://github.com/acme/tool.git", "versions": ["version-16", "version-15"],
                                "nightly": True, "free": False})

    def test_free_app_and_page_without_repo(self):
        page = PAGE.replace("Paid", "Free").replace('href="https://github.com/acme/tool"', 'href="https://gitlab.com/x"')
        info = marketplace.parse_page(page)
        self.assertEqual((info["free"], info["repo"]), (True, ""))

    def test_apps_list_is_sorted_and_names_are_checked(self):
        body = json.dumps({"message": [{"name": "zeta", "title": "Zeta"}, {"name": "../x", "title": "Bad"},
                                       {"name": "alpha", "title": "alpha app"}]})
        self.assertEqual(marketplace.apps(fetch=lambda path: body),
                         [{"name": "alpha", "title": "alpha app"}, {"name": "zeta", "title": "Zeta"}])
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.apps(fetch=lambda path: "<html>")

    def test_app_rejects_bad_names(self):
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.app("../../api", fetch=lambda path: PAGE)
        self.assertEqual(marketplace.app("tool", fetch=lambda path: PAGE)["name"], "tool")

    def test_branches_suggest_the_version_branch_then_main(self):
        with tempfile.TemporaryDirectory() as repo:
            git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
            git("init", "-b", "main")
            git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-m", "x")
            git("branch", "version-15")
            git("branch", "feature")
            self.assertEqual(marketplace.branches(repo, "version-15"), ["version-15", "feature", "main"])
            self.assertEqual(marketplace.branches(repo, "version-16")[0], "main")
            with self.assertRaises(marketplace.MarketplaceError):
                marketplace.branches(repo + "/missing", "version-15")

    def test_missing_repository_is_explained(self):
        from unittest import mock
        gone = subprocess.CompletedProcess([], 128, "", "fatal: repository 'https://github.com/x/y.git/' not found\n")
        with mock.patch.object(marketplace.deps, "_git", return_value=gone):
            with self.assertRaisesRegex(marketplace.MarketplaceError, "private or has been removed"):
                marketplace.branches("https://github.com/x/y.git", "version-15")

    def test_certificate_error_falls_back_to_curl(self):
        # python.org Python on macOS has no CA certificates until "Install Certificates" runs
        cert = urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))
        done = subprocess.CompletedProcess([], 0, b'{"message": []}', b"")
        with mock.patch.object(marketplace.urllib.request, "urlopen", side_effect=cert), \
                mock.patch.object(marketplace.subprocess, "run", return_value=done) as run:
            self.assertEqual(marketplace._get("/x"), '{"message": []}')
        self.assertEqual(run.call_args[0][0][0], "curl")
        self.assertEqual(run.call_args[0][0][-1], marketplace.SITE + "/x")
        failed = subprocess.CompletedProcess([], 6, b"", b"curl: (6) Could not resolve host")
        with mock.patch.object(marketplace.urllib.request, "urlopen", side_effect=cert), \
                mock.patch.object(marketplace.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(marketplace.MarketplaceError, "Could not resolve host"):
                marketplace._get("/x")

    def test_other_network_errors_do_not_try_curl(self):
        with mock.patch.object(marketplace.urllib.request, "urlopen", side_effect=urllib.error.URLError("timed out")), \
                mock.patch.object(marketplace.subprocess, "run") as run:
            with self.assertRaisesRegex(marketplace.MarketplaceError, "cannot reach Frappe Cloud"):
                marketplace._get("/x")
        run.assert_not_called()
