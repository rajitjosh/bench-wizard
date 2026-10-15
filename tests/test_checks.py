import tempfile
import unittest
from pathlib import Path

from bench_wizard import checks

V15 = {"title": "Version 15", "python": "3.11", "node": "18"}
V16 = {"title": "Version 16", "python": "3.14", "node": "24"}
PKGS = "".join(f"{p} install ok installed\n" for p in checks.BUILD_PACKAGES)

ALL_GOOD = {
    "dpkg-query": (0, PKGS),
    "python3.11": (0, "3.11.9 headers\n"),
    "mariadbd": (0, "mariadbd  Ver 10.6.23-MariaDB-0ubuntu0.22.04.1 for debian-linux-gnu on x86_64\n"),
    "my_print_defaults": (0, "--character-set-server=utf8mb4\n--collation-server=utf8mb4_unicode_ci\n"),
    "redis-server": (0, "Redis server v=6.0.16 sha=00000000:0 malloc=jemalloc-5.2.1 bits=64\n"),
    "yarn": (0, "1.22.22\n"),
    "wkhtmltopdf": (0, "wkhtmltopdf 0.12.6\n"),
    "bench": (0, "5.22.6\n"),
}


def fake_run(table):
    def run(argv, env=None):
        name = argv[0].rsplit("/", 1)[-1]
        if name == "python3.11" and "-c" not in argv:
            name = "python3.11 --version"
        return table.get(name, (127, ""))
    return run


class DetectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.node("v18.20.7")

    def node(self, version):
        d = self.home / ".nvm/versions/node" / version / "bin"
        d.mkdir(parents=True)
        (d / "node").write_text("")

    def detect(self, table=None, profile=V15, **changes):
        table = dict(ALL_GOOD if table is None else table, **changes)
        rows = checks.detect_all(profile, run=fake_run(table), home=str(self.home), system="linux")
        return {r["id"]: r for r in rows}

    def test_everything_present(self):
        rows = self.detect()
        self.assertEqual({r["status"] for r in rows.values()}, {"ok"})
        self.assertEqual(list(rows), ["build", "python", "mariadb", "charset", "redis", "node", "yarn",
                                      "wkhtmltopdf", "bench"])
        self.assertEqual(rows["python"]["found"], "3.11.9")
        self.assertEqual(rows["mariadb"]["found"], "10.6.23")
        self.assertEqual(rows["redis"]["found"], "6.0.16")
        self.assertEqual(rows["node"]["found"], "18.20.7")
        self.assertEqual(rows["bench"]["found"], "5.22.6")
        self.assertEqual(rows["python"]["required"], "3.11")
        self.assertEqual(rows["node"]["required"], "18")

    def test_nothing_present(self):
        rows = checks.detect_all(V16, run=fake_run({}), home=str(self.home / "empty"), system="linux")
        self.assertEqual({r["status"] for r in rows}, {"missing"})
        self.assertTrue(all(r["found"] == "" for r in rows))

    def test_missing_build_package_is_named(self):
        out = PKGS.replace("libffi-dev install ok installed\n", "libffi-dev deinstall ok config-files\n")
        row = self.detect(**{"dpkg-query": (1, out)})["build"]
        self.assertEqual(row["status"], "missing")
        self.assertIn("libffi-dev", row["found"])

    def test_python_without_venv_needs_fix(self):
        rows = self.detect(**{"python3.11": (1, "ModuleNotFoundError: ensurepip"),
                              "python3.11 --version": (0, "Python 3.11.9\n")})
        self.assertEqual(rows["python"]["status"], "needs_fix")
        self.assertIn("3.11.9", rows["python"]["found"])

    def test_python_without_dev_headers_needs_fix(self):
        # apps with compiled dependencies (mysqlclient) fail to install without Python.h
        rows = self.detect(**{"python3.11": (0, "3.11.9 no-headers\n")})
        self.assertEqual(rows["python"]["status"], "needs_fix")
        self.assertEqual(rows["python"]["found"], "3.11.9, development headers missing")

    def test_version_16_needs_its_own_python_and_node(self):
        rows = self.detect(profile=V16)
        self.assertEqual(rows["python"]["status"], "missing")
        self.assertEqual(rows["node"]["status"], "missing")
        self.assertEqual(rows["yarn"]["status"], "missing")
        self.assertEqual(rows["python"]["required"], "3.14")

    def test_old_mariadb_needs_fix(self):
        row = self.detect(mariadbd=(0, "mariadbd  Ver 10.3.39-MariaDB-0ubuntu0.20.04.2\n"))["mariadb"]
        self.assertEqual((row["status"], row["found"]), ("needs_fix", "10.3.39"))

    def test_new_mariadb_version_format(self):
        row = self.detect(mariadbd=(0, "mariadbd from 11.8.2-MariaDB, client 15.2 for debian-linux-gnu\n"))
        self.assertEqual(row["mariadb"]["found"], "11.8.2")
        self.assertEqual(row["mariadb"]["status"], "ok")

    def test_charset_later_option_overrides_earlier(self):
        out = ("--character-set-server=latin1\n--collation_server=latin1_swedish_ci\n"
               "--character_set_server=utf8mb4\n--collation-server=utf8mb4_unicode_ci\n")
        self.assertEqual(self.detect(my_print_defaults=(0, out))["charset"]["status"], "ok")

    def test_charset_wrong_collation_needs_fix(self):
        out = "--character-set-server=utf8mb4\n--collation-server=utf8mb4_general_ci\n"
        row = self.detect(my_print_defaults=(0, out))["charset"]
        self.assertEqual(row["status"], "needs_fix")
        self.assertIn("utf8mb4_general_ci", row["found"])

    def test_charset_unset_needs_fix(self):
        row = self.detect(my_print_defaults=(0, "--socket=/run/mysqld/mysqld.sock\n"))["charset"]
        self.assertEqual(row["status"], "needs_fix")

    def test_yarn_two_needs_fix(self):
        self.assertEqual(self.detect(yarn=(0, "4.1.0\n"))["yarn"]["status"], "needs_fix")


