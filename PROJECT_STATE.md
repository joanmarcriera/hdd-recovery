# Project State

## Current Objective

Complete F7 monitor stuckness: show whether a running stage is active, idle,
silent/no-output, or probably stuck using process CPU/IO samples and
`scan_runs` progress timestamps.

## Completed Work

- Added `tui/activity.py` for process CPU/IO delta sampling and pure activity
  classification.
- `tui/monitor.py` now keeps one per-process sampler, matches live recovery
  processes to running scan rows, and appends activity labels with source
  details to the SystemBar running-stage summary.
- Activity labels are `active`, `idle <age>`, `no output <age>`, or
  `probably stuck`; sources include CPU, process I/O, recent progress,
  last-progress age, heartbeat age, or missing progress timestamp.
- `tui/state.py` now loads F2/F3 supervision fields (`pid`, `pgid`,
  `heartbeat_at`, `last_progress_at`) when present while keeping older DBs
  readable with `NULL` fallbacks.
- Added `tests/unit/test_monitor_activity.py` for classifier and process
  sampler behavior.
- Marked F7 complete in `IMPROVEMENTS.md` and `TASKS.md`.

## Current Implementation State

F7 is implemented and verified locally. The working tree still contains
pre-existing untracked local metadata directories/files not touched by this
change.

## Files Changed

- `tui/activity.py`
- `tui/monitor.py`
- `tui/state.py`
- `tests/unit/test_monitor_activity.py`
- `IMPROVEMENTS.md`
- `PROJECT_STATE.md`
- `TASKS.md`
- `DECISIONS.md`

## Tests Run

- `python3 -m py_compile tui/activity.py tui/monitor.py tui/state.py tests/unit/test_monitor_activity.py` — passed.
- `python3 -m unittest discover -s tests/unit -p 'test_monitor_activity.py'` — passed, 6 tests.
- `./tests/run-unit.sh` — passed, 74 tests.

## Unresolved Defects Or Risks

- The first monitor tick for a process has no CPU/IO delta yet, so it may rely
  on progress timestamps until the next tick.
- Generic pgrep fallback without a disk/DB context still shows only process
  elapsed time; detailed stuckness needs a matched running `scan_runs` row.

## Known Blockers

None for F7.

## Next Recommended Action

Next backlog item is #18 split `bin/image-serve.py`, unless a recovery
operation needs immediate attention first.
