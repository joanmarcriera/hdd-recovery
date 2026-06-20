"""Unit tests for lib/serve_queue_log.py (#18 extraction)."""
import os
import tempfile
import unittest

from _loader import load_module

ql = load_module("lib/serve_queue_log.py")


def _sample_log(done=2, total=4, running=True):
    lines = [
        f"[2026-06-20T10:00:00Z] queue: {total} image(s), jobs=1, stages=fast",
    ]
    for i in range(done):
        lines.append(f"[2026-06-20T10:0{i}:00Z] START img{i}.img")
        lines.append("23:00:00 Offset 0MB (100.00%) Done in 0:00:00")
        lines.append(f"[2026-06-20T10:1{i}:00Z] DONE  img{i}.img  rc=0  (60s)")
    if running:
        lines.append("[2026-06-20T10:30:00Z] START current.img")
        lines.extend("23:00:00 Offset 0MB (100.00%) Done in 0:00:00"
                     for _ in range(4))
    return "\n".join(lines) + "\n"


class TestQueueLogExtraction(unittest.TestCase):
    def _scan(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(text)
            path = fh.name
        self.addCleanup(os.unlink, path)
        return ql.scan_queue_progress(path)

    def test_scan_queue_progress_tracks_current_image(self):
        prog = self._scan(_sample_log())
        self.assertEqual(prog["total"], 4)
        self.assertEqual(prog["done"], 2)
        self.assertEqual(prog["started"], 3)
        self.assertEqual(prog["current"], "current.img")
        self.assertEqual(prog["stages"], "fast")

    def test_scan_queue_progress_clears_current_when_finished(self):
        prog = self._scan(_sample_log(done=4, total=4, running=False)
                          + "[2026-06-20T11:00:00Z] queue finished: 4 ok, 0 failed\n")
        self.assertIsNone(prog["current"])
        self.assertIn("4 ok", prog["finished"])

    def test_collapse_queue_noise_keeps_latest_line(self):
        text = "a\n" + "\n".join(
            f"23:00:0{i} Offset 0MB Done in 0:00:00" for i in range(5)
        ) + "\nb\n"
        out = ql.collapse_queue_noise(text)
        self.assertIn("collapsed", out)
        self.assertIn("23:00:04 Offset", out)
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_progress_html_escapes_current_and_finished(self):
        prog = {"total": 2, "done": 1, "current": "<img>", "finished": "",
                "started": 1, "first_ts": "", "last_ts": "", "stages": ""}
        html = ql.queue_progress_html(prog, True, lambda s: str(s).replace("<", "&lt;"))
        self.assertIn("&lt;img>", html)
        self.assertIn("1 / 2", html)


if __name__ == "__main__":
    unittest.main()
