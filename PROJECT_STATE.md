# Project State

## Current Objective

Complete F1 shared watchdog runner: route CLI/web pipeline execution and the
TUI live-log launch path through shared process-group timeout supervision.

## Completed Work

- Tidied handoff loose ends:
  - committed the macOS PID-marker portability fix for `lib/runs.py`;
  - committed the pending P2 review-UI auth docs/compose updates.
- Added `lib/watchdog.py` with shared async subprocess streaming, wall timeout,
  idle-output timeout, and SIGTERM -> SIGKILL process-group cleanup.
- Rewired `bin/image-pipeline.py::run_command()` to use the shared sync wrapper
  while preserving `(rc, elapsed)` and rc `124` timeout semantics.
- Updated `tui/executor.py` so TUI launches use `start_new_session=True`.
- Updated `tui/screens/log_viewer.py` so live TUI stage logs use the shared
  watchdog, surface wall/idle timeouts, and keep detached process supervision
  active after leaving the log screen.
- Added `tests/unit/test_watchdog.py` for normal exit, wall timeout,
  idle-output timeout, process-group child cleanup, and cancellation cleanup.
- Marked F1 complete in `IMPROVEMENTS.md`.

## Current Implementation State

F1 is implemented and verified locally. The working tree still contains
pre-existing untracked local metadata directories/files not touched by this
change.

## Files Changed

- `lib/watchdog.py`
- `bin/image-pipeline.py`
- `tui/executor.py`
- `tui/screens/log_viewer.py`
- `tests/unit/test_watchdog.py`
- `IMPROVEMENTS.md`
- `PROJECT_STATE.md`
- `TASKS.md`
- `DECISIONS.md`

## Tests Run

- `python3 -m py_compile lib/watchdog.py bin/image-pipeline.py tui/executor.py tui/screens/log_viewer.py tests/unit/test_watchdog.py` — passed.
- `python3 -m unittest discover -s tests/unit -p 'test_watchdog.py'` — passed, 5 tests.
- `./tests/run-unit.sh` — passed, 51 tests.
- Lightweight TUI launch-path integration:
  `tui/executor.launch_cmd` + `lib.watchdog.stream_process` killed a
  print-then-sleep child on idle timeout and returned `124 idle`.

## Unresolved Defects Or Risks

- The TUI check exercised the executor/log-streaming path directly, not a full
  interactive Textual terminal session.
- TUI idle-output timeout defaults to `STAGE_IDLE_TIMEOUT=3600` seconds and can
  be disabled with `STAGE_IDLE_TIMEOUT=0`; F3 useful-progress probes are still
  needed for tools that emit output without making forensic progress.
- `bin/image-pipeline.py` now captures child output to relay it through the
  shared runner; this preserves queue-visible output and also writes child
  output to the pipeline log handle when present.

## Known Blockers

None for F1.

## Next Recommended Action

Start F3 useful-progress probes using the existing `heartbeat_at` and
`last_progress_at` scan-run columns.
