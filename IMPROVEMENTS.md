# HDD Recovery — Proposed Improvements

23 improvements, grouped by priority. Each entry covers what the issue is, why it matters, and the concrete fix with affected files.

---

## Priority 1 — Critical (Safety & Data Integrity)

### 1. SQL injection surface in shell scripts

`sql_escape()` in `lib/common.sh` uses `sed "s/'/''/g"` — naive, not safe for binary data or multi-byte characters. Shell scripts in `bin/image-detect-wallets.sh`, `bin/image-detect-pictures.sh`, and `bin/image-index-tsk.sh` build SQL via string interpolation.

**Fix:** Move all DB writes to Python inline blocks using `sqlite3` parameterized queries (already done in `register_artifacts_from_dir`). Eliminate `sql_escape()` from bash entirely.

**Files:** `lib/common.sh`, `bin/image-detect-wallets.sh`, `bin/image-detect-pictures.sh`, `bin/image-index-tsk.sh`

---

### 2. Silent failure propagation in shell scripts

Most scripts lack `set -Eeuo pipefail`. A carving tool that exits 1 can silently be logged as success in `scan_runs`, giving a false `ok` status for a stage that produced nothing.

**Fix:** Add `set -Eeuo pipefail` to all scripts missing it. Wrap long-running external tools with explicit exit-code checks before calling `record_scan_end ok`.

**Files:** Most of `bin/image-*.sh`

---

### 3. Stage output validation before downstream stages

`bin/image-pipeline.py` enforces ordering but does NOT verify that a required prior stage completed with status `ok` before running a dependent stage. Example: `image-bulk-extractor.sh --scope recovered` can run even if carving produced zero output.

**Fix:** Add a `requires_ok` pre-check in `image-pipeline.py` and `tui/stages.py` — query `scan_runs` for `status='ok'` on all prerequisite stages before launching.

**Files:** `bin/image-pipeline.py`, `tui/stages.py`

---

### 4. PID file race condition between supervisor and TUI

`docker/supervisor/main.go` writes `/tmp/hdd-recovery-webui.pid` at startup; `tui/screens/webserver.py` reads it to detect whether the web server is running. No atomic write or lock — fast restarts leave stale PIDs, causing the TUI to report the web server as alive when it isn't.

**Fix:** Supervisor should write PID atomically (write to temp file, then rename). TUI should validate the PID is alive via `/proc/<pid>/exe` check before trusting it.

**Files:** `docker/supervisor/main.go`, `tui/screens/webserver.py`

---

## Priority 2 — High (Recovery Effectiveness)

### 5. Content-based wallet detection (not just filenames)

`bin/image-detect-wallets.sh` scores purely on filename/path patterns — any file named `wallet.dat` scores 95 regardless of content. YARA scanning runs much later as a separate, optional stage.

**Fix:** Add a fast magic-byte pre-filter: confirm BerkeleyDB header (`\x00\x05\x31\x62` or `\x00\x00\x00\x20`) for `wallet.dat` candidates before awarding a high score. Integrate a lightweight YARA pass on top-scored candidates immediately after detection rather than deferring it.

**Files:** `bin/image-detect-wallets.sh`, `config/yara/wallets.yar`

---

### 6. OCR seed phrase scanning not wired into pipeline

`bin/image-ocr-seed-scan.py` exists and `BITCOIN-WALLET-RECOVERY.md` lists it as Step 6, but it is NOT registered in `tui/stages.py` or `bin/image-pipeline.py`. The script is completely orphaned. `tesseract-ocr` is already installed in the container.

**Fix:** Register it as a stage after `image-enrich-photos.sh` in `tui/stages.py` and `bin/image-pipeline.py`. Add pgrep pattern and `scan_runs` tracking.

**Files:** `tui/stages.py`, `bin/image-pipeline.py`, `bin/image-ocr-seed-scan.py`

---

### 7. Encrypted container detection missing — DONE (2026-06-20)

