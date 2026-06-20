# Project State

## Current Objective

Complete F3 useful-progress probes: distinguish real stage progress from noisy
stdout and terminate stuck unattended stages with rc `124`.

## Completed Work

- Added progress-probe support to `lib/watchdog.py`; wall, stdout-idle, and
  useful-progress timeouts all return rc `124` and kill the process group.
- Added `lib/progress.py` with pure counters for ddrescue map rescued bytes,
  output directory work, max mtime, and whitelisted SQLite row counts.
- `StageProgressProbe` observes the latest running `scan_runs` row for a stage,
  updates `heartbeat_at` on probe polls, and updates `last_progress_at` when
  the watchdog sees the counter advance.
- Wired progress probes into `bin/image-pipeline.py` for unattended runs and
  added `--stage-idle-timeout`, `--stage-progress-timeout`, and
  `--stage-progress-interval`.
- Wired `bin/image-queue.py` to pass the same timeout knobs through to each
  per-image pipeline command when set.
- Wired the TUI live log viewer to the same progress probe path.
- Added offline tests for watchdog progress timeouts, output-directory and DB
  probes, ddrescue map parsing, queue passthrough, and the pipeline wrapper
  killing a noisy child whose output directory stops changing.
- Marked F3 complete in `IMPROVEMENTS.md`.

## Current Implementation State

F3 is implemented and verified locally. The working tree still contains
pre-existing untracked local metadata directories/files not touched by this
change.

## Files Changed

- `lib/watchdog.py`
- `lib/progress.py`
- `bin/image-pipeline.py`
- `bin/image-queue.py`
- `tui/screens/log_viewer.py`
- `tests/unit/test_progress.py`
- `tests/unit/test_watchdog.py`
- `tests/unit/test_pipeline.py`
- `IMPROVEMENTS.md`
- `PROJECT_STATE.md`
- `TASKS.md`
- `DECISIONS.md`

## Tests Run

- `python3 -m py_compile lib/watchdog.py lib/progress.py bin/image-pipeline.py bin/image-queue.py tui/screens/log_viewer.py tests/unit/test_watchdog.py tests/unit/test_progress.py tests/unit/test_pipeline.py` — passed.
- `python3 -m unittest discover -s tests/unit -p 'test_watchdog.py'` — passed, 7 tests.
- `python3 -m unittest discover -s tests/unit -p 'test_progress.py'` — passed, 5 tests.
- `python3 -m unittest discover -s tests/unit -p 'test_pipeline.py'` — passed, 15 tests.
- `./tests/run-unit.sh` — passed, 60 tests.

## Unresolved Defects Or Risks

- The default progress timeout is conservative (`STAGE_PROGRESS_TIMEOUT=3600`).
  Operators can disable it with `0` for unusually quiet stages.
- Some stages only update DB rows at the end, so their useful-progress signal
  relies on output directory or mtime counters while the process runs.
- `STAGE_IDLE_TIMEOUT` remains disabled by default for pipeline runs to avoid
  killing silent-but-working stages that have a useful progress probe.

## Known Blockers

None for F3.

## Next Recommended Action

Start F4 durable queue/detached run tracking with a `supervised_runs` table and
startup reconciliation.
