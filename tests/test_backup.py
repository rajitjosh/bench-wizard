import gzip
import tempfile
import unittest
from pathlib import Path

from bench_wizard import backup

HEAD = """-- MariaDB dump 10.19
DROP TABLE IF EXISTS `__Auth`;
CREATE TABLE `__Auth` (
  `doctype` varchar(140) NOT NULL
) ENGINE=InnoDB;
INSERT INTO `__Auth` VALUES ('User');
DROP TABLE IF EXISTS `tabDocType`;
CREATE TABLE `tabDocType` (
  `name` varchar(140) NOT NULL,
  `app_name` varchar(140) DEFAULT NULL
) ENGINE=InnoDB;
INSERT INTO `tabDocType` VALUES ('Installed Application','not-an-app');
"""
TABLE = """DROP TABLE IF EXISTS `tabInstalled Application`;
CREATE TABLE `tabInstalled Application` (
  `name` varchar(140) NOT NULL,
  `creation` datetime(6) DEFAULT NULL,
  `idx` int(8) NOT NULL DEFAULT 0,
  `app_name` varchar(140) DEFAULT NULL,
  `app_version` varchar(140) DEFAULT NULL,
  `git_branch` varchar(140) DEFAULT NULL,
  `parent` varchar(140) DEFAULT NULL,
  PRIMARY KEY (`name`),
  KEY `parent` (`parent`)
) ENGINE=InnoDB;
"""
ROWS = ("INSERT INTO `tabInstalled Application` VALUES "
        "('a1','2025-01-01 10:00:00.000000',1,'frappe','15.80.1','version-15','Installed Applications'),"
        "('a2',NULL,2,'erpnext','15.78.0','version-15','Installed Applications'),"
        "('a3',NULL,3,'it''s, odd\\'app','0.0.1',NULL,'Installed Applications');\n")
TAIL = """DROP TABLE IF EXISTS `tabItem`;
CREATE TABLE `tabItem` (
  `name` varchar(140) NOT NULL
) ENGINE=InnoDB;
"""


class BackupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, text, name="dump.sql"):
        path = self.dir / name
        if name.endswith(".gz"):
            with gzip.open(path, "wt") as fh:
                fh.write(text)
        else:
            path.write_text(text)
        return str(path)

    def test_plain_dump(self):
        info = backup.inspect(self.write(HEAD + TABLE + ROWS + TAIL))
        self.assertEqual(info.apps["frappe"], "15.80.1")
        self.assertEqual(info.apps["erpnext"], "15.78.0")
        self.assertEqual(info.frappe_major, 15)

    def test_gzipped_dump(self):
        info = backup.inspect(self.write(HEAD + TABLE + ROWS + TAIL, "dump.sql.gz"))
        self.assertEqual(list(info.apps)[:2], ["frappe", "erpnext"])

    def test_values_with_quotes_and_commas(self):
        info = backup.inspect(self.write(HEAD + TABLE + ROWS + TAIL))
        self.assertEqual(info.apps["it's, odd'app"], "0.0.1")

    def test_insert_with_column_list(self):
        rows = ("INSERT INTO `tabInstalled Application` (`app_version`, `name`, `app_name`) VALUES "
                "('16.2.0','x','frappe');\n")
        info = backup.inspect(self.write(HEAD + TABLE + rows + TAIL))
        self.assertEqual(info.apps, {"frappe": "16.2.0"})
        self.assertEqual(info.frappe_major, 16)

    def test_rows_split_over_several_inserts(self):
        more = ("INSERT INTO `tabInstalled Application` VALUES "
                "('a4',NULL,4,'lending','1.0.0',NULL,'Installed Applications');\n")
        info = backup.inspect(self.write(HEAD + TABLE + ROWS + more + TAIL))
        self.assertIn("lending", info.apps)

    def test_rows_on_their_own_lines(self):
        # the layout MariaDB's own dump tool writes: "VALUES" ends the line, one row per line
        rows = ("INSERT INTO `tabInstalled Application` VALUES\n"
                "('a1',NULL,1,'frappe','15.113.3','version-15','Installed Applications'),\n"
                "('a2',NULL,2,'erpnext','15.115.0',NULL,'Installed Applications'),\n"
                "('a3',NULL,3,'iskcon_voucher','1.9.2',NULL,'Installed Applications');\n"
                "/*!40000 ALTER TABLE `tabInstalled Application` ENABLE KEYS */;\n")
        info = backup.inspect(self.write(HEAD + TABLE + rows + TAIL))
        self.assertEqual(info.apps, {"frappe": "15.113.3", "erpnext": "15.115.0", "iskcon_voucher": "1.9.2"})
        self.assertEqual(info.frappe_major, 15)

    def test_frappe_version_from_the_dump_header_when_the_table_is_missing(self):
        header = "-- begin frappe metadata\n-- [frappe]\n-- version = 14.80.1\n-- end frappe metadata\n"
        info = backup.inspect(self.write(header + HEAD + TAIL))
        self.assertEqual(info.apps, {})
        self.assertEqual(info.frappe_major, 14)

    def test_partial_backup_is_flagged(self):
        self.assertTrue(backup.inspect(self.write("-- Partial Backup of Frappe Site x\n" + HEAD + TAIL)).partial)
        self.assertFalse(backup.inspect(self.write(HEAD + TAIL)).partial)

    def test_dump_without_the_table(self):
        info = backup.inspect(self.write(HEAD + TAIL))
        self.assertEqual(info.apps, {})
        self.assertIsNone(info.frappe_major)

    def test_stops_before_later_tables(self):
        poison = "INSERT INTO `tabInstalled Application` VALUES ('z',NULL,9,'late','9.9.9',NULL,'p');\n"
        info = backup.inspect(self.write(HEAD + TABLE + ROWS + TAIL + poison))
        self.assertNotIn("late", info.apps)

    def test_missing_file(self):
        with self.assertRaises(backup.BackupError):
            backup.inspect(str(self.dir / "nope.sql"))

    def test_not_gzip_despite_extension(self):
        path = self.dir / "bad.sql.gz"
        path.write_text("plain text")
        with self.assertRaises(backup.BackupError):
            backup.inspect(str(path))