> Shipped as `bin/image-detect-encrypted-containers.sh` (stage `detect-encrypted`).
> Detection logic is pure/tested in `lib/encrypted.py` rather than YARA-only:
> partition-table walk for LUKS/BitLocker volumes, signature classification for
> KeePass/PGP/encrypted ZIP/7z/RAR, and entropy+extension heuristics for
> VeraCrypt/TrueCrypt. Findings land in SQLite (`source_tool=encrypted-detect`).
> In the `full` and `wallet` presets; tests in `tests/unit/test_encrypted.py`.


No detection for VeraCrypt volumes, BitLocker-encrypted partitions, LUKS containers, or encrypted ZIP/7z archives. These are common backup strategies for crypto holders and would produce high-value leads.

**Fix:** Add a new stage `bin/image-detect-encrypted-containers.sh` using YARA rules for VeraCrypt headers, `file`/`binwalk` for LUKS magic, and TSK-layer partition type scanning. Register results in the `findings` table.

**Files:** new `bin/image-detect-encrypted-containers.sh`, new `config/yara/encrypted_containers.yar`

---

### 8. Wallet duplicate candidates across stages — DONE (2026-06-20)

> Done as a non-destructive query-time merge rather than a schema change: the
> `/wallets` page now groups `wallet_candidates` by file (NULL-file rows stay
> individual), shows one row per file with a Methods column listing every
> discovery stage that found it, and a header line exposing both the raw and
> deduplicated counts. No rows are deleted — provenance is fully preserved.
> Helper `wallet_dedup_counts()` in `bin/image-serve.py`; test in
> `tests/unit/test_serve_queries.py`.


The same `wallet.dat` found by TSK index, carving, and ext recovery creates 3 separate `wallet_candidates` rows with no deduplication. This inflates apparent candidate counts and complicates review.

**Fix:** Add a `UNIQUE` constraint on `(path_hash, file_hash)` in `wallet_candidates`. Add a post-stage dedup query that merges rows sharing the same SHA256. Update `/wallets` in the web UI to show a merged/deduped count.

**Files:** `sql/analysis-schema.sql`, `bin/image-detect-wallets.sh`, `bin/image-serve.py`

---

### 9. Targeted wordlist generation for wallet cracking — DONE (2026-06-20)

> Shipped as `bin/image-gen-wordlist.sh` (pure logic in `lib/wordlist.py`).
> Extracts email local-parts, screen names, and non-common domain labels from
> `bulk_extractor_hits`, emits personal candidates first, then appends the base
> wordlist (rockyou) deduped. `image-crack-wallet.sh` already takes `--wordlist`.
> Tests in `tests/unit/test_wordlist.py`.


`bin/image-crack-wallet.sh` hardcodes `rockyou.txt` as the only wordlist. A wordlist built from artifacts already found on the disk (email addresses, screen names, domain fragments from `bulk_extractor_hits`) is far more likely to match a personal password.

**Fix:** Add optional `--wordlist <path>` to `image-crack-wallet.sh`. Add a new helper `bin/image-gen-wordlist.sh` that extracts candidates from `bulk_extractor_hits` and concatenates them with `rockyou.txt`.

**Files:** `bin/image-crack-wallet.sh`, new `bin/image-gen-wordlist.sh`

---

### 10. RAW photo format support gaps — VERIFIED NOT NEEDED (2026-06-20)

> Investigated before implementing. RAW recovery is already covered end-to-end:
> the `photorec-broad` stage (in the `full` preset) runs `--profile broad` =
> `fileopt,everything,enable,search`, enabling **all** PhotoRec formats incl.
> Canon CR2, Nikon NEF, Sony ARW, DNG, RAF, ORF. `PICTURE_EXTENSIONS` in
> `config/analysis-pipeline.env` already lists `cr2 nef arw dng raf orf`, so
> recovered RAW files are classified as picture candidates. Adding CR2/NEF/ARW
> headers to scalpel would only duplicate PhotoRec and add carving noise
> (contradicting the "carving produces duplicates" lesson). Not implemented.
> The original fix text below is retained for context.


