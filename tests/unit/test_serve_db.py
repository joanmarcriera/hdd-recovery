"""Unit tests for lib/serve_db.py (#18 extraction)."""
import os
import sqlite3
import tempfile
import unittest

from _loader import load_module

dbmod = load_module("lib/serve_db.py")


class TestSafeSql(unittest.TestCase):
    def test_allows_select_and_with(self):
        self.assertEqual(dbmod.safe_sql("SELECT 1"), "SELECT 1")
        self.assertEqual(
            dbmod.safe_sql("; WITH x AS (SELECT 1) SELECT * FROM x"),
            "WITH x AS (SELECT 1) SELECT * FROM x",
        )

    def test_rejects_write_or_schema_keywords(self):
        for bad in (
            "DELETE FROM files",
            "DROP TABLE files",
            "UPDATE files SET x=1",
            "PRAGMA table_info(files)",
            "SELECT 1; VACUUM",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    dbmod.safe_sql(bad)


class TestReadOnlyQueries(unittest.TestCase):
    def test_run_query_and_scalar_use_read_only_database(self):
        fd, path = tempfile.mkstemp(suffix=".analysis.sqlite")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE image_info(id INTEGER PRIMARY KEY, export_root TEXT)")
        conn.execute("INSERT INTO image_info(id, export_root) VALUES (1, '/tmp/export')")
        conn.commit()
        conn.close()

        cols, rows = dbmod.run_query(path, "SELECT export_root FROM image_info")
        self.assertEqual(cols, ["export_root"])
        self.assertEqual(rows[0][0], "/tmp/export")
        self.assertEqual(dbmod.db_export_root(path), "/tmp/export")

        with self.assertRaises(sqlite3.OperationalError):
            dbmod.run_query(path, "CREATE TABLE forbidden(id INTEGER)")


class TestFindDatabases(unittest.TestCase):
    def test_prunes_heavy_output_directories_and_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as root:
            keep = os.path.join(root, "images")
            prune = os.path.join(root, "exports")
            hidden = os.path.join(root, ".hidden")
            for path in (keep, prune, hidden):
                os.makedirs(path)
                open(os.path.join(path, "x.analysis.sqlite"), "w").close()

            found = dbmod.find_databases(root)
            self.assertEqual(found, [os.path.join(keep, "x.analysis.sqlite")])

    def test_prunes_rsync_conflict_backup_dirs(self):
        # Stale DB copies under rsync --backup-dir trees must not be discovered:
        # they point image_info at the real image/export and would show as
        # phantom duplicates and get re-enqueued.
        with tempfile.TemporaryDirectory() as root:
            images = os.path.join(root, "images")
            backup = os.path.join(images, "_rsync_conflict_backups_20260507-150519")
            for path in (images, backup):
                os.makedirs(path)
                open(os.path.join(path, "disk.img.analysis.sqlite"), "w").close()

            found = dbmod.find_databases(root)
            self.assertEqual(
                found, [os.path.join(images, "disk.img.analysis.sqlite")])


if __name__ == "__main__":
    unittest.main()
