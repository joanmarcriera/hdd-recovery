"""Unit tests for lib/serve_pipeline.py (#18 extraction)."""
import os
import sqlite3
import tempfile
import unittest

from _loader import load_module

pipe = load_module("lib/serve_pipeline.py")


def _make_db(export_root):
    fd, path = tempfile.mkstemp(suffix=".analysis.sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE image_info(id INTEGER PRIMARY KEY, export_root TEXT)")
    conn.execute("INSERT INTO image_info(id, export_root) VALUES (1, ?)", (export_root,))
    conn.commit()
    conn.close()
    return path


class TestServePipeline(unittest.TestCase):
    def test_build_queue_cmd_preserves_order_and_flags(self):
        cmd = pipe.build_queue_cmd(["a.db", "b.db"], ["fast", "photos"], 2,
                                   skip_done=True, keep_going=False)
        joined = " ".join(cmd)
        self.assertIn("--jobs 2", joined)
        self.assertIn("--stages fast,photos", joined)
        self.assertIn("--skip-done", cmd)
        self.assertNotIn("--keep-going", cmd)
        self.assertEqual(cmd[-2:], ["a.db", "b.db"])

    def test_queue_log_dir_prefers_root_subdirectory(self):
        with tempfile.TemporaryDirectory() as root:
            qdir = pipe._queue_log_dir(root)
            self.assertEqual(qdir, os.path.join(root, "queue-logs"))
            self.assertTrue(os.path.isdir(qdir))

    def test_request_stop_active_queue_marks_active_rows_without_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            db = os.path.join(root, "x.img.analysis.sqlite")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE image_info(id INTEGER PRIMARY KEY, export_root TEXT)")
            conn.execute("INSERT INTO image_info(id, export_root) VALUES (1, ?)", (root,))
            conn.commit()
            conn.close()
            run_id = pipe.create_supervised_run(db, "queue", "image-queue.py", "/tmp/q.log")

            self.assertEqual(pipe.request_stop_active_queue(root), 1)
            self.assertTrue(pipe.queue_stop_requested(root))

            conn = sqlite3.connect(db)
            try:
                row = conn.execute(
                    "SELECT status,cancel_requested FROM supervised_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("starting", 1))

    def test_request_stop_active_pipeline_marks_active_rows_without_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            db = os.path.join(root, "x.img.analysis.sqlite")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE image_info(id INTEGER PRIMARY KEY, export_root TEXT)")
            conn.execute("INSERT INTO image_info(id, export_root) VALUES (1, ?)", (root,))
            conn.commit()
            conn.close()
            run_id = pipe.create_supervised_run(db, "pipeline", "image-pipeline.py", "/tmp/p.log")

            self.assertEqual(pipe.request_stop_active_pipeline(db), 1)
            self.assertTrue(pipe.pipeline_stop_requested(db))

            conn = sqlite3.connect(db)
            try:
                row = conn.execute(
                    "SELECT status,cancel_requested FROM supervised_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("starting", 1))

    def test_db_log_path_accepts_only_export_logs(self):
        with tempfile.TemporaryDirectory() as export_root:
            logs = os.path.join(export_root, "logs")
            os.makedirs(logs)
            good = os.path.join(logs, "pipeline-1.log")
            with open(good, "w") as fh:
                fh.write("ok\n")
            outside = os.path.join(export_root, "pipeline-escape.log")
            with open(outside, "w") as fh:
                fh.write("bad\n")
            db = _make_db(export_root)
            self.addCleanup(os.unlink, db)

            resolved, err = pipe._db_log_path(db, good)
            self.assertEqual(resolved, os.path.realpath(good))
            self.assertEqual(err, "")

            resolved, err = pipe._db_log_path(db, outside)
            self.assertEqual(resolved, "")
            self.assertIn("outside", err)

    def test_page_pipeline_log_renders_tail(self):
        with tempfile.TemporaryDirectory() as export_root:
            logs = os.path.join(export_root, "logs")
            os.makedirs(logs)
            log_path = os.path.join(logs, "pipeline-1.log")
            with open(log_path, "w") as fh:
                fh.write("hello\nSummary:\n")
            db = _make_db(export_root)
            self.addCleanup(os.unlink, db)

            html = pipe.page_pipeline_log(db, log_path)
            self.assertIn("Pipeline Log", html)
            self.assertIn("hello", html)
            self.assertIn("Final summary", html)


if __name__ == "__main__":
    unittest.main()
