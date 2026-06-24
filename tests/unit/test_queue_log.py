"""Unit tests for the queue-log viewer helpers and web-server safety functions
in bin/image-serve.py. Pure functions only — no server, no DB, no network."""
import os
import tempfile
import unittest

from _loader import load_module

srv = load_module("bin/image-serve.py")
qlog = load_module("lib/serve_queue_log.py")


def _sample_log(done=3, total=9, running=True):
    lines = [
        f"[2026-06-19T15:28:26Z] queue: {total} image(s), jobs=1, "
        "stages=init-db,bulk-extractor-recovered,generate-report",
    ]
    for i in range(done):
        lines.append(f"[2026-06-19T15:3{i}:00Z] START img{i}.img")
        lines += [f"23:46:0{s} Offset 0MB (100.00%) Done in  0:00:00 "
                  f"at 2026-06-19 23:46:0{s}" for s in range(5)]
        lines.append(f"[2026-06-19T16:0{i}:00Z] DONE  img{i}.img  rc=0  (1800s)")
    if running:
        lines.append("[2026-06-19T17:00:00Z] START imgRUN.img")
        lines += [f"23:50:{s:02d} Offset 12000MB (100.00%) Done in  0:00:00 "
                  f"at 2026-06-19 23:50:{s:02d}" for s in range(40)]
    return "\n".join(lines) + "\n"


class TestScanProgress(unittest.TestCase):
    def _scan(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(text)
            path = fh.name
        self.addCleanup(os.unlink, path)
        return srv._scan_queue_progress(path)

    def test_running_counts(self):
        p = self._scan(_sample_log(done=3, total=9, running=True))
        self.assertEqual(p["total"], 9)
        self.assertEqual(p["done"], 3)
        self.assertEqual(p["started"], 4)
        self.assertEqual(p["current"], "imgRUN.img")
        self.assertEqual(p["finished"], "")

    def test_between_images_has_no_current(self):
        # started == done → nothing currently running
        p = self._scan(_sample_log(done=3, total=9, running=False))
        self.assertEqual(p["done"], 3)
        self.assertIsNone(p["current"])

    def test_finished_clears_current(self):
        text = _sample_log(done=9, total=9, running=False)
        text += "[2026-06-19T18:00:00Z] queue finished: 9 ok, 0 failed\n"
        p = self._scan(text)
        self.assertIn("9 ok", p["finished"])
        self.assertIsNone(p["current"])


class TestQueueProgressHtml(unittest.TestCase):
    def _prog(self, **kw):
        base = {"total": 0, "started": 0, "done": 0, "current": None,
                "finished": "", "first_ts": "t", "last_ts": "t", "stages": ""}
        base.update(kw)
        return base

    def test_finished_run_shows_finished(self):
        html = qlog.queue_progress_html(
            self._prog(total=9, done=9, finished="9 ok, 0 failed"),
            False, lambda s: s)
        self.assertIn("finished", html)

    def test_dead_and_incomplete_flags_abnormal_end(self):
        # Crashed/killed mid-run: work started, no "queue finished" line, the
        # process is not alive. Must NOT read as a clean green "idle".
        html = qlog.queue_progress_html(
            self._prog(total=11, started=5, done=5), False, lambda s: s)
        self.assertIn("abnormal", html.lower())
        self.assertNotIn('badge ok">idle', html)

    def test_no_work_started_is_idle(self):
        html = qlog.queue_progress_html(self._prog(total=0), False, lambda s: s)
        self.assertIn("idle", html.lower())


class TestCollapseNoise(unittest.TestCase):
    def test_collapses_runs_but_keeps_markers(self):
        text = _sample_log()
        out = srv._collapse_queue_noise(text)
        self.assertLess(out.count("Offset"), text.count("Offset"))
        self.assertIn("collapsed", out)
        for marker in ("START imgRUN.img", "DONE  img2.img", "queue: 9 image"):
            self.assertIn(marker, out)

    def test_short_runs_untouched(self):
        text = "a\n23:00:00 Offset 0MB Done in 0:00:00\nb\n"  # single noise line
        out = srv._collapse_queue_noise(text)
        self.assertNotIn("collapsed", out)
        self.assertIn("Offset", out)


class TestSafeSql(unittest.TestCase):
    def test_allows_select_and_with(self):
        self.assertEqual(srv.safe_sql("SELECT 1"), "SELECT 1")
        self.assertTrue(srv.safe_sql("WITH x AS (SELECT 1) SELECT * FROM x"))

    def test_rejects_writes(self):
        for bad in ("DELETE FROM files", "DROP TABLE files",
                    "UPDATE files SET x=1", "PRAGMA table_info(files)",
                    "INSERT INTO files VALUES (1)"):
            with self.assertRaises(ValueError):
                srv.safe_sql(bad)


class TestResolveUnderRoot(unittest.TestCase):
    def test_path_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            inside = os.path.join(root, "ok.txt")
            open(inside, "w").close()
            abs_path, err = srv._resolve_under_root(inside, root)
            self.assertIsNotNone(abs_path)
            self.assertIsNone(err)
            # escape attempt
            bad, err2 = srv._resolve_under_root(
                os.path.join(root, "..", "etc", "passwd"), root)
            self.assertIsNone(bad)
            self.assertIsNotNone(err2)


class TestHumanSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(srv._human_size(0), "—")
        self.assertIn("KB", srv._human_size(2048))
        self.assertIn("GB", srv._human_size(149 * 1024**3))


if __name__ == "__main__":
    unittest.main()
