import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from bench_wizard import catalog, deps, planner, upgrade
from tests.test_projects import make_bench
from tests.test_sites import git

ROOT = Path(__file__).resolve().parent.parent


def info(frappe="", requires_python=">=3.10", **requires):
    return deps.AppInfo("x", "x", "", requires_python, dict(requires, **({"frappe": frappe} if frappe else {})))


class PickBranchTest(unittest.TestCase):
    def test_the_first_likely_branch_that_supports_the_target_wins(self):
        declared = {"main": info(">=15.123.1,<17.0.0"), "develop": info(">=16.0.0,<18.0.0")}
        pick = lambda major, names=("main", "develop", "feature/x"): upgrade.pick_branch(
            "r", "main", f"version-{major}", major, list(names), lambda repo, b: declared[b])
        self.assertEqual(pick(16), ("main", "declares frappe >=15.123.1,<17.0.0"))
        self.assertEqual(pick(17)[0], "develop")
        branch, why = pick(18)
        self.assertEqual(branch, "")
        self.assertIn("main declares frappe >=15.123.1,<17.0.0", why)

    def test_a_version_branch_or_an_unversioned_main_is_taken_by_its_name(self):
        none = lambda repo, b: info()
        self.assertEqual(upgrade.pick_branch("r", "version-15", "version-16", 16, ["version-15", "version-16"], none)[0],
                         "version-16")
        branch, why = upgrade.pick_branch("r", "x", "version-16", 16, ["master", "x"], none)
        self.assertEqual(branch, "master")
        self.assertIn("check it", why)

    def test_an_unreadable_branch_is_skipped(self):
        def inspect(repo, b):
            if b == "main":
                raise deps.InspectError("private")
            return info(">=16")
        self.assertEqual(upgrade.pick_branch("r", "main", "version-16", 16, ["main", "develop"], inspect)[0], "develop")


class CheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bench = make_bench(self.root / "bench")
        self.catalog = catalog.load(ROOT / "catalog.json")
        for name, branch, remote in (("frappe", "version-15", "https://github.com/frappe/frappe.git"),
                                     ("erpnext", "version-15", "https://github.com/frappe/erpnext.git"),
                                     ("helpdesk", "main", "https://github.com/frappe/helpdesk.git"),
                                     ("mine", "dev", None)):
            app = Path(self.bench) / "apps" / name
            git(self.root, "init", "-q", "-b", branch, str(app))
            git(app, "commit", "-q", "--allow-empty", "-m", "one")
            if remote:
                git(app, "remote", "add", "upstream", remote)

    def inspect(self, repo, branch):
        return {("https://github.com/frappe/frappe.git", "version-16"): info(requires_python=">=3.14"),
                ("https://github.com/frappe/erpnext.git", "version-16"): info(">=16.0.0-dev,<17.0.0"),
                ("https://github.com/frappe/helpdesk.git", "main"): info(">=15.123.1,<17.0.0"),
                ("https://github.com/frappe/helpdesk.git", "develop"): info(">=16.0.0,<18.0.0")}[repo, branch]

    def test_every_app_gets_a_branch_and_the_bench_python_is_compared(self):
        result = upgrade.check(self.bench, "version-16", self.catalog, self.inspect, lambda repo: ["main", "develop"])
        rows = {r["name"]: r for r in result["apps"]}
        self.assertEqual({n: r["branch"] for n, r in rows.items()},
                         {"frappe": "version-16", "erpnext": "version-16", "helpdesk": "main", "mine": "dev"})
        self.assertIn("declares frappe", rows["helpdesk"]["why"])
        self.assertIn("no remote", rows["mine"]["why"])
        self.assertEqual(result["errors"], [])
        self.assertEqual((result["node"], result["python"]["needs"], result["python"]["ok"]), ("24", ">=3.14", False))
        with self.assertRaises(ValueError):
            upgrade.check(self.bench, "version-99", self.catalog, self.inspect, lambda repo: [])

    def test_an_app_with_no_supporting_branch_is_an_error(self):
        result = upgrade.check(self.bench, "version-16", self.catalog,
                               lambda repo, b: info(">=14,<15") if "helpdesk" in repo else self.inspect(repo, b),
                               lambda repo: ["main"])
        self.assertTrue(any(e.startswith("helpdesk: no branch supports Frappe 16") for e in result["errors"]))


