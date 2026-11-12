import subprocess
import tempfile
import unittest
from pathlib import Path

from bench_wizard import deps

PYPROJECT = '''[project]
name = "lending"
requires-python = ">=3.10"
dependencies = [
    "numpy",  # not a frappe dependency
]

[tool.bench.frappe-dependencies]
# comment line
frappe = ">=15.0.0,<16.0.0"
erpnext = '>=15.0.0,<16.0.0'  # trailing comment

[tool.bench.dev-dependencies]
ignored = "1.0"
'''


def make_repo(root, name, files, branch="version-15"):
    repo = Path(root) / name
    repo.mkdir()
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    run("init", "-q", "-b", branch)
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return str(repo)


class ReadPyprojectTest(unittest.TestCase):
    def test_reads_name_python_and_frappe_dependencies(self):
        got = deps.read_pyproject(PYPROJECT)
        self.assertEqual(got["name"], "lending")
        self.assertEqual(got["requires_python"], ">=3.10")
        self.assertEqual(got["deps"], {"frappe": ">=15.0.0,<16.0.0", "erpnext": ">=15.0.0,<16.0.0"})

    def test_missing_tables(self):
        got = deps.read_pyproject("[build-system]\nrequires = ['flit']\n")
        self.assertEqual(got, {"name": "", "requires_python": "", "deps": {}})


class ReadHooksTest(unittest.TestCase):
    def test_both_spellings_of_required_apps(self):
        got = deps.read_hooks('app_name = "x"\nrequired_apps = ["frappe/erpnext", "hrms"]\n')
        self.assertEqual(got["required_apps"], ["erpnext", "hrms"])
        self.assertEqual(got["app_name"], "x")

    def test_missing_required_apps(self):
        got = deps.read_hooks('app_title = "X App"\napp_description = "Does x"\n')
        self.assertEqual(got["required_apps"], [])
        self.assertEqual(got["app_title"], "X App")
        self.assertEqual(got["app_description"], "Does x")

    def test_code_is_not_executed(self):
        got = deps.read_hooks('import os\nraise SystemExit(1)\nrequired_apps = ["erpnext"]\n')
        self.assertEqual(got["required_apps"], ["erpnext"])

    def test_non_literal_value_is_skipped(self):
        got = deps.read_hooks('required_apps = get_apps()\napp_title = "T"\n')
        self.assertEqual(got["required_apps"], [])
        self.assertEqual(got["app_title"], "T")

    def test_syntax_error_gives_empty_result(self):
        self.assertEqual(deps.read_hooks("def (:")["required_apps"], [])


class RangeTest(unittest.TestCase):
    def test_admits_major(self):
        self.assertTrue(deps.admits_major(">=15.0.0,<16.0.0", 15))
        self.assertFalse(deps.admits_major(">=15.0.0,<16.0.0", 16))
        self.assertFalse(deps.admits_major(">=15.0.0,<16.0.0", 14))
        self.assertTrue(deps.admits_major(">=15.116.1,<17.0.0", 16))
        self.assertTrue(deps.admits_major(">=15.111.0,<16.0.0", 15))
        self.assertTrue(deps.admits_major("", 16))
        self.assertTrue(deps.admits_major("<16.5.0", 16))
        self.assertTrue(deps.admits_major("==15.*", 15))
        self.assertFalse(deps.admits_major("~=15.2", 16))

    def test_admits_version(self):
        self.assertTrue(deps.admits_version(">=3.10", (3, 11)))
        self.assertFalse(deps.admits_version(">=3.14", (3, 11)))
        self.assertTrue(deps.admits_version(">=3.14,<3.15", (3, 14)))
        self.assertFalse(deps.admits_version(">=3.10,<3.14", (3, 14)))
        self.assertTrue(deps.admits_version("", (3, 11)))
        self.assertTrue(deps.admits_version(">=3.11.0", (3, 11)))
        self.assertTrue(deps.admits_version("<=3.11.0", (3, 11)))
        self.assertFalse(deps.admits_version(">3.11.0", (3, 11)))
        self.assertTrue(deps.admits_version("==3.11", (3, 11, 4)))

    def test_supported_majors(self):
        self.assertEqual(deps.supported_majors(">=15.0.0,<16.0.0"), [15])
        self.assertEqual(deps.supported_majors(">=15.0.0,<17.0.0"), [15, 16])


class InspectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_normal_app(self):
        repo = make_repo(self.tmp.name, "lending", {
            "pyproject.toml": PYPROJECT,
            "lending/hooks.py": 'app_name = "lending"\napp_title = "Frappe Lending"\n'
                                'app_description = "Loans"\nrequired_apps = ["erpnext"]\n',
        })
        info = deps.inspect(repo, "version-15")
        self.assertEqual(info.app_name, "lending")
        self.assertEqual(info.app_title, "Frappe Lending")
        self.assertEqual(info.app_description, "Loans")
        self.assertEqual(info.requires_python, ">=3.10")
        self.assertEqual(info.requires, {"frappe": ">=15.0.0,<16.0.0", "erpnext": ">=15.0.0,<16.0.0"})

    def test_node_floor_from_nested_package_json(self):
        repo = make_repo(self.tmp.name, "fac", {
            "pyproject.toml": '[project]\nname = "fac"\n',
            "fac/hooks.py": 'app_name = "fac"\n',
            "package.json": '{"engines": {"node": ">=18"}}',
            "fac/chat/frontend/package.json": '{"engines": {"node": ">=22.12.0"}}',
            "fac/x/node_modules/dep/package.json": '{"engines": {"node": ">=30"}}',
        })
        self.assertEqual(deps.inspect(repo, "version-15").requires_node, 22)

    def test_node_floor(self):
        self.assertEqual(deps.node_floor(">=22.12.0"), 22)
        self.assertEqual(deps.node_floor("^18 || ^20"), 18)
        self.assertEqual(deps.node_floor(">=18 <23"), 18)
        self.assertEqual(deps.node_floor(""), 0)
        self.assertEqual(deps.read_node_floor("not json"), 0)
        self.assertEqual(deps.read_node_floor('{"engines": "x"}'), 0)

    def test_hooks_adds_apps_missing_from_pyproject(self):
        repo = make_repo(self.tmp.name, "ic", {
            "pyproject.toml": '[project]\nname = "india_compliance"\n'
                              '[tool.bench.frappe-dependencies]\nfrappe = ">=15.0.0,<16.0.0"\n',
            "india_compliance/hooks.py": 'required_apps = ["frappe/erpnext"]\n',
        })
        info = deps.inspect(repo, "version-15")
        self.assertEqual(info.requires, {"frappe": ">=15.0.0,<16.0.0", "erpnext": ""})

    def test_hyphenated_project_name(self):
        repo = make_repo(self.tmp.name, "hy", {
            "pyproject.toml": '[project]\nname = "my-app"\n',
            "my_app/hooks.py": 'app_title = "My App"\nrequired_apps = ["erpnext"]\n',
        })
        info = deps.inspect(repo, "version-15")
        self.assertEqual(info.app_name, "my_app")
        self.assertEqual(info.requires, {"erpnext": ""})

    def test_app_with_only_hooks(self):
        repo = make_repo(self.tmp.name, "old", {
            "notes.txt": "x",
            "old_app/hooks.py": 'app_name = "old_app"\nrequired_apps = ["erpnext"]\n',
            "extras/index.txt": "x",
        })
        info = deps.inspect(repo, "version-15")
        self.assertEqual(info.app_name, "old_app")
        self.assertEqual(info.app_title, "old_app")
        self.assertEqual(info.requires, {"erpnext": ""})

    def test_repo_with_neither_file(self):
        repo = make_repo(self.tmp.name, "plain", {"notes.txt": "x"})
        with self.assertRaises(deps.NotAFrappeApp):
            deps.inspect(repo, "version-15")

    def test_tag_can_be_inspected(self):
        repo = make_repo(self.tmp.name, "tagged", {
            "pyproject.toml": '[project]\nname = "tagged"\n', "tagged/hooks.py": 'app_title = "Tagged"\n'})
        subprocess.run(["git", "tag", "v1.2.3"], cwd=repo, check=True)
        self.assertEqual(deps.inspect(repo, "v1.2.3").app_title, "Tagged")

    def test_missing_branch(self):
        repo = make_repo(self.tmp.name, "b", {"notes.txt": "x"})
        with self.assertRaises(deps.InspectError):
            deps.inspect(repo, "version-99")


