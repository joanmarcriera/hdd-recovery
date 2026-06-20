# Tasks

## Done

- [x] P2 review-UI auth.
  - Completion criteria: `bin/image-serve.py` requires HTTP Basic auth on all
    review UI routes when `TTYD_PASSWORD` or `WEBUI_PASSWORD` is set; `/health`
    and `/status` remain unauthenticated; no auth is required when neither env
    var is set; unit tests cover the auth check; docs/env examples are updated;
    `./tests/run-unit.sh` passes.

- [x] F1 shared watchdog runner.
  - Completion criteria: CLI/web pipeline and TUI live-log launches share the
    same process-group watchdog core; TUI commands use `start_new_session=True`;
    wall timeout returns rc `124`; idle-output timeout is surfaced in the log
    viewer; focused async unit tests cover timeout and cancellation cleanup;
    `./tests/run-unit.sh` passes.

- [x] F3 useful-progress probes and progress-aware idle timeout.
  - Completion criteria: update `scan_runs.heartbeat_at` and
    `scan_runs.last_progress_at` from per-stage probes; support ddrescue map
    rescued bytes, output directory growth, DB row-count growth, queue-marker
    age, and log mtime where applicable; kill or pause stages that produce no
    useful progress for the configured window; add focused tests without
    touching source media.

- [x] F4 durable job table for queue/detached runs.
  - Completion criteria: add a `supervised_runs` table with PID/PGID, command,
    log, heartbeat, last progress, cancel flag and final status; write it when
    web queue or detached pipeline jobs launch; replace pgrep-only queue
    detection; reconcile stale rows at startup; add focused unit tests.

- [x] F5 maintain `crack_tasks` progress.
  - Completion criteria: parse hashcat status output into
    `crack_tasks.progress_pct` and `crack_tasks.eta_seconds`; enforce a maximum
    runtime; preserve checkpoint/restore behavior; add focused tests without
    requiring GPU/hashcat.

## Next

- [ ] F7 stuckness in the monitor.
  - Completion criteria: compare current vs prior activity using CPU/IO samples
    and `scan_runs.last_progress_at`; show active, idle, no-output, or probably
    stuck with the source of the judgment; add focused tests without touching
    source media.

- [ ] #18 split `bin/image-serve.py`.
  - Completion criteria: extract cohesive route/page/db/util modules behind a
    thin entrypoint; keep the public routes unchanged; add or preserve focused
    unit coverage after each extraction; keep `./tests/run-unit.sh` green.

- [ ] #7 encrypted-container detection.
  - Completion criteria: add a stage for VeraCrypt, BitLocker, LUKS, and
    encrypted archive leads; register findings in SQLite; document and test the
    stage without touching source media.

- [ ] #8 wallet candidate deduplication.
  - Completion criteria: merge duplicate wallet candidates across discovery
    methods without deleting evidence; expose deduped counts in the review UI;
    preserve provenance for every source method.

## Backlog

- [ ] #9 targeted wordlist generation.
- [ ] #12 TUI blocked-stage explanation.
- [ ] #13 dynamic device blocking in the wizard.
- [ ] #14 Ollama availability pre-check before photo tagging.
- [ ] #15 photo dedup clusters in the web UI.
- [ ] #16 replication reliability improvements.