`foremost` and `scalpel` have limited support for Canon CR2, Nikon NEF, and Sony ARW formats. These are common on cameras from the era that matches the target disks. PhotoRec supports them but may not be configured.

**Fix:** Verify `config/photorec/photorec_options.sample` enables RAW formats. Add CR2/NEF/ARW/DNG headers to `config/scalpel/scalpel-wallet-and-docs.conf`. Tune `recoverjpeg --size` for typical RAW file sizes.

**Files:** `config/photorec/photorec_options.sample`, `config/scalpel/scalpel-wallet-and-docs.conf`

---

## Priority 3 — Medium (UX & Reliability)

### 11. Hardcoded paths in scripts

`bin/image-serve.py:1785` hardcodes root default `/mnt/recovery16tb/recovery`. `bin/send-image-to-truenas.sh` hardcodes `/mnt/BigDisk/CryptoBackup`. `bin/image-crack-wallet.sh:48` hardcodes the `rockyou.txt` path. These assumptions break outside the original dev environment.

**Fix:** Move all path defaults to `config/analysis-pipeline.env` (`WORDLIST_PATH`, `TRUENAS_DEST_ROOT`, etc.). Scripts read from env with documented fallbacks.

**Files:** `bin/image-serve.py`, `bin/send-image-to-truenas.sh`, `bin/image-crack-wallet.sh`, `config/analysis-pipeline.env`

---

### 12. TUI doesn't explain why a stage is blocked — DONE (2026-06-20)

> The disk-detail panel now shows a "Blocked — requires first: …" banner listing
> the prerequisite stages that aren't DONE yet for any not-yet-run stage. Pure
> helper `unmet_prior_keys()` in `tui/stages.py` (tested in
> `tests/unit/test_pipeline.py`); the screen delegates to it.


When a stage is pending because prerequisites are not done, the TUI shows `pending` with no explanation. The operator has no way to know what needs to run first without reading `tui/stages.py` directly.

**Fix:** In `tui/screens/disk_detail.py`, for pending stages where `requires_ok` prerequisites are not all done, show a banner: "Requires: [stage names] to complete first."

**Files:** `tui/screens/disk_detail.py`, `tui/stages.py`

---

### 13. Wizard hardcodes blocked device set

`tui/screens/wizard.py` has `_BLOCKED = {"sda", "sdb", "sdc"}` — this assumes a specific hardware layout. The `/dev/sdb` ZFS member assumption is specific to the dev machine and will block valid target disks on other setups.

**Fix:** Derive blocked devices dynamically at runtime: parse `lsblk -J` to mark any device that is mounted, is a ZFS member (`zpool status`), or contains the root filesystem.

**Files:** `tui/screens/wizard.py`

---

### 14. Ollama availability not pre-checked before tag-photos stage — DONE (2026-06-20)

> `tui/ollama.py` probes each host's `/api/tags` (textual-free, unit-tested with
> an injected opener). The tag-photos config screen probes on mount and on a
> `Check [C]` button, shows green/red per-host status, and blocks Run only when a
> completed probe proves every host is down — so a slow/failed probe never locks
> the operator out. Tests in `tests/unit/test_ollama.py`.


If Ollama is unreachable, the tagging job starts, opens a `scan_runs` record, then immediately fails with an unhelpful connection error — leaving a `failed` stage that needs manual cleanup.

**Fix:** In `tui/screens/tag_photos.py`, add a probe that hits each host in `OLLAMA_HOSTS` at `/api/tags` before the run button is enabled. Show green/red availability status per host.

**Files:** `tui/screens/tag_photos.py`

---

### 15. Photo dedup clusters not visible in web UI

`image-dedup-photos.sh` populates `cluster_id` and `is_primary` in `recovered_artifacts` but neither `/gallery` nor `/pictures` exposes this. Operators cannot identify which recovered photo is the best representative of a duplicate group.

**Fix:** Add a "Groups" toggle in `/gallery` that collapses duplicates and shows cluster size. Add a `cluster_id` column to the `/pictures` table view.

**Files:** `bin/image-serve.py`

---