CATALOG = {
    "frappe_versions": {
        "version-15": {"title": "Version 15", "python": "3.11", "node": "18", "default": True},
        "version-16": {"title": "Version 16", "python": "3.14", "node": "24"},
    },
    "apps": {
        "frappe": {"title": "Frappe", "repo": "r/frappe", "requires": [],
                   "branches": {"version-15": "version-15", "version-16": "version-16"}},
        "erpnext": {"title": "ERPNext", "repo": "r/erpnext", "requires": ["frappe"],
                    "branches": {"version-15": "version-15", "version-16": "version-16"}},
        "lending": {"title": "Lending", "repo": "r/lending", "requires": ["frappe", "erpnext"],
                    "branches": {"version-15": "version-15", "version-16": "version-16"}},
        "pc": {"title": "Platform Commons", "repo": "r/pc", "requires": ["frappe", "erpnext"],
               "branches": {"version-15": "main"}},
    },
}


def info(name, requires=None, python="", node=0):
    return deps.AppInfo(name, name.title(), "", python, requires or {}, node)


def fake_inspect(table):
    def inspect(repo, branch):
        value = table[repo]
        if isinstance(value, Exception):
            raise value
        return value
    return inspect


def sel(name, version="version-15", **over):
    app = CATALOG["apps"][name]
    entry = {"repo": app["repo"], "branch": app["branches"][version], "title": app["title"], "added_for": None}
    entry.update(over)
    return entry


