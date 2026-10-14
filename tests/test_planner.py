import unittest
from pathlib import Path

from bench_wizard import catalog, planner

CATALOG = catalog.load(Path(__file__).resolve().parent.parent / "catalog.json")
F = "/home/u/git/demo"
ERPNEXT = {"name": "erpnext", "repo": "https://github.com/frappe/erpnext.git", "branch": "version-15"}
LENDING = {"name": "lending", "repo": "https://github.com/frappe/lending.git", "branch": "version-15"}


def answers(**over):
    base = {"version": "version-15", "folder": F, "site": "demo.localhost", "db_name": "",
            "admin_password": "adminpw", "mariadb_root_password": "rootpw",
            "apps": [ERPNEXT], "restore": None}
    base.update(over)
    return base


def restore(**over):
    base = {"sql": "/b/db.sql.gz", "public": "", "private": "", "backup_key": "", "site_key": "",
            "mute_emails": True, "pause_scheduler": True, "backup_apps": ["frappe", "erpnext"]}
    base.update(over)
    return base


def argvs(steps):
    return [s.argv for s in steps]


class PlanTest(unittest.TestCase):
    def test_plain_erpnext_version_15(self):
        steps = planner.plan(answers(), CATALOG)
        s = "demo.localhost"
        self.assertEqual(argvs(steps), [
            ["bench", "init", F, "--frappe-branch", "version-15", "--python", "python3.11"],
            ["sh", "-c", 'printf "%s\\n" "$1" > .nvmrc', "sh", "18"],
            ["bench", "get-app", "--skip-assets", "--branch", "version-15", ERPNEXT["repo"]],
            ["bench", "new-site", s, "--mariadb-root-password", "rootpw", "--admin-password", "adminpw"],
            ["bench", "--site", s, "install-app", "erpnext"],
            ["bench", "use", s],
            ["bench", "--site", s, "set-config", "developer_mode", "1"],
            ["bench", "build"],
            ["bench", "--site", s, "migrate"],
        ])
        self.assertEqual([st.id for st in steps], [
            "init", "nvmrc", "get-erpnext", "new-site", "install-erpnext", "use", "developer-mode",
            "build", "migrate"])

    def test_version_16_uses_its_python_and_node(self):
        steps = planner.plan(answers(version="version-16"), CATALOG)
        self.assertEqual(steps[0].argv[-3:], ["version-16", "--python", "python3.14"])
        self.assertEqual(steps[1].argv[-1], "24")

    def test_working_directories(self):
        steps = planner.plan(answers(), CATALOG)
        # the parent folder may not exist yet, so init and its cleanup run from /
        self.assertEqual(steps[0].cwd, "/")
        self.assertTrue(all(st.cwd == F for st in steps[1:]))

    def test_bench_redis_runs_from_new_site_onwards(self):
        services = planner.services(answers())
        self.assertEqual(services, [
            {"argv": ["redis-server", "config/redis_queue.conf"], "cwd": F, "before": "new-site"},
            {"argv": ["redis-server", "config/redis_cache.conf"], "cwd": F, "before": "new-site"},
        ])

    def test_site_name_is_validated(self):
        for bad in ("Site.localhost", "a b", "-x", "x/../y", "x;y"):
            with self.assertRaises(planner.PlanError):
                planner.plan(answers(site=bad), CATALOG)

    def test_database_name_is_validated(self):
        with self.assertRaises(planner.PlanError):
            planner.plan(answers(db_name="x; drop"), CATALOG)

    def test_values_that_look_like_options_are_rejected(self):
        with self.assertRaises(planner.PlanError) as ctx:
            planner.plan(answers(admin_password="-Secret1"), CATALOG)
        self.assertIn("must not start with", str(ctx.exception))
        with self.assertRaises(planner.PlanError):
            planner.plan(answers(restore=restore(site_key="-abc")), CATALOG)
        with self.assertRaises(planner.PlanError):
            planner.plan(answers(restore=restore(sql="-x.sql")), CATALOG)

    def test_apps_keep_given_order_for_fetch_and_install(self):
        steps = planner.plan(answers(apps=[ERPNEXT, LENDING]), CATALOG)
        ids = [st.id for st in steps]
        self.assertLess(ids.index("get-erpnext"), ids.index("get-lending"))
        self.assertLess(ids.index("install-erpnext"), ids.index("install-lending"))
        self.assertLess(ids.index("get-lending"), ids.index("new-site"))

    def test_no_apps(self):
        ids = [st.id for st in planner.plan(answers(apps=[]), CATALOG)]
        self.assertEqual(ids, ["init", "nvmrc", "new-site", "use", "developer-mode", "build", "migrate"])

    def test_frappe_is_never_fetched_or_installed(self):
        frappe = {"name": "frappe", "repo": "x", "branch": "version-15"}
        ids = [st.id for st in planner.plan(answers(apps=[frappe, ERPNEXT]), CATALOG)]
        self.assertNotIn("get-frappe", ids)
        self.assertNotIn("install-frappe", ids)

    def test_database_name(self):
        new_site = planner.plan(answers(db_name="demo_db"), CATALOG)[3]
        self.assertEqual(new_site.argv[3:5], ["--db-name", "demo_db"])

    def test_folder_with_whitespace_is_rejected(self):
        # bench init itself splits its own commands on spaces and fails
        with self.assertRaises(planner.PlanError) as ctx:
            planner.plan(answers(folder="/home/u/my benches/x"), CATALOG)
        self.assertIn("space", str(ctx.exception))

    def test_password_with_quotes_is_one_argument_and_masked(self):
        pw = "it's \"a\" $HOME pw"
        new_site = planner.plan(answers(admin_password=pw, mariadb_root_password="r o'ot"), CATALOG)[3]
        self.assertIn(pw, new_site.argv)
        shown = new_site.display()
        self.assertNotIn("HOME", shown)
        self.assertNotIn("r o", shown)
        self.assertEqual(shown, "bench new-site demo.localhost --mariadb-root-password •••• "
                                "--admin-password ••••")

    def test_display_quotes_arguments_with_spaces(self):
        step = planner.Step("x", "x", ["bench", "restore", "/b/my backup.sql"], F)
        self.assertEqual(step.display(), "bench restore '/b/my backup.sql'")

    def test_cleanup_and_retry(self):
        steps = {st.id: st for st in planner.plan(answers(), CATALOG)}
        self.assertEqual(steps["init"].cleanup, ["rm", "-rf", F])
        self.assertTrue(steps["init"].cleanup_required)
        self.assertEqual(steps["get-erpnext"].retry_argv[:3], ["bench", "get-app", "--overwrite"])
        self.assertIsNone(steps["get-erpnext"].cleanup)
        drop = steps["new-site"]
        self.assertEqual(drop.cleanup, ["bench", "drop-site", "demo.localhost", "--force", "--no-backup",
                                        "--db-root-password", "rootpw"])
        self.assertFalse(drop.cleanup_required)
        self.assertNotIn("rootpw", drop.display(drop.cleanup, drop.cleanup_secret_args))
        self.assertIsNone(steps["build"].cleanup)

    def test_unknown_version(self):
        with self.assertRaises(planner.PlanError):
            planner.plan(answers(version="version-99"), CATALOG)

    def test_required_answers(self):
        for key in ("folder", "site", "admin_password"):
            with self.assertRaises(planner.PlanError):
                planner.plan(answers(**{key: ""}), CATALOG)

    def test_relative_folder_rejected(self):
        with self.assertRaises(planner.PlanError):
            planner.plan(answers(folder="~/git/demo"), CATALOG)


