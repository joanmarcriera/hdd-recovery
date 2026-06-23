"""Unit tests for lib/db.py shared SQLite helpers (de-dup cycle 1)."""
import os
import sqlite3
import tempfile
import unittest

from _loader import REPO_ROOT

from lib.db import (
    ImageInfo,
    end_scan_run,
    fetch_image_info,
    open_writable_db,
    ro_db,
    start_scan_run,
)

SCHEMA = (REPO_ROOT / "sql" / "analysis-schema.sql").read_text()


class _TmpDB(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".analysis.sqlite")
        os.close(fd)
        conn = sqlite3.connect(self.path)
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass


class TestRoDb(_TmpDB):
    def test_yields_rows_and_is_read_only(self):
        with ro_db(self.path) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM scan_runs").fetchone()
            self.assertEqual(row["n"], 0)  # row_factory gives name access
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO scan_runs(stage,status,started_at) "
                    "VALUES('x','running','t')"
                )


class TestOpenWritableDb(_TmpDB):
    def test_applies_pragmas_and_runs_ddl(self):
        ddl = "CREATE TABLE IF NOT EXISTS extra(id INTEGER PRIMARY KEY, v TEXT);"
        conn = open_writable_db(self.path, ddl=ddl)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
            conn.execute("INSERT INTO extra(v) VALUES('hi')")
            conn.commit()
            self.assertEqual(
                conn.execute("SELECT v FROM extra").fetchone()[0], "hi")
        finally:
            conn.close()


class TestFetchImageInfo(_TmpDB):
    def _seed(self):
        conn = sqlite3.connect(self.path)
        conn.execute(
            "INSERT INTO image_info(id,image_path,image_name,image_basename,"
            "export_root,ddrescue_map_path,created_at,updated_at) "
            "VALUES(1,'/img/d.img','d.img','d','/exp/d','/logs/d.map','t','t')"
        )
        conn.commit()
        conn.close()

    def test_returns_fields(self):
        self._seed()
        info = fetch_image_info(self.path)
        self.assertIsInstance(info, ImageInfo)
        self.assertEqual(info.image_path, "/img/d.img")
        self.assertEqual(info.export_root, "/exp/d")
        self.assertEqual(info.ddrescue_map_path, "/logs/d.map")

    def test_raises_when_missing(self):
        with self.assertRaises(LookupError):
            fetch_image_info(self.path)


class TestScanRunRoundTrip(_TmpDB):
    def test_start_then_end(self):
        conn = sqlite3.connect(self.path)
        try:
            run_id = start_scan_run(
                conn, "demo-stage", "demo --run",
                log_path="/l/x.log", output_dir="/o/x")
            self.assertIsInstance(run_id, int)
            row = conn.execute(
                "SELECT stage,status,started_at,command_line,log_path,output_dir,"
                "ended_at,notes FROM scan_runs WHERE id=?", (run_id,)).fetchone()
            self.assertEqual(row[0], "demo-stage")
            self.assertEqual(row[1], "running")
            self.assertTrue(row[2])               # started_at set
            self.assertEqual(row[3], "demo --run")
            self.assertEqual(row[4], "/l/x.log")
            self.assertEqual(row[5], "/o/x")
            self.assertIsNone(row[6])             # ended_at not yet set

            end_scan_run(conn, run_id, "ok", notes="done it")
            row = conn.execute(
                "SELECT status,ended_at,notes FROM scan_runs WHERE id=?",
                (run_id,)).fetchone()
            self.assertEqual(row[0], "ok")
            self.assertTrue(row[1])               # ended_at set
            self.assertEqual(row[2], "done it")
        finally:
            conn.close()

    def test_omitted_paths_round_trip_as_empty_string(self):
        # Callers like image-tag-photos.py omit log_path/output_dir; they must
        # land as '' (not NULL), matching what the progress probe COALESCEs.
        conn = sqlite3.connect(self.path)
        try:
            run_id = start_scan_run(conn, "demo", "cmd")
            row = conn.execute(
                "SELECT log_path, output_dir FROM scan_runs WHERE id=?",
                (run_id,)).fetchone()
            self.assertEqual(row[0], "")
            self.assertEqual(row[1], "")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