class PlanTest(unittest.TestCase):
    def test_backup_first_then_switch_then_a_new_env_kept_beside_the_old(self):
        moves = [{"name": "frappe", "branch": "version-16", "remote": "upstream", "stash": False},
                 {"name": "mine", "branch": "v16", "remote": None, "stash": True}]
        steps = planner.plan_bench_upgrade("/b", ["a.localhost"], moves,
                                           [{"name": "telephony", "repo": "https://x/telephony", "branch": "develop"}],
                                           python="3.14", node="24")
        self.assertEqual([s.id for s in steps], [
            "backup-a.localhost", "fetch-frappe", "switch-frappe", "stash-mine", "switch-mine", "env-aside", "env",
            "nvmrc", "get-telephony", "requirements", "migrate", "build"])
        self.assertEqual(steps[6].argv, ["bench", "setup", "env", "--python", "python3.14"])
        self.assertEqual([s.id for s in planner.plan_bench_upgrade("/b", [], [], [])], ["requirements", "build"])  # no sites
        with self.assertRaises(planner.PlanError):
            planner.plan_bench_upgrade("/b", [], [], [], python="3; rm -rf ~")


if __name__ == "__main__":
    unittest.main()


class ReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_an_app_is_compared_with_frappe_erpnext_and_what_it_requires(self):
        folder = self.root / "b"
        (folder / "apps" / "mine" / "mine").mkdir(parents=True)
        (folder / "apps" / "mine" / "mine" / "hooks.py").write_text('app_name = "mine"\nrequired_apps = ["hrms"]\n')
        cat = catalog.load(ROOT / "catalog.json")
        cat["apps"]["hrms"] = {"title": "HRMS", "repo": "r", "branches": {}, "requires": []}
        rows = [{"name": n, "branch": "version-16", "repo": f"https://x/{n}"} for n in ("frappe", "erpnext", "hrms", "lending", "mine")]
        self.assertEqual([r["name"] for r in upgrade.core_apps(str(folder), "mine", rows, cat)], ["frappe", "erpnext", "hrms"])
        self.assertEqual(upgrade.rules_for("version-15", "version-16"), "v15-v16")
        self.assertEqual(upgrade.rules_for("version-16", "version-17"), "none")

    def test_reports_are_read_worst_first_and_listed_per_bench(self):
        out = Path(upgrade.report_dir(str(self.root / "state"), "/benches/b", "version-16", "mine"))
        out.mkdir(parents=True)
        (out / "findings.json").write_text(json.dumps([{"id": "A", "severity": "low"}, {"id": "B", "severity": "high"}]))
        report = upgrade.read_report(str(out))
        self.assertEqual(([f["id"] for f in report["findings"]], report["counts"]["high"], report["claude"]), (["B", "A"], 1, ""))
        (out / "claude.md").write_text("# Verdict")
        self.assertEqual(upgrade.read_report(str(out))["claude"], "# Verdict")
        self.assertEqual([(r["app"], r["claude"]) for r in upgrade.reports(str(self.root / "state"), "/benches/b")], [("mine", True)])
        self.assertEqual(upgrade.reports(str(self.root / "state"), "/benches/other"), [])
        self.assertIsNone(upgrade.read_report(str(self.root / "nothing")))

    def test_report_plan_fetches_trees_scans_and_has_claude_only_read(self):
        scan = {"app": "mine", "out": "/s/out", "rules": "v15-v16", "sources": [("frappe", "/b/apps/frappe")],
                "targets": [("frappe", "/t/frappe-version-16")], "prompt": "review it"}
        trees = [("frappe", "https://github.com/frappe/frappe.git", "version-16", "/t/frappe-version-16")]
        steps = planner.plan_upgrade_report("/b", trees, [scan], "/w/upgrade_scan.py")
        self.assertEqual([s.id for s in steps], ["tree-frappe", "scan-mine"])
        self.assertIn("frappe=/t/frappe-version-16", steps[1].argv)
        steps = planner.plan_upgrade_report("/b", trees, [scan], "/w/upgrade_scan.py", claude="/bin/claude", plugin="/w/p")
        review = steps[2].argv
        self.assertEqual((steps[2].id, steps[2].cwd, review[4]), ("review-mine", "/b/apps/mine", "/s/out/claude.md"))
        for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence"):
            self.assertIn(flag, review)
        tools = review[review.index("--allowed-tools") + 1:review.index("--no-session-persistence")]
        self.assertEqual(tools, ["Read", "Grep", "Glob", "Skill"])
        with self.assertRaises(planner.PlanError):
            planner.plan_upgrade_report("/b", [("frappe", "r", "--upload-pack=x", "/t")], [], "/w/s.py")

    def test_a_failed_review_keeps_no_review_but_does_not_stop_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "claude.md")
            run = lambda cmd: subprocess.run(["sh", "-c", planner.KEEP_REVIEW, "sh", out, "sh", "-c", cmd],
                                             capture_output=True, text=True)
            failed = run("echo half; exit 1")
            self.assertEqual((failed.returncode, os.listdir(tmp)), (0, []))
            self.assertIn("Claude did not finish its review (exit 1)", failed.stdout)
            self.assertEqual(run("echo review").returncode, 0)
            self.assertEqual(Path(out).read_text(), "review\n")

