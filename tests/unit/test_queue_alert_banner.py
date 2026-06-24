"""Tests for the home-page 'queue ended abnormally' banner (serve_pages)."""
import os
import tempfile
import unittest

from _loader import load_module

pages = load_module("lib/serve_pages.py")

_ABNORMAL_LOG = (
    "[2026-06-24T09:00:00Z] queue: 3 image(s), jobs=1, stages=init-db\n"
    "[2026-06-24T09:00:01Z] START a.img.analysis.sqlite\n"
    "[2026-06-24T09:00:30Z] DONE  a.img.analysis.sqlite  rc=0  (29s)\n"
)
_FINISHED_LOG = _ABNORMAL_LOG + "[2026-06-24T09:01:00Z] queue finished: 1 ok, 0 failed\n"


class TestQueueAlertBanner(unittest.TestCase):
    def setUp(self):
        self._orig = pages.queue_active
        pages.queue_active = lambda root=None: (None, None)  # no live queue
        self.addCleanup(lambda: setattr(pages, "queue_active", self._orig))

    def _root_with_log(self, contents):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        qdir = os.path.join(root, "queue-logs")
        os.makedirs(qdir)
        with open(os.path.join(qdir, "queue-20260624T090000Z.log"), "w") as fh:
            fh.write(contents)
        return root

    def test_banner_shown_for_abnormal_end(self):
        html = pages.queue_alert_banner(self._root_with_log(_ABNORMAL_LOG))
        self.assertIn("ended abnormally", html.lower())
        self.assertIn("1 of 3", html)

    def test_no_banner_when_finished(self):
        self.assertEqual(pages.queue_alert_banner(self._root_with_log(_FINISHED_LOG)), "")

    def test_no_banner_when_no_logs(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        self.assertEqual(pages.queue_alert_banner(root), "")

    def test_no_banner_when_queue_running(self):
        pages.queue_active = lambda root=None: (1234, "image-queue.py …")
        self.assertEqual(pages.queue_alert_banner(self._root_with_log(_ABNORMAL_LOG)), "")


if __name__ == "__main__":
    unittest.main()
