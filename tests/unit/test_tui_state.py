"""Unit tests for the TUI's stage-status state machine and ddrescue parsers in
tui/state.py. This is the logic that tells the operator which acquisition /
analysis step is done, running or still pending, so it decides what they run
next. Offline: pgrep is stubbed, no textual import, no devices."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _loader import REPO_ROOT

_TUI = str(REPO_ROOT / "tui")
if _TUI not in sys.path:
    sys.path.insert(0, _TUI)

import state  # noqa: E402  (tui/state.py; needs tui/ on sys.path)
from stages import STAGES  # noqa: E402

S = state.StageStatus
STAGE = {s.key: s for s in STAGES}


def _disk(tmp: Path, basename: str = "disk1", **kw) -> "state.DiskInfo":
    d = state.DiskInfo(
        conf_path=None, basename=basename, job_name=basename,
        source_model="", source_serial="", source_dev="",
        image_path=tmp / f"{basename}.img", map_path=None,
        db_path=tmp / f"{basename}.img.analysis.sqlite",
        export_root=tmp / "exports" / basename,
    )
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def _run(i, stage, status):
    return state.ScanRun(id=i, stage=stage, status=status,
                         started_at="2026-01-01T00:00:00Z", ended_at=None,
                         command_line=None, log_path=None, output_dir=None,
                         notes=None)


class _NoLiveProcs(unittest.TestCase):
    """Every test sees no live processes unless it says otherwise."""

    def setUp(self):
        p = mock.patch.object(state, "is_process_live", return_value=False)
        self.live = p.start()
        self.addCleanup(p.stop)
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)

    def status(self, disk, key):
        return state.get_stage_status(disk, STAGE[key])


class TestDdrescueStageStatus(_NoLiveProcs):
    def test_fresh_disk_everything_pending(self):
        d = _disk(self.tmp)
        for key in ("identify-source", "ddrescue-first", "ddrescue-retry",
                    "init-db", "structure-scan"):
            self.assertEqual(self.status(d, key), S.PENDING, key)

    def test_first_pass_coverage_threshold(self):
        d = _disk(self.tmp, image_exists=True, map_exists=True)
        d.map_coverage_pct = 98.99
        self.assertEqual(self.status(d, "ddrescue-first"), S.PARTIAL)
        d.map_coverage_pct = 99.0
        self.assertEqual(self.status(d, "ddrescue-first"), S.DONE)

    def test_retry_passes_only_skipped_when_essentially_complete(self):
        d = _disk(self.tmp, image_exists=True, map_exists=True)
        d.map_coverage_pct = 99.5      # first pass "done" but holes remain
        for key in ("ddrescue-retry", "ddrescue-reverse", "ddrescue-retrim"):
            self.assertEqual(self.status(d, key), S.PENDING, key)
        d.map_coverage_pct = 99.95
        for key in ("ddrescue-retry", "ddrescue-reverse", "ddrescue-retrim"):
            self.assertEqual(self.status(d, key), S.SKIPPED, key)

    def test_unreadable_map_with_no_image_is_partial_not_done(self):
        # Map present but coverage unknown and no image file: never claim DONE.
        d = _disk(self.tmp, image_exists=False, map_exists=True,
                  map_coverage_pct=None)
        self.assertEqual(self.status(d, "ddrescue-first"), S.PARTIAL)

    def test_live_ddrescue_wins_over_map(self):
        d = _disk(self.tmp, image_exists=True, map_exists=True,
                  map_coverage_pct=100.0)
        self.live.return_value = True
        self.assertEqual(self.status(d, "ddrescue-first"), S.RUNNING)
        self.assertEqual(self.status(d, "ddrescue-retry"), S.RUNNING)


class TestDbTrackedStageStatus(_NoLiveProcs):
    KEY = "structure-scan"

    def test_status_mapping(self):
        cases = {
            "ok": S.DONE, "partial": S.PARTIAL, "interrupted": S.PARTIAL,
            "failed": S.FAILED,
            # stale row: process died without updating the DB
            "running": S.PARTIAL,
            # unknown/garbage status must not be reported as DONE
            "bogus": S.PENDING, "": S.PENDING,
        }
        for db_status, want in cases.items():
            d = _disk(self.tmp, scan_runs=[_run(1, self.KEY, db_status)])
            self.assertEqual(self.status(d, self.KEY), want, db_status)

    def test_running_only_when_row_running_and_process_live(self):
        d = _disk(self.tmp, scan_runs=[_run(1, self.KEY, "running")])
        self.live.return_value = True
        self.assertEqual(self.status(d, self.KEY), S.RUNNING)

    def test_stage_resumed_twice_latest_run_wins(self):
        # ok -> rerun failed -> rerun interrupted: the newest row decides.
        runs = [_run(1, self.KEY, "ok"), _run(2, self.KEY, "failed"),
                _run(3, self.KEY, "interrupted")]
        d = _disk(self.tmp, scan_runs=runs)
        self.assertEqual(self.status(d, self.KEY), S.PARTIAL)
        d.scan_runs.append(_run(4, self.KEY, "ok"))
        self.assertEqual(self.status(d, self.KEY), S.DONE)

    def test_other_stages_rows_do_not_leak(self):
        d = _disk(self.tmp, scan_runs=[_run(1, "index-tsk", "ok")])
        self.assertEqual(self.status(d, self.KEY), S.PENDING)

    def test_next_pending_stage_points_at_first_unfinished(self):
        d = _disk(self.tmp)
        self.assertEqual(state.next_pending_stage(d).key, "identify-source")


class TestLoadScanRuns(unittest.TestCase):
    def test_reads_rows_in_id_order_and_tolerates_old_schema(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td, "x.sqlite")
            conn = sqlite3.connect(db)
            # old schema: no pid/pgid/heartbeat_at/last_progress_at columns
            conn.execute(
                "CREATE TABLE scan_runs(id INTEGER PRIMARY KEY, stage TEXT, "
                "status TEXT, started_at TEXT, ended_at TEXT, command_line TEXT,"
                " log_path TEXT, output_dir TEXT, notes TEXT)")
            conn.executemany(
                "INSERT INTO scan_runs(id,stage,status,started_at) VALUES(?,?,?,?)",
                [(2, "s", "failed", "t2"), (1, "s", "ok", "t1")])
            conn.commit()
            conn.close()
            runs = state._load_scan_runs(db)
        self.assertEqual([r.id for r in runs], [1, 2])
        self.assertIsNone(runs[0].pid)

    def test_missing_or_corrupt_db_yields_no_runs(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(state._load_scan_runs(Path(td, "nope.sqlite")), [])
            bad = Path(td, "bad.sqlite")
            bad.write_bytes(b"not a sqlite file at all" * 100)
            self.assertEqual(state._load_scan_runs(bad), [])


class TestMapCoverageDirect(unittest.TestCase):
    def _cov(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td, "d.map")
            p.write_text(text)
            return state._map_coverage_direct(p)

    def test_ignores_comments_and_current_status_line(self):
        text = (
            "# Mapfile. Created by GNU ddrescue version 1.27\n"
            "# current_pos  current_status  current_pass\n"
            "0x00001000     ?               1\n"
            "#      pos        size  status\n"
            "0x00000000  0x00000C00  +\n"
            "0x00000C00  0x00000400  -\n"
        )
        self.assertAlmostEqual(self._cov(text), 75.0)

    def test_only_plus_counts_as_rescued(self):
        text = "".join(f"0x{i:08x}  0x00000100  {st}\n"
                       for i, st in enumerate("+-/*?"))
        self.assertAlmostEqual(self._cov(text), 20.0)

    def test_malformed_and_empty_maps(self):
        self.assertIsNone(self._cov(""))
        self.assertIsNone(self._cov("# only comments\n"))
        self.assertIsNone(self._cov("garbage line\nzz yy +\n"))
        self.assertIsNone(self._cov("0x0 0x0 +\n"))          # zero total
        # garbage mixed with valid lines: garbage ignored
        self.assertAlmostEqual(
            self._cov("junk\n0x0 0x100 +\n0x100 notHex -\n"), 100.0)

    def test_missing_file(self):
        self.assertIsNone(state._map_coverage_direct(Path("/nonexistent/x.map")))


class TestParseRateLog(unittest.TestCase):
    def _parse(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td, "r.log")
            p.write_text(text)
            return state.parse_rate_log(p)

    def test_last_entry_wins(self):
        got = self._parse("# Rates Logfile\n0 0 0 0 0\n10 2048 1024 1 4096\n")
        self.assertEqual(got, {"elapsed_s": 10.0, "rate_bps": 2048.0,
                               "rescued_bytes": 4096})

    def test_malformed(self):
        self.assertIsNone(self._parse(""))
        self.assertIsNone(self._parse("# header only\n"))
        self.assertIsNone(self._parse("10 2048 1024\n"))        # too few cols
        self.assertIsNone(self._parse("ten 2048 1024 1 4096\n"))  # non-numeric
        self.assertIsNone(state.parse_rate_log(Path("/nonexistent/r.log")))


class TestIsProcessLive(unittest.TestCase):
    """pgrep output is stubbed; this checks the per-disk attribution."""

    def _live(self, pgrep_stdout, disk):
        res = mock.Mock(returncode=0 if pgrep_stdout else 1, stdout=pgrep_stdout)
        with mock.patch.object(state.subprocess, "run", return_value=res):
            return state.is_process_live("ddrescue", disk)

    def test_attributes_process_to_its_disk(self):
        with tempfile.TemporaryDirectory() as td:
            d = _disk(Path(td), "disk1")
            line = f"123 ddrescue /dev/sdd {d.image_path} /x/disk1.map\n"
            self.assertTrue(self._live(line, d))
            self.assertFalse(self._live("", d))
            other = "123 ddrescue /dev/sde /x/other.img /x/other.map\n"
            self.assertFalse(self._live(other, d))

    @unittest.expectedFailure
    def test_basename_prefix_of_another_disk_is_not_live(self):
        # Found, not fixed: the basename is matched as a plain substring, so a
        # ddrescue for "disk10" is reported as live for "disk1". This errs on
        # the safe side (the TUI refuses to launch on disk1 and shows it as
        # RUNNING) but is wrong; tightening it narrows a concurrency guard,
        # which is left for the owner.
        with tempfile.TemporaryDirectory() as td:
            d = _disk(Path(td), "disk1")
            line = "123 ddrescue /dev/sde /x/disk10.img /x/disk10.map\n"
            self.assertFalse(self._live(line, d))


if __name__ == "__main__":
    unittest.main()