class NodeBinTest(unittest.TestCase):
    def test_picks_highest_patch_of_the_major(self):
        with tempfile.TemporaryDirectory() as home:
            for v in ("v18.9.0", "v18.20.7", "v18.20.10", "v20.1.0", "v180.0.0"):
                d = Path(home) / ".nvm/versions/node" / v / "bin"
                d.mkdir(parents=True)
                (d / "node").write_text("")
            self.assertTrue(checks.node_bin("18", home).endswith("v18.20.10/bin"))
            self.assertIsNone(checks.node_bin("24", home))

    def test_build_env_puts_node_and_local_bin_first(self):
        with tempfile.TemporaryDirectory() as home:
            d = Path(home) / ".nvm/versions/node/v18.1.0/bin"
            d.mkdir(parents=True)
            (d / "node").write_text("")
            env = checks.build_env("18", home, base={"PATH": "/usr/bin", "X": "1"}, system="linux")
            self.assertEqual(env["PATH"].split(":")[:3], [checks.SHIMS_DIR, str(d), f"{home}/.local/bin"])
            self.assertTrue(env["PATH"].endswith("/usr/bin"))
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(env["X"], "1")


class ShimTest(unittest.TestCase):
    def test_bench_commands_see_a_supervisorctl_that_does_nothing(self):
        # bench calls `sudo supervisorctl` during init when Supervisor is installed;
        # a local-development bench must never touch it.
        import shutil
        import subprocess
        env = checks.build_env("18", "/nonexistent", base={"PATH": "/usr/bin:/bin"})
        found = shutil.which("supervisorctl", path=env["PATH"])
        self.assertEqual(found, checks.SHIMS_DIR + "/supervisorctl")
        result = subprocess.run([found, "status"], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, ""))


class InstallCommandsTest(unittest.TestCase):
    def cmds(self, check_id, profile=V15, ubuntu="22.04"):
        return checks.install_commands(check_id, profile, home="/home/u", ubuntu=ubuntu, system="linux")

    def test_python_uses_deadsnakes_for_the_profile_version(self):
        cmds = self.cmds("python", V16)
        self.assertTrue(all(c["sudo"] for c in cmds))
        self.assertIn("ppa:deadsnakes/ppa", cmds[0]["argv"])
        self.assertEqual(cmds[-1]["argv"][-4:], ["python3.14", "python3.14-dev", "python3.14-venv", "python3-pip"])

    def test_apt_runs_non_interactively(self):
        argv = self.cmds("redis")[0]["argv"]
        self.assertEqual(argv[:2], ["env", "DEBIAN_FRONTEND=noninteractive"])
        self.assertIn("redis-server", argv)

    def test_build_installs_add_apt_repository(self):
        self.assertIn("software-properties-common", self.cmds("build")[0]["argv"])

    def test_charset_writes_config_then_restarts(self):
        cmds = self.cmds("charset")
        self.assertEqual(cmds[0]["argv"], ["tee", "/etc/mysql/mariadb.conf.d/99-frappe.cnf"])
        self.assertIn("collation-server = utf8mb4_unicode_ci", cmds[0]["input"])
        self.assertNotIn("innodb", cmds[0]["input"])
        self.assertEqual(cmds[1]["argv"], ["systemctl", "restart", "mariadb"])

    def test_node_installs_as_user_with_profile_version(self):
        cmds = self.cmds("node", V16)
        self.assertFalse(cmds[0]["sudo"])
        self.assertIn("nvm install 24", cmds[0]["argv"][-1])

    def test_bench_uses_pip_on_22_04_and_pipx_later(self):
        self.assertEqual(self.cmds("bench")[-1]["argv"], ["pip3", "install", "--user", "frappe-bench"])
        later = self.cmds("bench", ubuntu="24.04")
        self.assertTrue(later[0]["sudo"])
        self.assertIn("pipx", later[0]["argv"])
        self.assertEqual(later[-1]["argv"], ["pipx", "install", "frappe-bench"])
        self.assertFalse(later[-1]["sudo"])

    def test_unknown_check(self):
        with self.assertRaises(KeyError):
            self.cmds("nope")


