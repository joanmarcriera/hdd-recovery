"""Unit tests for the pipeline/queue timeout backstop. Offline; spawns only
short-lived `sleep`/`true` children."""
import os
import time
import unittest

from _loader import load_module

pl = load_module("bin/image-pipeline.py")
q = load_module("bin/image-queue.py")


class TestRunCommand(unittest.TestCase):
    def test_normal_exit(self):
        rc, dt = pl.run_command(["true"], os.environ.copy(), 0)
        self.assertEqual(rc, 0)

    def test_nonzero_exit_preserved(self):
        rc, dt = pl.run_command(["false"], os.environ.copy(), 0)
        self.assertNotEqual(rc, 0)

    def test_timeout_kills_promptly(self):
        t0 = time.time()
        rc, dt = pl.run_command(["sleep", "30"], os.environ.copy(), 1)
        self.assertEqual(rc, pl.TIMEOUT_RC)
        self.assertLess(time.time() - t0, 6)  # killed ~at limit, not after 30s

    def test_timeout_zero_disables(self):
        # a fast command with timeout=0 must still complete normally
        rc, dt = pl.run_command(["true"], os.environ.copy(), 0)
        self.assertEqual(rc, 0)


class TestQueueBuildCmd(unittest.TestCase):
    def test_stage_timeout_passthrough(self):
        cmd = q.build_cmd("/x.sqlite", ["a", "b"], True, True, 900)
        self.assertIn("--stage-timeout", cmd)
        self.assertIn("900", cmd)
        self.assertEqual(cmd[-2:], ["a", "b"])

    def test_no_timeout_when_none(self):
        cmd = q.build_cmd("/x.sqlite", ["a"], False, False, None)
        self.assertNotIn("--stage-timeout", cmd)


if __name__ == "__main__":
    unittest.main()
