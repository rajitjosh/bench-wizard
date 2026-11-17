import json
import subprocess
import tempfile
import unittest

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