### 16. replicate-parallel-to-truenas.sh lacks reliability

The current script is a 46-line rsync wrapper with GNU parallel. It has no error aggregation, no per-file logging, no resume capability after partial failure, and no manifest verification.

**Fix:** Rewrite using `rsync --log-file`, collect per-job exit codes, write a transfer manifest, and add an optional `--checksum` verification pass.

**Files:** `bin/replicate-parallel-to-truenas.sh`

---

### 17. findings table schema inconsistency

The `findings` table is auto-created inline in `bin/image-tag-photos.py` with `CREATE TABLE IF NOT EXISTS` but is absent from `sql/analysis-schema.sql`. Same issue for `wallet_keys` and `crack_tasks`. This means `ensure_db` does not create them, and their schema can diverge across scripts.

**Fix:** Move all table definitions to `sql/analysis-schema.sql`. Ensure `ensure_db` in `lib/common.sh` creates them idempotently on every init. Remove inline `CREATE TABLE` from individual scripts.

**Files:** `sql/analysis-schema.sql`, `lib/common.sh`, `bin/image-tag-photos.py`

---

## Priority 4 — Low (Polish & Future-Proofing)

### 18. image-serve.py refactor (2007 lines, monolithic)

All HTML generation, SQL logic, routing, and file serving are in a single 2007-line file. Adding new routes or modifying existing ones requires navigating the entire file.

**Fix:** Split into `image-serve-routes.py` (route handlers), and either `image-serve-html.py` (HTML generation) or Jinja2 templates under `templates/`. Keep `image-serve.py` as a thin entrypoint.

**Files:** `bin/image-serve.py`

---

### 19. GPS photo map view

EXIF enrichment already extracts GPS coordinates into the DB but the web UI has no map view. For people-tracing and timeline reconstruction this would be high-value.

**Fix:** Add a `/map` route that renders a Leaflet.js map (loaded from CDN, no server-side dependency) with pins for all photos that have GPS coordinates. Link from `/pictures`.

**Files:** `bin/image-serve.py`

---

### 20. Smoke test portability

All 16 tests require the container environment and external fixtures (`wallet-plain.dat`, `wallet-encrypted.dat`, etc.). They cannot be run outside Docker, making CI difficult and local iteration slow.

**Fix:** Add `tests/fixtures/` with minimal synthetic fixtures. Make tests respect a `FIXTURE_DIR` env var so they can run outside Docker.

**Files:** `tests/smoke/` (all), new `tests/fixtures/`

---

### 21. YARA rule scoring should be configurable

`bin/image-yara-scan.sh` has a hardcoded score mapping per YARA rule name (lines 87–98). Adding or tuning rules requires editing the shell script.

**Fix:** Move the score mapping to `config/yara/scoring.conf` (key=value format), loaded at runtime by the scan script.

**Files:** `bin/image-yara-scan.sh`, new `config/yara/scoring.conf`

---

### 22. Multi-disk parallel stage execution

The TUI is single-disk-at-a-time. An operator imaging two disks simultaneously must switch contexts manually and cannot see both pipelines at once.

**Fix:** Add a "background jobs" panel to the TUI dashboard showing all active `image-pipeline.py` processes with their current stage and status. `image-pipeline.py` already supports per-db execution, so the pipeline side requires no changes.

**Files:** `tui/screens/dashboard.py`, `tui/main.py`

---

### 23. Volatility plugin coverage for wallet processes

`bin/image-volatility-scan.sh` runs a fixed set of generic plugins. Bitcoin-relevant memory artifacts (clipboard contents, process heap dumps from `bitcoin-qt`, `electrum`, `multibit`) are not targeted.

**Fix:** Add a `--profile wallet` option that runs clipboard scanning, `malfind`, and `handles` plugins filtered to wallet process names. Results go to the `findings` table.

**Files:** `bin/image-volatility-scan.sh`

---

## Web UI / TUI Performance (2026-06-19)

