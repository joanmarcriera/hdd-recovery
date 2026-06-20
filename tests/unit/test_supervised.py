"""Unit tests for durable supervised queue/pipeline run records."""
import os
import sqlite3
import subprocess
import tempfile
import unittest

from _loader import REPO_ROOT, load_module

sup = load_module("lib/supervised.py")
runs = load_module("lib/runs.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


class TestSupervisedRuns(unittest.TestCase):
    def _db(self):
        fd, path = tempfile.mkstemp(suffix=".analysis.sqlite")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA.read_text())
        conn.commit()
        conn.close()
        self.addCleanup(os.unlink, path)
        return path

    def _status(self, db, run_id):
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT status, pid, heartbeat_at, last_progress_at, exit_code "
            "FROM supervised_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        conn.close()
        return row

    def test_create_attach_finish_and_env_helpers(self):
        db = self._db()
        run_id = sup.create_supervised_run(db, "queue", "image-queue.py", "/tmp/q.log")
        p = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and p.terminate())
        try:
            sup.set_supervised_process(db, run_id, p.pid)
            active = sup.active_supervised_runs(db, run_kind="queue", reconcile=False)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].pid, p.pid)

            old = os.environ.get("SUPERVISED_RUNS")
            os.environ["SUPERVISED_RUNS"] = sup.encode_env([(db, run_id)])
            try:
                sup.heartbeat_env_runs(progress=True)
                sup.finish_env_runs("ok", exit_code=0)
            finally:
                if old is None:
                    os.environ.pop("SUPERVISED_RUNS", None)
                else:
                    os.environ["SUPERVISED_RUNS"] = old

            status, _, heartbeat, progress_at, exit_code = self._status(db, run_id)
            self.assertEqual(status, "ok")
            self.assertIsNotNone(heartbeat)
            self.assertIsNotNone(progress_at)
            self.assertEqual(exit_code, 0)
        finally:
            p.terminate()
            p.wait()

    def test_reconcile_marks_dead_pid_interrupted(self):
        db = self._db()
        run_id = sup.create_supervised_run(db, "pipeline", "image-pipeline.py", "/tmp/p.log")
        p = subprocess.Popen(["sleep", "30"], start_new_session=True)
        sup.set_supervised_process(db, run_id, p.pid)
        p.terminate()
        p.wait()

        n = sup.reconcile_supervised_runs(db, markers=("sleep",))

        self.assertEqual(n, 1)
        self.assertEqual(self._status(db, run_id)[0], "interrupted")

    def test_cancel_marks_and_terminates_process_group(self):
        db = self._db()
        run_id = sup.create_supervised_run(db, "queue", "image-queue.py", "/tmp/q.log")
        p = subprocess.Popen(["sleep", "30"], start_new_session=True)
        sup.set_supervised_process(db, run_id, p.pid)
        try:
            self.assertTrue(sup.cancel_supervised_run(db, run_id))
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.assertFalse(runs.pid_alive(p.pid, markers=("sleep",)))
            conn = sqlite3.connect(db)
            cancel_requested = conn.execute(
                "SELECT cancel_requested FROM supervised_runs WHERE id=?",
                (run_id,),
            ).fetchone()[0]
            conn.close()
            self.assertEqual(cancel_requested, 1)
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()

    def test_request_stop_marks_without_terminating_process_group(self):
        db = self._db()
        run_id = sup.create_supervised_run(db, "queue", "image-queue.py", "/tmp/q.log")
        p = subprocess.Popen(["sleep", "30"], start_new_session=True)
        sup.set_supervised_process(db, run_id, p.pid)
        try:
            self.assertTrue(sup.request_supervised_stop(db, run_id))
            self.assertTrue(runs.pid_alive(p.pid, markers=("sleep",)))
            self.assertTrue(sup.env_stop_requested(sup.encode_env([(db, run_id)])))
        finally:
            if p.poll() is None:
                p.terminate()
            p.wait()


if __name__ == "__main__":
    unittest.main()
