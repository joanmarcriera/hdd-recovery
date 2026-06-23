# Handoff brief — hdd-recovery (for Codex CLI)

Repo root, branch `main`. You have full git + network + can run the Textual TUI
(the other agent ran in a sandbox without sqlite3, without textual, and could
not push). Start here:

```bash
cd ~/Development/repos/hdd-recovery
git status
./tests/run-unit.sh          # baseline: 46 tests, OK
```

## Conventions (follow these)
- Bash: `set -Eeuo pipefail`; source `lib/common.sh`; every stage writes a
  `scan_runs` row via `record_scan_start`/`record_scan_end`; default to preview,
  require `--run`; outputs additive (never overwrite in place).
- SQLite: parameterized queries only; web reads open `?mode=ro`.
- Tests: `tests/unit/` is stdlib `unittest`, no deps/fixtures; load hyphenated
  `bin/*.py` via `tests/unit/_loader.py`. Add a test with every behavior change.
  Heavy fixture tests live in `tests/smoke/`.
- CI: `.github/workflows/ci.yml` (bash -n, shellcheck errors, py_compile, unit
  suite). `docker-publish.yml` builds+pushes on push to `main`.
- Read `CLAUDE.md` (architecture + safety rules) and `IMPROVEMENTS.md` (backlog +
  the "Codex review F1–F8" section at the bottom).

## What is DONE and committed (Codex review F1–F8)
- **F2 — durable PID + reconciliation.** `scan_runs` now has
  `pid, pgid, host, heartbeat_at, last_progress_at, cancel_requested`
  (`sql/analysis-schema.sql` + `lib/common.sh` migration; `record_scan_start`
  fills pid/pgid/host/heartbeat_at). `lib/runs.py::reconcile_running()` flips a
  row left `running` by a kill/crash/restart to `interrupted` once its pid is
  provably gone (marker-guard against PID reuse; rows without a pid are left for
  human review). Called at pipeline start and web-UI startup; standalone
  `bin/image-reconcile-runs.py`. The `interrupted` status renders as PARTIAL in
  both the web `badge()` and `tui/state.py`.
- **F6 — prereq enforcement.** `image-pipeline.py --require-prereqs` skips a
  stage whose `requires_prior` aren't `status=ok`; queue enables it by default.
- **F8 — derived monitor device.** `tui/devices.py::resolve_dest_dev()`; `monitor.py`
  no longer hardcodes `sdc` (`$MONITOR_DEST_DEV` → mount backing image/export root → `sdc`).

### F1 — DONE (commits b81b191, 4f86959). Shared `lib/watchdog.py` (wall + idle
timeout, process-group kill, rc=124, async + sync wrappers); `image-pipeline.py`
uses `run_command_sync`; TUI log viewer uses `stream_process` with
`STAGE_TIMEOUT`/`STAGE_IDLE_TIMEOUT`; tests in `tests/unit/test_watchdog.py`.

## REMAINING — your work (priority order). Build on F2 columns + lib/watchdog.py.

### F3 (HIGH — do first) — useful-progress probes + idle timeout
**Why this is the real fix:** the F1 *idle* timeout fires on no stdout/stderr,
but `bulk_extractor`'s finalization spin prints a progress line every second, so
it never trips — and the unattended path (`image-pipeline.py::run_command` →
`run_command_sync`) passes only `wall_timeout`, no idle at all. So a
spinning-but-stuck stage still burns up to 12h. F3 distinguishes "slow but
working" from "stuck" by watching *real output*, not console chatter.

Design:
- Add a progress-probe callback to `lib/watchdog.py`'s loop: every N seconds call
  a `probe() -> int|float` (a monotonic "work done" counter); when it advances,
  refresh the no-progress deadline and bump `scan_runs.last_progress_at`; when it
  stalls for `STAGE_PROGRESS_TIMEOUT`, terminate the group (reuse the rc=124 +
  killpg path). Keep probes pure and unit-testable.
- Per-stage probe sources (a small registry keyed by scan_run_key/script):
  ddrescue → rescued bytes from the mapfile (`ddrescue-status.sh` already parses
  it); carving/photorec → output-dir byte size or file count; TSK/bulk_extractor
  indexers → `COUNT(*)` on the target table or feature-file bytes; default → max
  mtime under the stage output dir.
- Wire idle+progress into the **pipeline/queue** path too (not just the TUI) —
  that's the unattended path that matters. Add `STAGE_IDLE_TIMEOUT` /
  `STAGE_PROGRESS_TIMEOUT` envs alongside `STAGE_TIMEOUT`.
- Tests: probe advance → deadline resets; probe stalls → rc=124; output-dir and
  DB-count probes against a temp dir / temp DB. (All offline-testable.)

### F4 (Medium) — durable job table for queue/detached runs
`image-serve.py` detects queues with `pgrep` and launches detached processes with
no durable record (`spawn_queue`/`queue_active`). Add a `supervised_runs` table
(PID, PGID, command, log, heartbeat, last_progress_at, cancel_requested, final
status) written at launch and updated by the runner; replace pgrep-only
detection; enables a cancel button. Reconcile these on startup like F2 does for
scan_runs.

### F5 (Medium) — maintain crack_tasks progress
`crack_tasks` has `progress_pct`/`eta_seconds` (schema) but `image-crack-wallet.sh`
never fills them though `hashcat --status` is enabled. Parse status lines into
the row, enforce a max runtime, preserve checkpoint/restore.

### F7 (Medium) — stuckness in the monitor
`tui/monitor.py` shows elapsed time, not stuckness. Compare current vs prior
activity (CPU/IO sample + `last_progress_at`) and show `active` / `idle Nm` /
`no output Nh` / `probably stuck`, with the source of the judgment.

## Notes / minor items
- Tracked tree is clean as of commit 4f86959 (F1). Loose ends from the prior
  handoff are resolved: P2 auth test committed (a6b0621), interrupted-status
  display fix committed (f9f45f6), auth docs committed (6e094e8).
- Remaining `git status` entries are untracked local agent metadata only
  (`.agents/`, `AGENTS.md`, `CODEX-HANDOFF.md`, `docs/superpowers/`,
  `skills-lock.json`, `PROJECT_STATE.md`, `TASKS.md`, `DECISIONS.md`) — leave them.
- Minor robustness: `record_scan_start` calls `apply_schema_migrations` every
  stage start (fine), but that ALTER assumes `scan_runs` exists — it always does
  after `ensure_db`; guard if you ever call it pre-init.
- Verify after F3: `./tests/run-unit.sh` (currently 51) stays green and a
  print-then-sleep child that stops *producing output files* (not just stdout) is
  killed with rc=124 on the pipeline path.

## Deploy
`git push origin main` triggers CI + the image build. Then on TrueNAS
`docker pull joanmarcriera/hdd-forensics:latest` and restart the app (not while a
queue is mid-run unless you mean to stop it). `STAGE_TIMEOUT` env tunes the wall
timeout (default 12h).
