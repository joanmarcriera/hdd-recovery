"""Unit tests for the pipeline/queue timeout backstop. Offline; spawns only
short-lived `sleep`/`true` children."""
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest

from _loader import REPO_ROOT, load_module

pl = load_module("bin/image-pipeline.py")
q = load_module("bin/image-queue.py")
prog = load_module("lib/progress.py")
sup = load_module("lib/supervised.py")
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

    def test_progress_timeout_on_pipeline_wrapper(self):
        with tempfile.TemporaryDirectory() as td:
            script = (
                "from pathlib import Path\n"
                "import sys, time\n"
                "Path(sys.argv[1], 'once.txt').write_text('x')\n"
                "end = time.time() + 30\n"
                "while time.time() < end:\n"
                "    print('noisy', flush=True)\n"
                "    time.sleep(0.1)\n"
            )
            rc, dt = pl.run_command(
                [sys.executable, "-c", script, td],
                os.environ.copy(),
                0,
                progress_timeout=1,
                progress_interval=0.1,
                progress_probe=lambda: prog.directory_work_counter(td),
            )
        self.assertEqual(rc, pl.TIMEOUT_RC)


class TestQueueBuildCmd(unittest.TestCase):
    def test_stage_timeout_passthrough(self):
        cmd = q.build_cmd("/x.sqlite", ["a", "b"], True, True, 900)
        self.assertIn("--stage-timeout", cmd)
        self.assertIn("900", cmd)
        self.assertEqual(cmd[-2:], ["a", "b"])

    def test_no_timeout_when_none(self):
        cmd = q.build_cmd("/x.sqlite", ["a"], False, False, None)
        self.assertNotIn("--stage-timeout", cmd)

    def test_progress_timeout_passthrough(self):
        cmd = q.build_cmd(
            "/x.sqlite",
            ["a"],
            False,
            False,
            stage_progress_timeout=600,
            stage_progress_interval=5,
        )
        self.assertIn("--stage-progress-timeout", cmd)
        self.assertIn("600", cmd)
        self.assertIn("--stage-progress-interval", cmd)
        self.assertIn("5", cmd)

    def test_run_one_disables_child_finish_of_queue_supervision(self):
        seen = {}
        orig_run = q.subprocess.run
        try:
            def fake_run(cmd, env=None):
                seen["disabled"] = env.get("SUPERVISED_FINISH_DISABLED")
                return types.SimpleNamespace(returncode=0)

            q.subprocess.run = fake_run
            rc = q.run_one("/x.sqlite", ["a"], False, False)
        finally:
            q.subprocess.run = orig_run

        self.assertEqual(rc, 0)
        self.assertEqual(seen["disabled"], "1")


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

    def test_detect_encrypted_registered_and_eligible(self):
        s = pl.stage_index().get("detect-encrypted")
        self.assertIsNotNone(s, "detect-encrypted stage not registered")
        ok, reason = pl.is_eligible(s)
        self.assertTrue(ok, f"detect-encrypted not eligible: {reason}")
        self.assertEqual(s.script, "image-detect-encrypted-containers.sh")
        self.assertEqual(s.scan_run_key, "detect-encrypted")

    def test_unmet_prior_keys(self):
        # Pure helper behind the TUI blocked-stage banner (#12).
        from tui.stages import unmet_prior_keys
        s = pl.stage_index().get("detect-encrypted")  # requires_prior=["init-db"]
        self.assertEqual(unmet_prior_keys(s, set()), ["init-db"])
        self.assertEqual(unmet_prior_keys(s, {"init-db"}), [])

    def test_detect_encrypted_in_full_and_wallet_presets(self):
        # Encrypted-container leads are cheap and high-value, so unlike OCR they
        # run as part of the standard sweeps.
        self.assertIn("detect-encrypted", pl.PRESETS["full"])
        self.assertIn("detect-encrypted", pl.PRESETS["wallet"])


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


class TestPipelineGracefulStop(unittest.TestCase):
    def _db(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        db = os.path.join(td.name, "x.img.analysis.sqlite")
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA.read_text())
        now = "2026-06-20T00:00:00Z"
        conn.execute(
            "INSERT INTO image_info(id,image_path,image_name,image_basename,"
            "export_root,created_at,updated_at) VALUES (1,?,?,?,?,?,?)",
            (os.path.join(td.name, "x.img"), "x.img", "x", td.name, now, now),
        )
        conn.commit()
        conn.close()
        return db

    def test_stop_request_breaks_before_next_stage_and_marks_stopped(self):
        db = self._db()
        run_id = sup.create_supervised_run(db, "pipeline", "image-pipeline.py", "/tmp/p.log")
        old_env = os.environ.get("SUPERVISED_RUNS")
        old_argv = sys.argv[:]
        orig_run_stage = pl.run_stage
        orig_stop = pl.env_stop_requested
        ran = []
        checks = iter([False, True])
        try:
            os.environ["SUPERVISED_RUNS"] = sup.encode_env([(db, run_id)])
            sys.argv = [
                "image-pipeline.py",
                db,
                "--run",
                "structure-scan",
                "detect-wallets",
            ]

            def fake_run_stage(stage, *args, **kwargs):
                ran.append(stage.key)
                return 0, 0.1

            pl.run_stage = fake_run_stage
            pl.env_stop_requested = lambda: next(checks, True)
            rc = pl.main()
        finally:
            pl.run_stage = orig_run_stage
            pl.env_stop_requested = orig_stop
            sys.argv = old_argv
            if old_env is None:
                os.environ.pop("SUPERVISED_RUNS", None)
            else:
                os.environ["SUPERVISED_RUNS"] = old_env

        self.assertEqual(rc, 0)
        self.assertEqual(ran, ["structure-scan"])
        self.assertEqual(self._supervised_status(db, run_id), "stopped")

    def _supervised_status(self, db, run_id):
        conn = sqlite3.connect(db)
        try:
            return conn.execute(
                "SELECT status FROM supervised_runs WHERE id=?",
                (run_id,),
            ).fetchone()[0]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
