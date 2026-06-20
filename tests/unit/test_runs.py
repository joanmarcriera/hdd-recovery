"""F2 — durable-PID reconciliation (lib/runs.py). Offline; spawns only short
`true`/`sleep` children to exercise dead vs live PIDs."""
import os
import sqlite3
import subprocess
import tempfile
import unittest

from _loader import REPO_ROOT, load_module

runs = load_module("lib/runs.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


def _db(rows):
    """rows: list of (stage, status, pid). Returns a temp DB path."""
    fd, path = tempfile.mkstemp(suffix=".analysis.sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text())
    for stage, status, pid in rows:
        conn.execute(
            "INSERT INTO scan_runs(stage,status,started_at,pid) VALUES (?,?,?,?)",
            (stage, status, "2026-06-20T00:00:00Z", pid))
    conn.commit()
    conn.close()
    return path


def _statuses(db):
    conn = sqlite3.connect(db)
    out = dict(conn.execute("SELECT stage, status FROM scan_runs").fetchall())
    conn.close()
    return out


class TestPidAlive(unittest.TestCase):
    def test_dead_pid(self):
        p = subprocess.Popen(["true"])
        p.wait()
        self.assertFalse(runs.pid_alive(p.pid))

    def test_live_pid_marker_guard(self):
        p = subprocess.Popen(["sleep", "30"])
        try:
            self.assertTrue(runs.pid_alive(p.pid, markers=("sleep",)))
            # alive, but cmdline doesn't match our markers → treat as not-ours
            self.assertFalse(runs.pid_alive(p.pid, markers=("no_such_marker",)))
        finally:
            p.terminate()
            p.wait()

    def test_bad_inputs(self):
        self.assertFalse(runs.pid_alive(None))
        self.assertFalse(runs.pid_alive(-1))
        self.assertFalse(runs.pid_alive("x"))


class TestReconcile(unittest.TestCase):
    def test_only_dead_running_rows_reconciled(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        live = subprocess.Popen(["sleep", "30"])
        try:
            db = _db([
                ("bulk", "running", dead.pid),   # dead pid → interrupted
                ("carve", "running", live.pid),  # live pid → kept
                ("idx", "running", None),         # no pid → kept (human review)
                ("report", "ok", dead.pid),       # not running → kept
            ])
            self.addCleanup(os.unlink, db)
            n = runs.reconcile_running(db, markers=("sleep",))
            self.assertEqual(n, 1)
            st = _statuses(db)
            self.assertEqual(st["bulk"], "interrupted")
            self.assertEqual(st["carve"], "running")
            self.assertEqual(st["idx"], "running")
            self.assertEqual(st["report"], "ok")
        finally:
            live.terminate()
            live.wait()

    def test_note_records_reason(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        db = _db([("bulk", "running", dead.pid)])
        self.addCleanup(os.unlink, db)
        runs.reconcile_running(db)
        note = sqlite3.connect(db).execute(
            "SELECT notes FROM scan_runs WHERE stage='bulk'").fetchone()[0]
        self.assertIn("reconciled", note)


if __name__ == "__main__":
    unittest.main()