class ResolveTest(unittest.TestCase):
    def resolve(self, selected, table, version="version-15"):
        return deps.resolve(selected, CATALOG, version, inspect_fn=fake_inspect(table))

    def test_requirement_already_in_the_bench_is_not_fetched(self):
        res = deps.resolve({"lending": sel("lending")}, CATALOG, "version-15", present={"erpnext"},
                           inspect_fn=fake_inspect({"r/lending": info("lending", {"erpnext": ""})}))
        self.assertEqual((res.errors, res.order), ([], ["lending"]))

    def test_orders_by_declared_dependencies(self):
        res = self.resolve(
            {"lending": sel("lending"), "erpnext": sel("erpnext")},
            {"r/lending": info("lending", {"frappe": ">=15.0.0,<16.0.0", "erpnext": ">=15.0.0,<16.0.0"}),
             "r/erpnext": info("erpnext", {"frappe": ">=15.0.0,<16.0.0"})},
        )
        self.assertEqual(res.errors, [])
        self.assertEqual(res.order, ["erpnext", "lending"])

    def test_catalog_requires_used_when_app_declares_nothing(self):
        res = self.resolve(
            {"lending": sel("lending"), "erpnext": sel("erpnext")},
            {"r/lending": info("lending"), "r/erpnext": info("erpnext")},
        )
        self.assertEqual(res.order, ["erpnext", "lending"])
        self.assertTrue(any("Lending" in n for n in res.notices))

    def test_missing_dependency_is_added_from_catalog(self):
        res = self.resolve(
            {"lending": sel("lending")},
            {"r/lending": info("lending", {"erpnext": ""}), "r/erpnext": info("erpnext")},
        )
        self.assertEqual(res.errors, [])
        self.assertEqual(res.order, ["erpnext", "lending"])
        self.assertEqual(res.apps["erpnext"]["added_for"], "Lending")
        self.assertEqual(res.apps["erpnext"]["branch"], "version-15")

    def test_dependency_of_a_dependency_is_followed(self):
        custom = {"repo": "r/custom", "branch": "main", "title": "Custom", "added_for": None}
        res = self.resolve(
            {"custom": custom},
            {"r/custom": info("custom", {"lending": ""}),
             "r/lending": info("lending", {"erpnext": ""}),
             "r/erpnext": info("erpnext")},
        )
        self.assertEqual(res.errors, [])
        self.assertEqual(res.order, ["erpnext", "lending", "custom"])

    def test_dependency_not_available_for_version(self):
        custom = {"repo": "r/custom", "branch": "main", "title": "Custom", "added_for": None}
        res = self.resolve({"custom": custom}, {"r/custom": info("custom", {"pc": ""})}, "version-16")
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Platform Commons", res.errors[0])
        self.assertIn("Version 16", res.errors[0])

    def test_dependency_not_in_catalog(self):
        custom = {"repo": "r/custom", "branch": "main", "title": "Custom", "added_for": None}
        res = self.resolve({"custom": custom}, {"r/custom": info("custom", {"telephony": ""})})
        self.assertEqual(len(res.errors), 1)
        self.assertIn("telephony", res.errors[0])
        self.assertIn("custom app", res.errors[0])

    def test_frappe_range_mismatch(self):
        res = self.resolve(
            {"lending": sel("lending"), "erpnext": sel("erpnext", "version-16")},
            {"r/lending": info("lending", {"frappe": ">=15.0.0,<16.0.0", "erpnext": ""}),
             "r/erpnext": info("erpnext", {"frappe": ">=16.0.0,<17.0.0"})},
            "version-16",
        )
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Lending", res.errors[0])
        self.assertIn("supports Frappe 15, not 16", res.errors[0])

    def test_app_to_app_range_mismatch(self):
        res = self.resolve(
            {"lending": sel("lending", "version-16"), "erpnext": sel("erpnext")},
            {"r/lending": info("lending", {"erpnext": ">=16.0.0,<17.0.0"}),
             "r/erpnext": info("erpnext")},
        )
        self.assertEqual(len(res.errors), 1)
        self.assertIn("ERPNext", res.errors[0])

    def test_app_range_not_checked_for_unversioned_branch(self):
        res = self.resolve(
            {"lending": sel("lending"), "erpnext": sel("erpnext", branch="develop")},
            {"r/lending": info("lending", {"erpnext": ">=15.0.0,<16.0.0"}), "r/erpnext": info("erpnext")},
        )
        self.assertEqual(res.errors, [])

    def test_ties_keep_the_order_the_apps_were_given_in(self):
        # apps that declare nothing are installed in the order of the user's list
        custom = {"repo": "r/custom", "branch": "main", "title": "Custom", "added_for": None}
        res = self.resolve(
            {"custom": custom, "lending": sel("lending"), "erpnext": sel("erpnext")},
            {"r/custom": info("custom"), "r/lending": info("lending", {"erpnext": ""}), "r/erpnext": info("erpnext")},
        )
        self.assertEqual(res.order, ["custom", "erpnext", "lending"])

    def test_app_range_is_checked_against_a_version_tag(self):
        res = self.resolve(
            {"lending": sel("lending"), "erpnext": sel("erpnext", branch="v16.2.0")},
            {"r/lending": info("lending", {"erpnext": ">=15.0.0,<16.0.0"}), "r/erpnext": info("erpnext")},
        )
        self.assertEqual(len(res.errors), 1)
        ok = self.resolve(
            {"lending": sel("lending"), "erpnext": sel("erpnext", branch="v15.115.0")},
            {"r/lending": info("lending", {"erpnext": ">=15.0.0,<16.0.0"}), "r/erpnext": info("erpnext")},
        )
        self.assertEqual(ok.errors, [])

    def test_python_mismatch(self):
        res = self.resolve({"erpnext": sel("erpnext")}, {"r/erpnext": info("erpnext", python=">=3.14")})
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Python", res.errors[0])

    def test_cycle(self):
        a = {"repo": "r/a", "branch": "main", "title": "A", "added_for": None}
        b = {"repo": "r/b", "branch": "main", "title": "B", "added_for": None}
        res = self.resolve({"a": a, "b": b}, {"r/a": info("a", {"b": ""}), "r/b": info("b", {"a": ""})})
        self.assertTrue(any("cycle" in e for e in res.errors))

    def test_node_is_raised_to_what_an_app_needs(self):
        res = self.resolve({"lending": sel("lending"), "erpnext": sel("erpnext")},
                           {"r/lending": info("lending", node=22), "r/erpnext": info("erpnext", node=20)})
        self.assertEqual(res.node, "22")
        self.assertTrue(any("Node 22 instead of 18" in n for n in res.notices))

    def test_node_stays_when_apps_need_no_newer_one(self):
        res = self.resolve({"erpnext": sel("erpnext")}, {"r/erpnext": info("erpnext", node=16)})
        self.assertEqual((res.node, res.notices), ("18", []))

    def test_inspect_failure_is_reported_and_catalog_order_kept(self):
        res = self.resolve(
            {"lending": sel("lending"), "erpnext": sel("erpnext")},
            {"r/lending": deps.InspectError("no access"), "r/erpnext": info("erpnext")},
        )
        self.assertTrue(any("no access" in e for e in res.errors))
        self.assertEqual(res.order, ["erpnext", "lending"])
