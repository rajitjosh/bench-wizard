import unittest

from bench_wizard import gitauth

T = "ghp_exampletoken123"


class SplitTest(unittest.TestCase):
    def test_url_with_user_and_token(self):
        clean, cred = gitauth.split(f"https://alice:{T}@github.com/acme/app.git")
        self.assertEqual(clean, "https://github.com/acme/app.git")
        self.assertEqual(cred, {"prefix": "https://github.com/acme/", "userinfo": f"alice:{T}"})

    def test_url_with_token_only(self):
        clean, cred = gitauth.split(f"https://{T}@github.com/acme/app")
        self.assertEqual(clean, "https://github.com/acme/app")
        self.assertEqual(cred["userinfo"], T)

    def test_plain_url_has_no_credentials(self):
        self.assertEqual(gitauth.split("https://github.com/frappe/erpnext"),
                         ("https://github.com/frappe/erpnext", None))

    def test_ssh_and_local_paths_are_untouched(self):
        for url in ("git@github.com:acme/app.git", "ssh://git@github.com/acme/app.git", "/srv/repos/app"):
            self.assertEqual(gitauth.split(url), (url, None))

    def test_whitespace_is_trimmed(self):
        self.assertEqual(gitauth.split("  https://github.com/a/b.git \n")[0], "https://github.com/a/b.git")


class EnvTest(unittest.TestCase):
    def test_credentials_travel_in_the_environment_per_owner(self):
        creds = {"https://github.com/acme/": f"alice:{T}", "https://gitlab.com/x/": "bob:pw"}
        env = gitauth.env(creds)
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], f"url.https://alice:{T}@github.com/acme/.insteadOf")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "https://github.com/acme/")
        self.assertEqual(env["GIT_CONFIG_VALUE_1"], "https://gitlab.com/x/")

    def test_no_credentials_no_environment(self):
        self.assertEqual(gitauth.env({}), {})


class ScrubTest(unittest.TestCase):
    def test_scrub_removes_credentials_from_any_text(self):
        text = f"fatal: unable to access 'https://alice:{T}@github.com/acme/app.git/': 403"
        self.assertEqual(gitauth.scrub(text), "fatal: unable to access 'https://github.com/acme/app.git/': 403")

    def test_scrub_leaves_other_text_alone(self):
        self.assertEqual(gitauth.scrub("email me at a@b.com about https://x.org/y"),
                         "email me at a@b.com about https://x.org/y")
