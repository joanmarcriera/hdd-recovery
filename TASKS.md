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

- [x] F7 stuckness in the monitor.
  - Completion criteria: compare current vs prior activity using CPU/IO samples
    and `scan_runs.last_progress_at`; show active, idle, no-output, or probably
    stuck with the source of the judgment; add focused tests without touching
    source media.

- [x] #7 encrypted-container detection. (2026-06-20)
  - `bin/image-detect-encrypted-containers.sh` (stage `detect-encrypted`) parses
    the image partition table for LUKS/BitLocker volumes and classifies the
    recovered corpus + inventory for KeePass/PGP/encrypted-archive signatures and
    VeraCrypt/TrueCrypt by entropy+extension. Pure logic in `lib/encrypted.py`;
    registered in `tui/stages.py` and the `full`/`wallet` presets; covered by
    `tests/unit/test_encrypted.py` (+ registry tests).

- [x] #9 targeted wordlist generation. (2026-06-20)
  - `bin/image-gen-wordlist.sh` builds a disk-targeted password list from
    `bulk_extractor_hits` (email local-parts, screen names, non-common domain
    labels) and appends a base wordlist after the personal candidates. Pure logic
    in `lib/wordlist.py`; feed via `image-crack-wallet.sh --wordlist`. Tests in
    `tests/unit/test_wordlist.py`.

- [x] #10 RAW photo format support — verified already covered (2026-06-20)
  - PhotoRec `--profile broad` enables all RAW formats and `PICTURE_EXTENSIONS`
    already lists cr2/nef/arw/dng/raf/orf, so no work was needed. See
    `IMPROVEMENTS.md` #10.

- [x] #8 wallet candidate deduplication. (2026-06-20)
  - Non-destructive query-time merge: `/wallets` groups candidates by file,
    shows a Methods column with every discovery stage, and exposes raw vs
    deduped counts. No evidence deleted. Helper `wallet_dedup_counts()` in
    `bin/image-serve.py`; test in `tests/unit/test_serve_queries.py`.

## Next

- [ ] #18 split `bin/image-serve.py`.
  - Completion criteria: extract cohesive route/page/db/util modules behind a
    thin entrypoint; keep the public routes unchanged; add or preserve focused
    unit coverage after each extraction; keep `./tests/run-unit.sh` green.

## Backlog

- [x] #14 Ollama availability pre-check before photo tagging. (2026-06-20)
  - `tui/ollama.py` probes `/api/tags`; the tag-photos screen shows green/red
    per-host status (Check [C]) and gates Run when all hosts are down. Tests in
    `tests/unit/test_ollama.py`.
- [x] #12 TUI blocked-stage explanation. (2026-06-20)
  - Disk-detail panel shows a "Blocked — requires first: …" banner; pure helper
    `unmet_prior_keys()` in `tui/stages.py`, tested in `test_pipeline.py`.
- [ ] #13 dynamic device blocking in the wizard.
- [ ] #15 photo dedup clusters in the web UI.
- [ ] #16 replication reliability improvements.