MAC_GOOD = dict(ALL_GOOD, **{
    "xcode-select": (0, "/Library/Developer/CommandLineTools\n"),
    "brew": (0, "Homebrew 4.6.0\n"),
    "git": (0, "git version 2.50.1\n"),
    "pkg-config": (0, "0.29.2\n"),
    "mariadbd": (0, "mariadbd  Ver 11.8.2-MariaDB for osx10.20 on arm64 (Homebrew)\n"),
})
del MAC_GOOD["dpkg-query"]


class MacDetectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / ".nvm/versions/node/v18.20.7/bin"
        d.mkdir(parents=True)
        (d / "node").write_text("")
        self.home = tmp.name

    def detect(self, **changes):
        table = dict(MAC_GOOD, **changes)
        rows = checks.detect_all(V15, run=fake_run(table), home=self.home, system="darwin")
        return {r["id"]: r for r in rows}

    def test_everything_present_without_dpkg(self):
        rows = self.detect()
        self.assertEqual({r["status"] for r in rows.values()}, {"ok"})
        self.assertEqual(rows["build"]["title"], "Homebrew and build tools")
        self.assertEqual(rows["mariadb"]["found"], "11.8.2")

    def test_missing_homebrew_is_named(self):
        row = self.detect(brew=(127, ""))["build"]
        self.assertEqual((row["status"], row["found"]), ("missing", "missing: Homebrew"))

    def test_brew_directories_are_on_path_for_apps_started_from_finder(self):
        env = checks.build_env("18", self.home, base={"PATH": "/usr/bin:/bin"}, system="darwin")
        self.assertIn("/opt/homebrew/bin", env["PATH"].split(":"))
        self.assertTrue(env["PATH"].startswith(checks.SHIMS_DIR))
        linux = checks.build_env("18", self.home, base={"PATH": "/usr/bin:/bin"}, system="linux")
        self.assertNotIn("/opt/homebrew/bin", linux["PATH"])


class MacInstallCommandsTest(unittest.TestCase):
    def cmds(self, check_id, profile=V15, arch="arm64"):
        return checks.install_commands(check_id, profile, system="darwin", arch=arch)

    def test_homebrew_never_runs_as_root(self):
        for check_id in ("build", "python", "mariadb", "charset", "redis", "node", "yarn", "bench"):
            self.assertFalse(any(c["sudo"] for c in self.cmds(check_id)), check_id)

    def test_python_uses_the_versioned_formula(self):
        self.assertEqual(self.cmds("python", V16)[0]["argv"], ["brew", "install", "python@3.14"])

    def test_mariadb_is_started_as_a_service(self):
        self.assertEqual(self.cmds("mariadb")[-1]["argv"], ["brew", "services", "start", "mariadb"])

    def test_charset_goes_to_homebrews_config_dir(self):
        cmds = self.cmds("charset")
        self.assertEqual(cmds[1]["argv"][0], "tee")
        self.assertTrue(cmds[1]["argv"][1].endswith("/etc/my.cnf.d/99-frappe.cnf"))
        self.assertIn("utf8mb4_unicode_ci", cmds[1]["input"])
        self.assertEqual(cmds[-1]["argv"], ["brew", "services", "restart", "mariadb"])

    def test_wkhtmltopdf_needs_rosetta_only_on_apple_silicon(self):
        arm = self.cmds("wkhtmltopdf")
        self.assertEqual(arm[0]["argv"][0], "softwareupdate")
        self.assertEqual(arm[-1]["argv"][:2], ["installer", "-pkg"])
        self.assertTrue(arm[-1]["sudo"])
        intel = self.cmds("wkhtmltopdf", arch="x86_64")
        self.assertEqual(intel[0]["argv"][0], "curl")

    def test_bench_uses_pipx(self):
        self.assertEqual(self.cmds("bench")[-1]["argv"], ["pipx", "install", "frappe-bench"])


class SqlTest(unittest.TestCase):
    def test_root_password_sql_escapes_quotes_and_backslashes(self):
        sql = checks.root_password_sql("a'b\\c")
        self.assertIn("'a\\'b\\\\c'", sql)
        self.assertIn("ALTER USER 'root'@'localhost'", sql)


class SudoTest(unittest.TestCase):
    def test_password_is_fed_on_stdin_when_sudo_asks_for_one(self):
        argv, stdin = checks.sudo_call(["tee", "/etc/x"], "pw", "content\n", passwordless=False)
        self.assertEqual(argv, ["sudo", "-S", "-k", "-p", "", "tee", "/etc/x"])
        self.assertEqual(stdin, "pw\ncontent\n")

    def test_password_never_reaches_the_command_when_sudo_needs_none(self):
        # with NOPASSWD, sudo does not read stdin, so a password line would land in the file
        argv, stdin = checks.sudo_call(["tee", "/etc/x"], "pw", "content\n", passwordless=True)
        self.assertEqual(argv, ["sudo", "-n", "tee", "/etc/x"])
        self.assertEqual(stdin, "content\n")

    def test_no_input_at_all_when_passwordless_and_command_takes_none(self):
        argv, stdin = checks.sudo_call(["systemctl", "restart", "mariadb"], "pw", None, passwordless=True)
        self.assertIsNone(stdin)
