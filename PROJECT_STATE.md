# Project State

## Current Objective

Complete F5 wallet cracking progress: update `crack_tasks` from hashcat status
output, enforce a bounded cracker runtime, and preserve checkpoint/restore
state.

## Completed Work

- Added `lib/crack_progress.py` to parse hashcat `Progress` and
  `Time.Estimated` status lines and update `crack_tasks.progress_pct` /
  `crack_tasks.eta_seconds` with parameterized SQLite writes.
- `bin/image-crack-wallet.sh` now wraps hashcat output through the progress
  parser while preserving pass-through logging.
- Added `--max-runtime <seconds>` and `CRACK_WALLET_MAX_RUNTIME`, defaulting to
  12 hours. Runtime expiry via `timeout` marks the crack task `paused` and keeps
  the hashcat restore checkpoint path recorded.
- CPU fallback cracking is also runtime-bounded when enabled, but detailed
  progress parsing remains hashcat-only.
- Added `tests/unit/test_crack_progress.py` for parser, ETA conversion, direct
  DB updates, and stream flushing without requiring GPU/hashcat.
- Marked F5 complete in `IMPROVEMENTS.md` and `TASKS.md`.

## Current Implementation State

F5 is implemented and verified locally. The working tree still contains
pre-existing untracked local metadata directories/files not touched by this
change.

## Files Changed

- `lib/crack_progress.py`
- `bin/image-crack-wallet.sh`
- `tests/unit/test_crack_progress.py`
- `IMPROVEMENTS.md`
- `PROJECT_STATE.md`
- `TASKS.md`
- `DECISIONS.md`

## Tests Run

- `python3 -m py_compile lib/crack_progress.py tests/unit/test_crack_progress.py` — passed.
- `bash -n bin/image-crack-wallet.sh` — passed.
- `python3 -m unittest discover -s tests/unit -p 'test_crack_progress.py'` — passed, 5 tests.
- `./tests/run-unit.sh` — passed, 68 tests.

## Unresolved Defects Or Risks

- `crack_tasks.progress_pct` and `eta_seconds` are populated only for hashcat,
  because john CPU fallback does not emit the same status format.
- Hashcat restore checkpoints are preserved by marking max-runtime exits as
  `paused`; operators should resume with `--task-id <id>`.

## Known Blockers

None for F5.

## Next Recommended Action

Start F7 monitor stuckness: compare process CPU/IO samples and
`scan_runs.last_progress_at` to show active, idle, no-output, or probably stuck.
