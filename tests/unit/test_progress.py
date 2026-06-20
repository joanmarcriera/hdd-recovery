"""Unit tests for useful-progress probes."""
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from _loader import REPO_ROOT, load_module

progress = load_module("lib/progress.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


class TestProgressCounters(unittest.TestCase):
    def test_ddrescue_rescued_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "disk.map"
            path.write_text(
                "# Mapfile\n"
                "0x00000000 0x00001000 +\n"
                "0x00001000 0x00002000 -\n"
                "0x00003000 0x00000010 +\n",
                encoding="utf-8",
            )

            self.assertEqual(progress.ddrescue_rescued_bytes(path), 0x1010)

    def test_directory_work_counter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.bin").write_bytes(b"abc")
            (root / "sub").mkdir()
            (root / "sub" / "b.bin").write_bytes(b"hello")

            self.assertEqual(progress.directory_work_counter(root), 10)

    def test_table_row_count(self):
        fd, db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.addCleanup(os.unlink, db)
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA.read_text())
        conn.execute(
            "INSERT INTO wallet_candidates(source_stage,score,reason,created_at) "
            "VALUES('test',1,'x','2026-06-20T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        self.assertEqual(progress.table_row_count(db, "wallet_candidates"), 1)


class TestStageProgressProbe(unittest.TestCase):
    def _db(self):
        fd, db = tempfile.mkstemp(suffix=".analysis.sqlite")
        os.close(fd)
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA.read_text())
        conn.commit()
        conn.close()
        self.addCleanup(os.unlink, db)
        return db

    def test_probe_touches_heartbeat_and_progress(self):
        db = self._db()
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO scan_runs(stage,status,started_at,output_dir) "
                "VALUES(?,?,?,?)",
                ("bulk-extractor-raw", "running", "2026-06-20T00:00:00Z", td),
            )
            conn.commit()
            conn.close()

            probe = progress.build_stage_progress_probe(db, "bulk-extractor-raw")
            self.assertIsNotNone(probe)
            self.assertEqual(probe(), 0)

            conn = sqlite3.connect(db)
            first = conn.execute(
                "SELECT heartbeat_at,last_progress_at FROM scan_runs"
            ).fetchone()
            conn.close()
            self.assertIsNotNone(first[0])
            self.assertIsNone(first[1])

            time.sleep(0.01)
            Path(td, "feature.txt").write_text("abc", encoding="utf-8")
            value = probe()
            probe.mark_progress(float(value))

            conn = sqlite3.connect(db)
            second = conn.execute(
                "SELECT heartbeat_at,last_progress_at FROM scan_runs"
            ).fetchone()
            conn.close()
            self.assertIsNotNone(second[0])
            self.assertIsNotNone(second[1])

    def test_probe_uses_table_counter_for_detect_stage(self):
        db = self._db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO scan_runs(stage,status,started_at) VALUES(?,?,?)",
            ("detect-wallets", "running", "2026-06-20T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO wallet_candidates(source_stage,score,reason,created_at) "
            "VALUES('test',1,'x','2026-06-20T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        probe = progress.build_stage_progress_probe(db, "detect-wallets")
        self.assertEqual(probe(), 1)


if __name__ == "__main__":
    unittest.main()