Investigation of slow `:7788` (web review UI, `image-serve.py`) and `:7682`
(ttyd terminal running the Textual TUI). Architecture: a Go supervisor
(`docker/supervisor/main.go`) proxies `/` → `image-serve.py` and `/terminal/`
→ ttyd. The two stacks have independent performance characteristics.

### Implemented in this pass

- **Gallery thumbnails were full-resolution originals.** `page_gallery` /
  `page_gallery_fs` rendered each 150×112 tile with `<img src="/file?path=…">`
  (or `/export_view`) pointing at the *original* carved/extracted image — a page
  of 48 images could pull tens-to-hundreds of MB. Added a Pillow-backed `/thumb`
  endpoint (and `&thumb=1` on `/export_view`) that downscales to ~320×240 JPEG,
  caches to disk (`$HDD_THUMB_CACHE` or `$TMPDIR/hdd-thumb-cache`), and serves
  with `ETag` + `Cache-Control`. Measured: a 1800×1200 PNG → ~0.7 KB thumbnail.
- **No HTTP caching.** `/file` now streams in 64 KB chunks (was `read()`-all into
  RAM) and sends `ETag`/`Cache-Control` with `304` revalidation; `/thumb` and
  `/export_view` likewise. Scroll-back and revisits no longer re-download.
- **No compression.** `send_html` now gzips responses > 512 B when the client
  sends `Accept-Encoding: gzip` (with `Vary`).
- **Homepage did N×6 SQLite opens + N pgreps.** `page_home` now uses one
  connection per DB (`_home_db_stats`), a single shared `pgrep`
  (`_pipeline_pgrep_lines`), and a 5 s output cache to absorb the 20 s
  pipeline meta-refresh.
- **TUI SystemBar churn.** `tui/monitor.py` ticked every 2 s, spawning one
  `pgrep` per disk plus SQLite reads, then repainting over the ttyd websocket.
  Tick raised to 4 s and all recovery `pgrep`s share a 3 s cache
  (`_recovery_pgrep`).

### Still worth investigating

- **Gallery "View all" mode** still emits the entire result set as one DOM (every
  `<img>` tag for thousands of artifacts). Even with thumbnails + `loading=lazy`
  the DOM is huge. Consider capping it, or replacing with infinite-scroll /
  windowed rendering.
- **Pre-generate thumbnails** in a batch stage (e.g. during
  `image-enrich-photos.sh`, which already opens every image with PIL) so the
  first gallery load is warm instead of generating on demand.
- **`COUNT(*)` on large tables** (`files`, `recovered_artifacts`) runs on every
  home/gallery load. For very large DBs, cache counts in a small summary table
  updated at stage end, or add covering indexes.
- **Gzip at the proxy.** The Go supervisor could gzip all proxied responses
  (incl. ttyd assets), centralizing compression instead of per-app.
- **ttyd/Textual latency is partly inherent.** Further wins: lower full-screen
  repaint frequency, and consider serving the review data as plain HTML (already
  done via `image-serve.py`) rather than via the terminal for read-heavy review.
- **`ThreadingHTTPServer` has no connection cap.** Under many concurrent image
  requests it can spawn unbounded threads; a small worker pool / semaphore would
  bound memory.

---

## Status — 2026-06-20 (reliability + CI + polish pass)

### Done this pass

- **Queue-log viewer rewrite** (`bin/image-serve.py`). Progress header (image
  X of N, current image, bar) parsed from `START`/`DONE` markers; collapses the
  per-second bulk_extractor `Offset … Done in 0:00:00` spam; in-place JS poll
  (preserves scroll, Pause button) replacing the whole-page meta-refresh; raw=1
  JSON poll endpoint.
- **Per-stage timeout backstop** (`bin/image-pipeline.py`, `bin/image-queue.py`).
  Stages ran via `subprocess.call()` with no time limit, so the bulk_extractor
  `--scope recovered` finalization spin blocked the whole image and the entire
  queue indefinitely. Now: Popen in its own session, `wait(timeout)`, and on
  timeout SIGTERM→SIGKILL the whole process group (no orphans); returns rc=124,
  summary status `timeout`. `--stage-timeout` / `STAGE_TIMEOUT` env (default 12h,
  `0` disables), propagated through the queue. *Addresses the gap behind the
  current stuck queue.*
