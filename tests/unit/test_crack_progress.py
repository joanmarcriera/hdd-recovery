"""Unit tests for hashcat progress parsing used by image-crack-wallet.sh."""
import io
import os
import sqlite3
import sys
import tempfile
import unittest

from _loader import REPO_ROOT, load_module

crack_progress = load_module("lib/crack_progress.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


class TestHashcatProgressParsing(unittest.TestCase):
    def test_parse_progress_pct(self):
        parsed = crack_progress.parse_hashcat_status_line(
            "Progress.........: 12345/100000 (12.34%)"
        )

        self.assertEqual(parsed["progress_pct"], 12.34)

    def test_parse_eta_seconds(self):
        self.assertEqual(
            crack_progress.parse_eta_seconds(
                "Time.Estimated...: Sat Jun 20 12:00:00 2026 (1 day, 2 hours, 3 mins, 4 secs)"
            ),
            93784,
        )

    def test_ignore_non_duration_eta(self):
        self.assertIsNone(
            crack_progress.parse_eta_seconds(
                "Time.Estimated...: Sat Jun 20 12:00:00 2026"
            )
        )


class TestCrackTaskProgressUpdates(unittest.TestCase):
    def _db(self):
        fd, db = tempfile.mkstemp(suffix=".analysis.sqlite")
        os.close(fd)
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA.read_text())
        conn.execute(
            "INSERT INTO crack_tasks(cracker,target_kind,status) VALUES(?,?,?)",
            ("hashcat", "wallet.dat", "running"),
        )
        conn.commit()
        conn.close()
        self.addCleanup(os.unlink, db)
        return db

    def _row(self, db):
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT progress_pct, eta_seconds FROM crack_tasks WHERE id=1"
        ).fetchone()
        conn.close()
        return row

    def test_update_task_progress_uses_parameters(self):
        db = self._db()

        crack_progress.update_task_progress(db, 1, progress_pct=42.5, eta_seconds=99)

        self.assertEqual(self._row(db), (42.5, 99))

    def test_stream_updates_flushes_pending_fields(self):
        db = self._db()
        old_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(
                "Progress.........: 50/100 (50.00%)\n"
                "Time.Estimated...: Sat Jun 20 12:00:00 2026 (10 mins, 5 secs)\n"
            )
            crack_progress.stream_updates(db, 1, min_interval=0)
        finally:
            sys.stdin = old_stdin

        self.assertEqual(self._row(db), (50.0, 605))


if __name__ == "__main__":
    unittest.main()
