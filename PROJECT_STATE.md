# Project State

## Current Objective

Complete F4 durable queue/detached run tracking: replace pgrep-only active
detection with SQLite-backed `supervised_runs` records.

## Completed Work

- Added a `supervised_runs` table to `sql/analysis-schema.sql`.
- Added `lib/supervised.py` for creating supervised rows, attaching PID/PGID,
  updating heartbeat/progress, finishing rows, reconciling stale PIDs, encoding
  child-runner env metadata, and requesting cancellation.
- Web single-image pipeline launches now create a supervised row before
  `Popen`, pass `SUPERVISED_RUNS` to `image-pipeline.py`, attach PID/PGID after
  launch, and detect active runs from SQLite before falling back to legacy pgrep.
- Web multi-image queue launches now create matching queue rows in each selected
  image DB, pass all row IDs to `image-queue.py`, attach PID/PGID after launch,
  and detect active queues from SQLite.
- `image-pipeline.py` and `image-queue.py` update supervised heartbeat/progress
  and final status from the child process.
- Web startup reconciles stale `supervised_runs` rows alongside stale
  `scan_runs`.
- Added cancel buttons for active pipeline and queue runs; cancellation sets
  `cancel_requested=1` and sends SIGTERM to the recorded process group.
- Added `tests/unit/test_supervised.py` for create/attach/finish, env helpers,
  reconciliation, and cancellation.
- Marked F4 complete in `IMPROVEMENTS.md`.

## Current Implementation State

F4 is implemented and verified locally. The working tree still contains
pre-existing untracked local metadata directories/files not touched by this
change.

## Files Changed

- `lib/supervised.py`
- `sql/analysis-schema.sql`
- `bin/image-serve.py`
- `bin/image-pipeline.py`
- `bin/image-queue.py`
- `tests/unit/test_supervised.py`
- `IMPROVEMENTS.md`
- `PROJECT_STATE.md`
- `TASKS.md`
- `DECISIONS.md`

## Tests Run

- `python3 -m py_compile lib/supervised.py bin/image-serve.py bin/image-pipeline.py bin/image-queue.py tests/unit/test_supervised.py` — passed.
- `python3 -m unittest discover -s tests/unit -p 'test_supervised.py'` — passed, 3 tests.
- `python3 -m unittest discover -s tests/unit -p 'test_queue_log.py'` — passed, 9 tests.
- `python3 -m unittest discover -s tests/unit -p 'test_serve_queries.py'` — passed, 6 tests.
- `./tests/run-unit.sh` — passed, 63 tests.

## Unresolved Defects Or Risks

- Queue runs spanning multiple image DBs create one supervised row per selected
  DB. Cancel sends SIGTERM once per row, so duplicate signals to the same PGID
  are possible but harmless.
- Legacy unmanaged queue/pipeline runs can still be detected by the old pgrep
  fallback until no such older launches remain.
- Supervised heartbeat is updated at runner boundaries; long-running per-stage
  progress remains represented in `scan_runs` from F3.

## Known Blockers

None for F4.

## Next Recommended Action

Start F5 `crack_tasks` progress updates for hashcat-based wallet cracking.