- **CI** (`.github/workflows/ci.yml`): `bash -n`, ShellCheck (errors only),
  `python -m py_compile`, and the offline unit suite, on push to main + PRs.
- **Offline unit test suite** (`tests/unit/`, stdlib `unittest`, no deps or
  fixtures): queue-log parser/collapse, `safe_sql`, path-traversal guard,
  `_human_size`, pipeline timeout/process-group kill, queue `build_cmd`, and a
  schema-built-DB regression test proving the parameterized SQL honors `LIMIT`.
  Runner: `tests/run-unit.sh`. *Groundwork for the #18 refactor.*
- **Pinned Python deps** (`docker/requirements.txt`): bsddb3, ecdsa,
  pycryptodome, volatility3, imagehash — the formerly-unpinned pip installs.
  Dockerfile installs from it.
- **Parameterized SQL** — the f-string `LIMIT`/`OFFSET` in the wallet / picture /
  file / gallery queries now use bound params (`#9`).
- **`/api/queue`** — machine-readable queue progress JSON for monitoring.
- **Build context trimmed** — `.dockerignore` excludes dev/agent dirs and notes.
- **Release path** — `main` is canonical; push-to-main auto-builds via the
  existing `docker-publish.yml` Action.

### Now obsolete / already resolved (backlog corrections)

- **#2 (set -Eeuo pipefail)** — all 68 shell scripts already set it; the
  remaining work is just explicit exit-code checks before `record_scan_end ok`.
- **#17 (findings/wallet_keys/crack_tasks not in schema)** — these tables are now
  in `sql/analysis-schema.sql`; only a harmless defensive `CREATE TABLE IF NOT
  EXISTS` remains inline in `image-tag-photos.py`.
- **#18 line count** — `image-serve.py` is now ~2,930 lines (was 2,007). Refactor
  still pending (deferred).
- **#20 (smoke-test portability)** — partially addressed: a fixture-free
  `tests/unit/` suite now runs in CI and locally. The fixture-based
  `tests/smoke/` still need synthetic fixtures + `FIXTURE_DIR`.

### Recommended next (safe, self-contained — pick any)

- **#6 — DONE (2026-06-20).** Wired the orphaned `image-ocr-seed-scan.py` into
  `tui/stages.py` as stage `ocr-seed-scan` (eligible, tracked, registry tests in
  `tests/unit/test_pipeline.py`). Left out of the `full` preset to avoid auto-OCR.
- **#11 — DONE (2026-06-20).** Hardcoded path defaults now read from
  `config/analysis-pipeline.env`: `WORDLIST_PATH` (image-crack-wallet.sh),
  `TRUENAS_DEST_ROOT` (send-image-to-truenas.sh), `RECOVERY_ROOT`
  (image-serve.py fallback). All keep their built-in default when unset; CLI
  args still win.
- **#21 — DONE (2026-06-20).** YARA rule→score map externalized to
  `config/yara/scoring.conf` (with a `default` catch-all). image-yara-scan.sh
  falls back to the built-in map if the file is absent/unparseable; pinned by
  `tests/unit/test_yara_scoring.py`.
- **P2 — DONE (2026-06-20).** Optional HTTP Basic auth now protects the
  LAN-facing review UI when `TTYD_PASSWORD` or `WEBUI_PASSWORD` is set.
  `/health` and `/status` remain unauthenticated for health probes.

---

## Codex review — unattended-run robustness (2026-06-20)

External review focused on long-running unattended stages. Theme: a shared,
durable, **progress-aware** run supervisor. Eight findings (F1–F8).

### Done

- **F6 — Prerequisite enforcement.** `image-pipeline.py` gained
  `--require-prereqs`: a stage whose `requires_prior` stages aren't `status=ok`
  is skipped instead of burning hours on missing inputs. `image-queue.py` enables
  it by default for unattended runs (`--no-require-prereqs` to opt out). Tests in
  `tests/unit/test_pipeline.py`.