class RestorePlanTest(unittest.TestCase):
    def test_minimal_restore(self):
        steps = planner.plan(answers(restore=restore()), CATALOG)
        s = "demo.localhost"
        self.assertEqual(argvs(steps)[3:], [
            ["bench", "new-site", s, "--mariadb-root-password", "rootpw", "--admin-password", "adminpw"],
            ["bench", "--site", s, "restore", "/b/db.sql.gz", "--db-root-password", "rootpw"],
            ["bench", "--site", s, "migrate"],
            ["bench", "--site", s, "set-admin-password", "adminpw"],
            ["bench", "--site", s, "set-config", "mute_emails", "1"],
            ["bench", "--site", s, "set-config", "pause_scheduler", "1"],
            ["bench", "use", s],
            ["bench", "--site", s, "set-config", "developer_mode", "1"],
            ["bench", "build"],
        ])

    def test_all_optional_fields(self):
        r = restore(public="/b/files.tar", private="/b/private.tar", backup_key="BK", site_key="SK")
        steps = {st.id: st for st in planner.plan(answers(restore=r), CATALOG)}
        self.assertEqual(steps["restore"].argv[4:], [
            "/b/db.sql.gz", "--db-root-password", "rootpw", "--with-public-files", "/b/files.tar",
            "--with-private-files", "/b/private.tar", "--encryption-key", "BK"])
        self.assertEqual(steps["site-key"].argv[-3:], ["set-config", "encryption_key", "SK"])
        shown = steps["restore"].display() + steps["site-key"].display()
        for secret in ("rootpw", "BK", "SK"):
            self.assertNotIn(secret, shown)

    def test_site_key_set_before_migrate(self):
        ids = [st.id for st in planner.plan(answers(restore=restore(site_key="SK")), CATALOG)]
        self.assertLess(ids.index("restore"), ids.index("site-key"))
        self.assertLess(ids.index("site-key"), ids.index("migrate"))

    def test_toggles_off(self):
        ids = [st.id for st in planner.plan(
            answers(restore=restore(mute_emails=False, pause_scheduler=False)), CATALOG)]
        self.assertNotIn("mute-emails", ids)
        self.assertNotIn("pause-scheduler", ids)

    def test_apps_not_in_backup_installed_after_migrate(self):
        steps = planner.plan(answers(apps=[ERPNEXT, LENDING], restore=restore()), CATALOG)
        ids = [st.id for st in steps]
        self.assertNotIn("install-erpnext", ids)
        self.assertLess(ids.index("migrate"), ids.index("install-lending"))
        self.assertEqual(ids.count("migrate"), 1)

    def test_backup_app_not_selected_is_removed_before_migrate(self):
        steps = planner.plan(answers(restore=restore(backup_apps=["frappe", "erpnext", "gone_app"])), CATALOG)
        ids = [st.id for st in steps]
        self.assertEqual([i for i in ids if i.startswith("forget-")], ["forget-gone_app"])
        self.assertLess(ids.index("restore"), ids.index("forget-gone_app"))
        self.assertLess(ids.index("forget-gone_app"), ids.index("migrate"))
        self.assertEqual(steps[ids.index("forget-gone_app")].argv[-2:], ["remove-from-installed-apps", "gone_app"])

    def test_partial_backup_uses_partial_restore(self):
        steps = planner.plan(answers(restore=restore(partial=True, backup_key="k")), CATALOG)
        step = next(st for st in steps if st.id == "restore")
        self.assertEqual(step.argv[3:], ["partial-restore", "/b/db.sql.gz", "--encryption-key", "k"])
        self.assertIn("••••", step.display())

    def test_partial_backup_with_files_is_rejected(self):
        with self.assertRaises(planner.PlanError):
            planner.plan(answers(restore=restore(partial=True, public="/b/files.tar")), CATALOG)

    def test_unknown_backup_app_list_installs_every_selected_app(self):
        # install-app is a no-op for an app the restored site already has
        steps = planner.plan(answers(apps=[ERPNEXT, LENDING], restore=restore(backup_apps=[])), CATALOG)
        ids = [st.id for st in steps]
        self.assertLess(ids.index("migrate"), ids.index("install-erpnext"))
        self.assertLess(ids.index("install-erpnext"), ids.index("install-lending"))

    def test_restore_needs_sql_path(self):
        with self.assertRaises(planner.PlanError):
            planner.plan(answers(restore=restore(sql="")), CATALOG)
