"""Unit tests for the pipeline/queue timeout backstop. Offline; spawns only
short-lived `sleep`/`true` children."""
import os
import sqlite3
import tempfile
import time
import unittest

from _loader import REPO_ROOT, load_module

pl = load_module("bin/image-pipeline.py")
q = load_module("bin/image-queue.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


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


class TestStageRegistry(unittest.TestCase):
    """Guards the stage registry, incl. the wired-in OCR seed-scan stage (#6)."""

    def test_keys_and_numbers_unique(self):
        keys = [s.key for s in pl.STAGES]
        nums = [s.number for s in pl.STAGES]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(nums), len(set(nums)))

    def test_ocr_seed_scan_registered_and_eligible(self):
        s = pl.stage_index().get("ocr-seed-scan")
        self.assertIsNotNone(s, "ocr-seed-scan stage not registered")
        ok, reason = pl.is_eligible(s)
        self.assertTrue(ok, f"ocr-seed-scan not eligible: {reason}")
        self.assertEqual(s.script, "image-ocr-seed-scan.py")
        self.assertEqual(s.args_template, ["{db}"])  # script has no --run flag
        self.assertEqual(s.scan_run_key, "ocr-seed-scan")

    def test_ocr_not_in_full_preset(self):
        # Slow OCR must stay opt-in — not run automatically on every image.
        self.assertNotIn("ocr-seed-scan", pl.PRESETS["full"])


class TestPrereqs(unittest.TestCase):
    """F6 — requires_prior enforcement (skip stages with missing inputs)."""

    def _db(self):
        fd, path = tempfile.mkstemp(suffix=".analysis.sqlite")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA.read_text())
        conn.commit()
        conn.close()
        self.addCleanup(os.unlink, path)
        return path

    def _mark_ok(self, db, scan_run_key):
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO scan_runs(stage,status,started_at) VALUES (?,?,?)",
            (scan_run_key, "ok", "2026-06-20T00:00:00Z"))
        conn.commit()
        conn.close()

    def test_unmet_then_met(self):
        idx = pl.stage_index()
        stage = idx["text-seed-scan"]
        prereqs = stage.requires_prior
        self.assertTrue(prereqs, "fixture stage should have prerequisites")
        db = self._db()
        # nothing run yet → all prereqs unmet
        self.assertEqual(pl.unmet_prerequisites(db, stage, idx), prereqs)
        # satisfy the first prereq → it drops out of the unmet list
        self._mark_ok(db, idx[prereqs[0]].scan_run_key)
        self.assertEqual(pl.unmet_prerequisites(db, stage, idx), prereqs[1:])
        # satisfy the rest → none unmet
        for pk in prereqs[1:]:
            self._mark_ok(db, idx[pk].scan_run_key)
        self.assertEqual(pl.unmet_prerequisites(db, stage, idx), [])

    def test_stage_without_prereqs(self):
        idx = pl.stage_index()
        stage = idx["structure-scan"]
        self.assertEqual(pl.unmet_prerequisites(self._db(), stage, idx), [])


class TestQueuePrereqPassthrough(unittest.TestCase):
    def test_default_on(self):
        cmd = q.build_cmd("/x.sqlite", ["a"], False, False)
        self.assertIn("--require-prereqs", cmd)

    def test_can_disable(self):
        cmd = q.build_cmd("/x.sqlite", ["a"], False, False, require_prereqs=False)
        self.assertNotIn("--require-prereqs", cmd)


if __name__ == "__main__":
    unittest.main()