- **F2 — Durable PID + stale-run reconciliation.** `scan_runs` now records
  `pid/pgid/host/heartbeat_at/last_progress_at/cancel_requested` (schema +
  `common.sh` migration; `record_scan_start` populates them). New `lib/runs.py`
  `reconcile_running()` marks rows left `running` by a kill/crash/restart as
  `interrupted` once their pid is provably gone (rows without a pid are left for
  human review). Called at pipeline start and web-UI startup; standalone
  `bin/image-reconcile-runs.py`. Tests in `tests/unit/test_runs.py`.
- **F8 — Derived monitored device.** `tui/monitor.py` no longer hardcodes `sdc`;
  `tui/devices.py` derives the destination disk from `$MONITOR_DEST_DEV` / the
  mount backing the image-export root (ZFS-aware), falling back to `sdc`. Tests
  in `tests/unit/test_monitor_devices.py`.
- **F1 — Shared watchdog runner.** `lib/watchdog.py` now owns async process
  streaming, wall timeout, idle-output timeout, and SIGTERM -> SIGKILL
  process-group cleanup. `bin/image-pipeline.py` uses the sync wrapper so
  `run_command()` keeps rc=124 timeout semantics; `tui/executor.py` launches
  commands with `start_new_session=True`; `tui/screens/log_viewer.py` surfaces
  wall/idle timeout results and keeps a detached watchdog active after leaving
  the live log screen. Tests in `tests/unit/test_watchdog.py` and
  `tests/unit/test_pipeline.py`.
- **F3 — Useful-progress probes + progress timeout.** `lib/watchdog.py` now
  accepts progress probes and kills with rc=124 on `progress` timeout even when
  stdout is noisy. `lib/progress.py` provides ddrescue-map, directory, mtime and
  SQLite row-count counters and updates `scan_runs.heartbeat_at` /
  `last_progress_at` for the active running row. `image-pipeline.py`,
  `image-queue.py`, and the TUI log viewer wire `STAGE_IDLE_TIMEOUT`,
  `STAGE_PROGRESS_TIMEOUT`, and `STAGE_PROGRESS_INTERVAL`. Tests in
  `tests/unit/test_progress.py`, `tests/unit/test_watchdog.py`, and
  `tests/unit/test_pipeline.py`.
- **F4 — Durable supervised runs.** Added `supervised_runs` to the schema and
  `lib/supervised.py` for durable detached pipeline/queue records with PID/PGID,
  command, log, heartbeat, progress, cancel flag, exit code, final status and
  stale-run reconciliation. Web pipeline and queue launches create rows before
  `Popen`, pass `SUPERVISED_RUNS` to child runners, attach PID/PGID after
  launch, and expose cancel buttons. `image-pipeline.py`, `image-queue.py`, and
  web startup update/reconcile those rows. Tests in
  `tests/unit/test_supervised.py`.
- **F5 — Maintain `crack_tasks` progress.** Added `lib/crack_progress.py` to
  parse hashcat `Progress` / `Time.Estimated` status lines into
  `crack_tasks.progress_pct` and `crack_tasks.eta_seconds`. `image-crack-wallet.sh`
  now pipes hashcat output through that parser, enforces `--max-runtime` /
  `CRACK_WALLET_MAX_RUNTIME` (default 12h), and marks timeout/termination as
  `paused` while preserving the restore checkpoint path. Tests in
  `tests/unit/test_crack_progress.py`.
- **F7 — Stuckness in the monitor.** Added `tui/activity.py` for process
  CPU/IO delta sampling and pure activity classification. The SystemBar now
  appends active / idle / no-output / probably-stuck labels with judgment
  sources for matched running scan rows, using `scan_runs.last_progress_at`,
  `heartbeat_at`, and process samples. Tests in
  `tests/unit/test_monitor_activity.py`.

### Pending (bigger, build on F2's durable columns)

No F-series unattended-run robustness items remain pending from the 2026-06-20
Codex review.
