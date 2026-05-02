# Stage 1 — Tool Expansion Work Tickets

Hand-off spec for Sonnet / Codex. One ticket per PR. Tickets are ordered by dependency: do them in order unless explicitly marked parallel-safe.

**Source plan:** Wave A (wallet/seed) + Wave B (Windows mem + review efficiency) from owner brief 2026-05-02.
**Scope discipline:** No orchestration, no job engine, no DAG, no auto-stage-selection. Each tool is a manually invokable script that writes structured findings into the per-image SQLite DB.

---

## Hard rules every ticket must honor

These are non-negotiable. They come from `CLAUDE.md` and the project's safety stance. If a ticket appears to require breaking one, stop and flag it.

- `set -Eeuo pipefail` + `source lib/common.sh` at the top of every bash script
- `record_scan_start` / `record_scan_end` around every DB-writing operation
- Default to preview/dry-run; require `--run` to execute
- Never overwrite existing output dirs in place — back up with timestamp suffix or refuse with `--force`
- **Never rename original carved files in place.** Enrichment goes into the DB or into a separate aliased export tree.
- All outputs land under `/mnt/recovery16tb/recovery/exports/<image>/...` — never inside the container overlay
- Cracking stages MUST be `is_optional=True, is_manual=True` in TUI; no automatic crack runs
- Hard-fail on missing GPU when a stage requires it — never fall back silently to CPU

---

## Smoke tests in an offline / non-GPU environment

The runner executing this spec (Codex / Sonnet) likely cannot:
- Build the Docker image to completion (network-restricted, large image, proprietary TrID tarball)
- Run hashcat on a real GPU
- Materialize realistic fixtures (encrypted wallet.dat, hiberfil.sys, kdbx, plaso .plaso)

**Rule:** every ticket's smoke test that cannot run in the runner's environment must be checked in as an executable script under `tests/smoke/T<n>-<slug>.sh` (or `.py`) for the owner to run locally. The script must be self-contained: create its own fixtures or download them, exit non-zero on failure, print the SQL queries it expects to verify outputs.

In `STAGE1-PROGRESS.md` (see below), each acceptance criterion is marked one of:
- `pass` — runner verified it
- `pending-owner-verification` — script is checked in, awaiting host with GPU / Docker / real fixture
- `fail — <reason>` — runner attempted, hit a blocker; do not proceed past the dependency

Never invent passing test results. `pending-owner-verification` is the correct answer when a check requires hardware/data the runner doesn't have.

---

## Progress tracking — STAGE1-PROGRESS.md

Maintain `STAGE1-PROGRESS.md` at the repo root throughout the run. One section per ticket. Each section lists every acceptance criterion verbatim with its status. Update it after every commit. This is the audit trail — do not retroactively rewrite history; if a check later flips from `pass` to `fail`, append a new dated note rather than overwrite.

Template per ticket:

```
## T<n> — <title>
Commit: <sha>
Status: complete | partial | blocked

- [pass] <criterion>
- [pending-owner-verification] <criterion> — see tests/smoke/T<n>-<slug>.sh
- [fail — <reason>] <criterion>

Notes: <free-form, e.g. design choices that diverged from the spec, with justification>
```

---

## Cross-cutting findings the advisor flagged — read before you start

1. **TrID `--ae` is forbidden.** It rewrites filenames in place, which violates the no-rename rule. Store TrID's top guesses in `recovered_artifacts.trid_top_ext` / `trid_top_score` / `trid_top3_json` columns instead. The gallery / UI can join on those columns.

2. **Schema migrations are not free.** `sql/analysis-schema.sql` uses `CREATE TABLE IF NOT EXISTS`. Adding new columns to existing tables is silently a no-op on databases that already have the table — writes that target the new columns will fail. Ticket **T0** ships the migration mechanism. **No other ticket may add columns until T0 lands.**

3. **`crack_tasks` is for resumable jobs, not result rows.** Cracking takes days. Schema must model queued / running / paused / completed states, plus checkpoint paths and wordlist provenance. See T0 for the full column list.

4. **GPU silent CPU fallback wastes days.** Every cracking script runs `hashcat -I` (or equivalent) up front and hard-fails if no NVIDIA device shows up.

5. **Volatility3 needs source files extracted first.** `hiberfil.sys` and `pagefile.sys` live inside an NTFS partition. They must be extracted to `exports/<image>/winmem/` before Volatility3 runs. T7 includes both an extraction step and the scan step.

6. **BIP-39 sliding-window logic exists three times.** `bin/image-ocr-seed-scan.py`, `bin/image-pdf-extract.sh`, and the new text-seed-scan in T5 all do the same word-list lookup. T5 refactors the shared logic into `lib/seed_scan.py` and updates the existing two callers. Do not copy-paste the algorithm a third time.

---

## Ticket dependency graph

```
T0 (schema migration) ──┬──> T6 (TrID columns)
                        ├──> T8 (imagehash dedup columns)
                        └──> T9 (quality_score column)

T1 (pywallet)           ──> T3 (image-crack-wallet.sh)
T2 (john + hashcat + GPU helper) ──> T3, T10
T7a (extract-winmem)    ──> T7b (volatility3 scan)

T4 (btcrecover)             — independent of T1–T3
T5 (lib/seed_scan + text-seed-scan) — depends on nothing other than existing repo
T11 (psort crypto filter)   — extension to image-plaso.sh, independent
T12 (TUI stages)            — runs after T1–T11, before T13
T13 (README + TODO refresh) — runs LAST, after T12 ships
```

T4, T5, T6, T7, T8, T9, T11 are parallel-safe with each other once T0 is in. Sequential runners (Codex / Sonnet) execute in this order: T0, T1, T2, T3, T4, T5, T6, T7a, T7b, T8, T9, T10, T11, T12, T13.

---

# T0 — Schema migration mechanism

**Scope:** Add idempotent ALTER TABLE migrations to `lib/common.sh:ensure_db` so new columns land on existing databases. This blocks every later ticket that adds columns.

**Files to touch:**
- `lib/common.sh` — extend `ensure_db()`
- `sql/analysis-schema.sql` — add `crack_tasks` table, `wallet_keys` table; add new columns to `recovered_artifacts`

**Schema deltas to ship in this ticket:**

```sql
-- recovered_artifacts: add enrichment columns
ALTER TABLE recovered_artifacts ADD COLUMN trid_top_ext TEXT;
ALTER TABLE recovered_artifacts ADD COLUMN trid_top_score REAL;
ALTER TABLE recovered_artifacts ADD COLUMN trid_top3_json TEXT;
ALTER TABLE recovered_artifacts ADD COLUMN dedup_cluster_id INTEGER;
ALTER TABLE recovered_artifacts ADD COLUMN is_cluster_primary INTEGER DEFAULT 0;
ALTER TABLE recovered_artifacts ADD COLUMN quality_score REAL;

-- crack_tasks: resumable cracking job tracking
CREATE TABLE IF NOT EXISTS crack_tasks (
  id              INTEGER PRIMARY KEY,
  cracker         TEXT NOT NULL,        -- john | hashcat | btcrecover | keepass4brute
  target_artifact_id INTEGER,           -- recovered_artifacts.id of wallet.dat / kdbx
  target_kind     TEXT NOT NULL,        -- wallet.dat | kdbx | seed-partial
  hash_mode       TEXT,                 -- john format string OR hashcat -m number
  wordlist_path   TEXT,
  rules_path      TEXT,
  checkpoint_path TEXT,
  progress_pct    REAL,
  eta_seconds     INTEGER,
  started_at      TEXT,
  paused_at       TEXT,
  ended_at        TEXT,
  status          TEXT NOT NULL,        -- queued|running|paused|completed|cracked|exhausted|failed
  result_value    TEXT,                 -- cracked password / passphrase, NULL if not cracked
  notes           TEXT,
  FOREIGN KEY (target_artifact_id) REFERENCES recovered_artifacts(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_crack_status ON crack_tasks(status);

-- wallet_keys: extracted private keys with provenance
CREATE TABLE IF NOT EXISTS wallet_keys (
  id              INTEGER PRIMARY KEY,
  source_artifact_id INTEGER,            -- which wallet.dat / wallet.json this came from
  source_method   TEXT NOT NULL,         -- pywallet | bitcoin2john | btcrecover | manual
  key_type        TEXT NOT NULL,         -- wif | hex | bip32_xpriv | bip39_seed | bip32_xpub
  key_value       TEXT NOT NULL,
  address         TEXT,
  encrypted       INTEGER NOT NULL DEFAULT 0,
  decrypt_passphrase TEXT,                -- only set if cracked + operator approved persistence
  notes           TEXT,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (source_artifact_id) REFERENCES recovered_artifacts(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_keys_type ON wallet_keys(key_type);
```

**Migration mechanism:** `ensure_db()` already runs `analysis-schema.sql` (which is `CREATE … IF NOT EXISTS`). Extend it so that for each ALTER-style addition above, it inspects `pragma table_info(<table>)` and runs the ADD COLUMN only if the column is absent. Keep it pure SQL or inline Python — match existing style.

Recommended pattern: add a small helper function `apply_schema_migrations()` that runs after the schema file. Keep migrations declarative (a list of `(table, column, type, default)` tuples) so future additions are one-liners.

**Acceptance criteria:**
- Running `bin/image-analysis-init.sh` on an existing Hitachi DB does not error
- `sqlite3 <existing-db> "PRAGMA table_info(recovered_artifacts);"` shows the new columns after init runs
- `sqlite3 <new-db> "SELECT * FROM crack_tasks; SELECT * FROM wallet_keys;"` works on a fresh DB
- Re-running `ensure_db` is idempotent (no errors on second invocation)

**Smoke test:**
```bash
TEST_DB=/tmp/migration_test.sqlite
sqlite3 "$TEST_DB" < sql/analysis-schema.sql  # baseline
# Now apply new schema, expect ADD COLUMN to run
bash -c 'source lib/common.sh && ensure_db "$1"' _ "$TEST_DB"
sqlite3 "$TEST_DB" "PRAGMA table_info(recovered_artifacts);" | grep trid_top_ext
sqlite3 "$TEST_DB" ".schema crack_tasks"
```

**Out of scope:** Schema versioning table. Don't introduce one yet — pragma-based detection is enough for Stage 1. Tackle it when migration count exceeds ~10.

---

# T1 — pywallet wrapper

**Scope:** Add the GSC Python 3 fork of pywallet to the container. Wrap it in `bin/image-wallet-inspect.sh` so each `wallet.dat` in `wallet_candidates` gets inspected; record findings.

**Files to touch:**
- `docker/Dockerfile` — clone pywallet at a pinned SHA (not `main`), `pip install -e .`
- `bin/image-wallet-inspect.sh` (new)
- `lib/common.sh` — only if a helper to iterate `wallet_candidates` is missing

**Behavior:**
- For each `wallet.dat` file referenced in `wallet_candidates` (via `files.id` → `files.path`), invoke `pywallet --dumpwallet` and parse output
- Also invoke `pywallet --recover` on wallet files that fail normal dump (corruption recovery mode)
- Optionally run `pywallet --recov_device <loop-device>` once per image as a raw-image scan. pywallet's `--recov_device` mode generally rejects regular files and wants a block device. The wrapper must: (a) set up a read-only loop device with `losetup -r --show -f <image>`, (b) run pywallet against the loop device, (c) tear it down with `losetup -d <loop>` in an EXIT trap. Reuse `bin/image-attach-ro.sh` / `bin/image-detach.sh` if they already provide this. If raw-device scanning still fails after loop setup, log the failure, write `findings` row `category='wallet-scan-skipped'`, and exit 0 — do not abort the whole stage.
- For each extracted key, write a row into `wallet_keys` with `source_method='pywallet'`, `source_artifact_id=<recovered_artifacts.id of the wallet.dat if known>`, `encrypted=<1 if dump indicated encrypted>`
- Also write a `findings` row per inspection: `source_tool='pywallet'`, `category='wallet'`, with the wallet path, key count, encrypted flag, score 80 if any keys extracted

**Output paths:**
- Logs: `<export_root>/logs/wallet-inspect-<timestamp>.log`
- Raw pywallet dumps (sensitive): `<export_root>/hits/wallet-inspect/<timestamp>/<wallet-id>/dump.txt`
- Markdown summary: `<export_root>/hits/wallet-inspect/<timestamp>/summary.md`

**Acceptance criteria:**
- `bin/image-wallet-inspect.sh --help` prints usage with prerequisites and outputs
- Running against a DB with no wallet candidates exits cleanly with status `ok` and notes "no wallet candidates"
- Running against a DB with at least one wallet.dat candidate produces rows in `wallet_keys` and `findings`
- Encrypted wallets produce `encrypted=1` rows and a clear instruction in the log to run T3 (image-crack-wallet.sh) next

**Smoke test (Dockerfile build + functional):**
1. Build container with the new pywallet pin
2. Run `python3 -c 'import pywallet'` — must succeed
3. Drop a known-good unencrypted Bitcoin Core wallet.dat fixture into `tests/fixtures/wallet/wallet-plain.dat`. Run the wrapper against a fake DB pointing at it. Expect: at least one row in `wallet_keys` with `key_type='wif'` and `encrypted=0`.
4. Drop a known-encrypted wallet.dat fixture into `tests/fixtures/wallet/wallet-encrypted.dat`. Run the wrapper. Expect: a `wallet_keys` row with `encrypted=1` and a log line instructing the operator to run T3 next.
5. Loop-device flow: create `/tmp/blank.img` (16 MiB zeros), call the wrapper with `--scan-raw`. Expect: a loop device is created, pywallet runs (and may find nothing — that's fine), the loop device is detached on exit. Verify with `losetup -l` after the script exits.

If the runner cannot exercise these (no fixtures, no loop privilege), check the smoke-test script in at `tests/smoke/T1-pywallet.sh` and mark the criteria `pending-owner-verification` per the smoke-test policy above.

**Out of scope:** Decryption of encrypted wallets. That's T3.

**Reference:** https://github.com/Great-Software-Company/pywallet — pin a commit SHA you've inspected. Do not pin `main`.

---

# T2 — john + hashcat install + GPU preflight helper

**Scope:** Install john, hashcat, and their data packages. Add `lib/gpu_check.sh` so cracking scripts can hard-fail on missing GPU.

**Files to touch:**
- `docker/Dockerfile` — `apt-get install -y john john-data hashcat hashcat-data`
- `lib/gpu_check.sh` (new) — sourced by cracking scripts

**`lib/gpu_check.sh` contract:**
- Exposes a function `require_nvidia_gpu()` that:
  - Runs `hashcat -I` and parses output
  - Looks for at least one device with `Type: GPU` AND `Vendor.: NVIDIA`
  - On match: prints `NVIDIA GPU detected: <device-name>` and returns 0
  - On miss: prints diagnostic (full `hashcat -I` output) and exits 1
- Exposes `require_cuda()` — same as above but also checks for CUDA backend specifically (some pipelines need CUDA, OpenCL is fine for most)

**Acceptance criteria:**
- `hashcat --version` works inside the container
- `john --list=formats | grep -i bitcoin` shows `bitcoin` format
- `bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'` exits 0 on a host with `--gpus all` configured
- Same command exits 1 with a clear message on a CPU-only host
- `bitcoin2john.py` is on PATH or at a known path inside the container (`/usr/share/john/bitcoin2john.py` per the host install)

**Smoke test:**
```bash
docker run --rm --gpus all hdd-forensics:latest bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'
docker run --rm hdd-forensics:latest bash -c 'source lib/gpu_check.sh && require_nvidia_gpu' || echo "expected fail OK"
```

**Out of scope:** Actual cracking logic. That's T3 / T10.

---

# T3 — image-crack-wallet.sh

**Scope:** Run `bitcoin2john.py` on each `wallet.dat` flagged as encrypted, then crack with john (CPU) and/or hashcat mode 11300 (GPU). Track everything in `crack_tasks`.

**Files to touch:**
- `bin/image-crack-wallet.sh` (new)
- `config/wordlists/` (new dir) — placeholder symlinks to `/usr/share/wordlists/rockyou.txt` (already on Kali) and the bulk_extractor wordlist output

**Depends on:** T0 (crack_tasks table), T1 (pywallet output identifies encrypted wallets), T2 (GPU helper, john, hashcat installed)

**Behavior:**
- Iterate `wallet_keys` rows where `encrypted=1` AND `source_method='pywallet'` AND no successful `crack_tasks` row exists for the target artifact yet
- For each: insert a `crack_tasks` row with `status='queued'`, then progress through `running` → `cracked|exhausted|failed`
- Run `bitcoin2john.py` to produce a hash file
- If GPU available (call `require_nvidia_gpu` from T2): run `hashcat -m 11300 --status --status-timer=60 --restore-file-path=<checkpoint>`
- If GPU not available AND user passed `--cpu-fallback`: run `john --format=bitcoin --pot=<output>`
- Default behavior with no GPU: refuse to run; do not silently CPU fallback
- On crack: write password to `crack_tasks.result_value`, set `status='cracked'`, also write a `findings` row (`source_tool='hashcat'`, `category='crack-result'`, score 95)
- Write to `wallet_keys`: when cracked, decrypt the wallet (call back into pywallet) and add the decrypted private keys with `source_method='pywallet+crack'`, `encrypted=0`
- On Ctrl-C: pause cleanly — set `status='paused'`, save checkpoint path. Re-running the script picks up paused tasks first.

**Output paths:**
- Logs: `<export_root>/logs/crack-wallet-<timestamp>.log`
- Hashes: `<export_root>/hits/crack-wallet/<timestamp>/<wallet-id>.hash`
- Hashcat checkpoints: `<export_root>/state/hashcat/<task-id>/`
- Cracked passwords: written to `crack_tasks.result_value` (DB only, NOT a separate file)

**CLI:**
```
bin/image-crack-wallet.sh <db> [--wordlist <path>] [--rules <path>]
                                [--gpu | --cpu-fallback]
                                [--task-id <id>]    # resume a specific task
                                [--run]
```

**Acceptance criteria:**
- Without `--run`, prints the `bitcoin2john` and `hashcat` commands it would execute, plus the GPU detection result
- With `--run` on a DB containing one encrypted wallet.dat: produces a `crack_tasks` row, runs hashcat, updates row to `cracked` or `exhausted`
- Ctrl-C during a run leaves the task in `paused` state with `checkpoint_path` set
- Re-running the script finds the paused task and resumes from the checkpoint
- On a host without `--gpus all`: refuses to run unless `--cpu-fallback` was passed

**Smoke test:** Use a tiny encrypted wallet.dat with a known weak password (e.g. `test123`). Pass a wordlist containing that password. Confirm: `crack_tasks.result_value` is `test123`, `wallet_keys` gets the decrypted keys, `findings` row exists.

**Out of scope:** KeePass cracking (T10), seed-phrase brute force (T4), btcrecover (T4).

---

# T4 — btcrecover wrapper

**Scope:** Wrap btcrecover for two operator-driven scenarios: (a) partial BIP-39 seed (10 of 12 words known), (b) partial-password wallet recovery.

**Files to touch:**
- `docker/Dockerfile` — `pip install btcrecover`
- `bin/image-btcrecover.sh` (new)

**Depends on:** T0 (crack_tasks)

**Behavior:**
- This is **operator-driven, not auto-iterating**. The script takes a config file describing the recovery target and runs btcrecover against it.
- Config file lives at `<export_root>/state/btcrecover/<task-name>.yml` and specifies: target type (seed | password), known fragments, charset, max distance, etc.
- Each invocation creates one `crack_tasks` row with `cracker='btcrecover'`, `target_kind='seed-partial'` or `password-partial`
- Standard pause/resume via `crack_tasks.status` and btcrecover's own checkpoint file
- On crack: write `crack_tasks.result_value`, also append a `wallet_keys` row if the result is a private key/seed

**CLI:**
```
bin/image-btcrecover.sh <db> --config <yml-path> [--gpu] [--run]
bin/image-btcrecover.sh <db> --resume <task-id> [--run]
```

**Acceptance criteria:**
- `bin/image-btcrecover.sh --help` prints usage and links to btcrecover docs
- A config file targeting a known-good test seed (11 of 12 words, 1 missing) cracks within the timeout when `--run` is given
- TUI stage is `is_optional=True, is_manual=True` — operator must explicitly invoke

**Smoke test:** Use a synthetic 12-word BIP-39 mnemonic, drop one word, ask btcrecover to brute-force the missing word. Confirm: completes in seconds, fills `crack_tasks.result_value`.

**Out of scope:** Building the YAML config interactively. Operator writes it manually for now.

**Reference:** https://github.com/3rdIteration/btcrecover

---

# T5 — Shared BIP-39 seed scanner + text-seed-scan

**Scope:** Extract the BIP-39 sliding-window logic from `image-ocr-seed-scan.py` and `image-pdf-extract.sh` into `lib/seed_scan.py`. Update both existing callers. Add a new `image-text-seed-scan.sh` that scans recovered text files.

**Files to touch:**
- `lib/seed_scan.py` (new) — exposes `scan_text(text: str, min_words: int = 6) -> list[Match]` returning consecutive BIP-39 word runs with positions
- `bin/image-ocr-seed-scan.py` — refactor to use `lib.seed_scan`
- `bin/image-pdf-extract.sh` — refactor (it has inline Python; move that logic into the shared module)
- `bin/image-text-seed-scan.sh` (new)

**Depends on:** Nothing (no schema changes — uses existing `findings` table)

**Behavior of `image-text-seed-scan.sh`:**
- Walk `<export_root>/recovered/` for files with extensions: `.txt .html .htm .md .csv .rtf .json .log .ini .conf .yml .yaml`
- Skip files larger than 10 MB (configurable via env)
- For each, read as UTF-8 with errors='replace', run `lib.seed_scan.scan_text`
- Hits with `>= 6` consecutive BIP-39 words → `findings` row (`source_tool='text-seed-scan'`, `category='seed_phrase'`, score 70 for 6–11 words, score 95 for ≥12)
- For ≥12-word hits, also write to `notes` table (high-confidence)
- Output TSV at `<export_root>/hits/text-seed/<timestamp>/hits.tsv`

**Refactor acceptance:**
- After refactor, `bin/image-ocr-seed-scan.py` and `bin/image-pdf-extract.sh` continue to produce identical findings (same row count, same scores) on a fixed test corpus. Run them before refactor, snapshot the DB, run after refactor, diff.

**Smoke test:**
```bash
mkdir -p /tmp/seed-test/recovered
printf 'random text\nabandon ability able about above absent absorb abstract absurd abuse access accident\n more text' > /tmp/seed-test/recovered/note.txt
# Set up a minimal DB pointing export_root at /tmp/seed-test, run text-seed-scan
# Expect: one findings row, category='seed_phrase', score 95 (12 BIP-39 words)
```

**Out of scope:** Cross-file deduplication of seed hits. Each file gets its own row; humans dedupe on review.

---

# T6 — TrID enrichment

**Scope:** Run TrID over the recovered corpus, store top-3 guesses per file in `recovered_artifacts` columns. **Do not use `--ae`.** Do not rename anything.

**Files to touch:**
- `docker/Dockerfile` — install TrID (binary distribution from mark0.net) + the TrID definitions package. Pin to a specific version.
- `bin/image-enrich-trid.sh` (new)

**Depends on:** T0 (trid_top_ext, trid_top_score, trid_top3_json columns)

**Behavior:**
- Walk `<export_root>/recovered/`
- For each file in `recovered_artifacts` where `trid_top_ext IS NULL`, run `trid <file>` and parse its output (lines like `40.0% (.JPG) JPEG bitmap (5000/1/1)`)
- Update the row: `trid_top_ext` = top match's extension (no leading dot), `trid_top_score` = its percentage as float, `trid_top3_json` = JSON array of the top 3 matches
- Add `findings` row per file: `source_tool='trid'`, `category='enrichment'`, score 0 (informational)

**CLI:**
```
bin/image-enrich-trid.sh <db> [--limit <n>] [--force]
```

**Acceptance criteria:**
- `trid --version` works inside container
- Running on a DB with carved files updates the new columns
- Re-running without `--force` skips already-enriched rows (`WHERE trid_top_ext IS NULL`)
- Original file paths in `recovered_artifacts.full_path` are unchanged after the script runs (assert this in the smoke test)

**Smoke test:** Carve a tiny image, run T6, then `find <export_root>/recovered -newer <some-marker>` — should report no modifications. Then `SELECT trid_top_ext, trid_top_score FROM recovered_artifacts LIMIT 5` should show populated values.

**Out of scope:** Auto-extension renaming. The owner's plan mentions `--ae` but it violates evidence-preservation. Document this decision in the script header.

---

# T7 — Volatility3 (split into T7a + T7b)

## T7a — Extract Windows memory artifacts

**Scope:** Locate `hiberfil.sys` and `pagefile.sys` inside the image and copy them out to `exports/<image>/winmem/`. Volatility3 needs them as standalone files.

**Files to touch:**
- `bin/image-extract-winmem.sh` (new)

**Depends on:** Existing TSK index (`files` table populated by `image-index-tsk.sh`)

**Behavior:**
- Query `files` for entries with `name IN ('hiberfil.sys', 'pagefile.sys')` and `is_dir=0`
- For each: use `icat <image> <inode>` to extract; write to `<export_root>/winmem/<partition_id>/<filename>`
- SHA256 the output, register in `recovered_artifacts` with `method='winmem-extract'`
- Skip if already extracted (check by SHA256)

**Acceptance:** A Windows image yields hiberfil.sys + pagefile.sys at `<export_root>/winmem/`, registered in `recovered_artifacts`.

## T7b — Volatility3 scan

**Scope:** Run a focused Volatility3 plugin set against extracted winmem files; import findings.

**Files to touch:**
- `docker/Dockerfile` — `pip install volatility3`
- `bin/image-volatility-scan.sh` (new)

**Depends on:** T7a output

**Plugin set to run** (default; configurable via flag):
- `windows.pslist` — process list at hibernation
- `windows.cmdline` — command lines (often reveals running wallet apps, browsers)
- `windows.dumpfiles --regex 'wallet|electrum|metamask|keystore'` — dump matching open files
- `windows.hashdump` — local SAM hashes (relevant if the user reuses passwords)
- `windows.lsadump` — LSA secrets
- `windows.cachedump` — domain cached creds
- `windows.netscan` — sockets at hibernation (may reveal browser state for web wallets)

**Behavior:**
- For each plugin: run, capture output, parse into `findings` rows (`source_tool='volatility3'`, `category='volatility'`)
- `dumpfiles` results: register dumped files in `recovered_artifacts` with `method='volatility-dump'`, then run YARA/text-seed-scan over them automatically? **No** — keep stages independent. Operator runs YARA on the recovered corpus separately.

**Output:** Per-plugin TSV under `<export_root>/hits/volatility/<timestamp>/<plugin>.tsv`, plus dumped files under `<export_root>/recovered/volatility/`.

**Acceptance:** Against a known-good Windows hiberfil.sys test fixture, the scan completes and produces findings rows. (Use a small synthetic memory dump if a real fixture is unavailable.)

**Out of scope:** Linux memory analysis. Mac memory analysis. Custom Volatility plugins.

---

# T8 — Photo deduplication via perceptual hash

**Scope:** Group near-duplicate carved photos by perceptual hash. Mark cluster representatives in `recovered_artifacts`.

**Files to touch:**
- `docker/Dockerfile` — `pip install imagehash` (Pillow already present)
- `bin/image-dedup-photos.sh` (new)

**Depends on:** T0 (`dedup_cluster_id`, `is_cluster_primary` columns), at least one carving stage having run

**Behavior:**
- Walk `recovered_artifacts` rows where `mime_type LIKE 'image/%'` and `dedup_cluster_id IS NULL`
- Compute pHash for each; cluster by Hamming distance < 10 (configurable)
- Pick cluster primary by: largest size_bytes, breaking ties by highest `quality_score` (from T9), breaking ties by lowest id
- Update rows: `dedup_cluster_id` = cluster id (sequential int, scoped to this DB), `is_cluster_primary` = 1 for the rep, 0 for others
- Add `findings` row per cluster: `source_tool='imagehash'`, `category='dedup'`, key=`'cluster_size'`, value=count, score 0

**CLI:**
```
bin/image-dedup-photos.sh <db> [--distance <n>] [--force]
```

**Acceptance:**
- After running, every image artifact has `dedup_cluster_id` set
- Each cluster has exactly one row with `is_cluster_primary=1`
- Re-running without `--force` is a no-op

**Smoke test:** Drop 5 copies of the same JPEG (different filenames, identical bytes) plus 1 unrelated JPEG into `<export_root>/recovered/`. Run T8. Expect 2 clusters, the 5-copy cluster has 1 primary.

**Out of scope:** Cross-DB deduplication. This stage is per-image only.

---

# T9 — Image quality scoring

**Scope:** Score recovered images for quality so the gallery surfaces good photos first.

**Files to touch:**
- `bin/image-enrich-photos.sh` — extend with a quality scoring pass

**Depends on:** T0 (`quality_score` column)

**Behavior:**
- For each image artifact, compute a quality heuristic. Suggested approach: `magick identify -format '%[entropy] %[mean] %w %h' <file>` and combine into a 0–100 score that penalizes very low entropy (likely corrupt/blank) and very small dimensions (thumbnails)
- Update `recovered_artifacts.quality_score`
- Optionally: skip images smaller than a minimum size to save time

**Acceptance:**
- Running enrich-photos populates `quality_score` for all image artifacts
- Visibly-corrupt images (truncated JPEGs, blank canvases) score low
- Real photos score high

**Smoke test:** Plant 3 images: a real photo, a blank white JPEG, a 4×4 px thumbnail. Run T9. Expect quality_score real > blank, real > thumbnail.

**Out of scope:** ML-based BRISQUE/NIQE. Use simple ImageMagick heuristics only.

---

# T10 — KeePass cracking

**Scope:** Detect KeePass `.kdbx` files in the image, route to hashcat -m 13400 (KDBX 1–3) or keepass4brute (KDBX 4 / Argon2). Track in `crack_tasks`.

**Files to touch:**
- `docker/Dockerfile` — install keepass2john (ships with john) + clone `keepass4brute` from GitHub at pinned SHA
- `bin/image-crack-keepass.sh` (new)

**Depends on:** T0 (crack_tasks), T2 (GPU helper, hashcat)

**Behavior:**
1. Find candidates: `recovered_artifacts` rows where `mime_type` matches kdbx pattern OR filename ends in `.kdbx`
2. For each: parse the KDBX header (first 12 bytes) to detect version: `0xB54BFB67` then `0x0001-0x0003` = AES (hashcat-able), `0x0004` = Argon2 (keepass4brute path)
3. Hashcat path:
   - Run `keepass2john <kdbx> > <hash-file>`
   - Insert `crack_tasks` row with `cracker='hashcat'`, `hash_mode='13400'`
   - Run `hashcat -m 13400` with checkpointing
4. Argon2 path:
   - Insert `crack_tasks` row with `cracker='keepass4brute'`
   - Run keepass4brute (CPU-only, slow — print warning estimate)
5. On crack: write `crack_tasks.result_value`, set status `cracked`, write `findings` row

**CLI:**
```
bin/image-crack-keepass.sh <db> [--wordlist <path>] [--task-id <id>] [--run]
```

**Acceptance:**
- KDBX 3 file with weak password cracks via hashcat path
- KDBX 4 file is detected and routed to keepass4brute (or skipped with a clear message if `--allow-cpu-cracking` not passed)
- `crack_tasks.hash_mode` is `'13400'` for hashcat path, `'argon2-keepass4brute'` for keepass4brute path

**Smoke test:** Generate a minimal KDBX 3 file with `kdbxtool` or KeePassXC, password `test123`. Confirm hashcat cracks it.

**Out of scope:** Extracting credentials from a cracked KeePass DB. Operator opens it manually with KeePassXC after `result_value` is filled.

---

# T11 — Plaso psort crypto-keyword filter

**Scope:** Extend `image-plaso.sh` so after the main super-timeline runs, a second pass produces a focused crypto sub-timeline.

**Files to touch:**
- `bin/image-plaso.sh` — add a post-processing step

**Depends on:** Existing `image-plaso.sh` having produced the .plaso file

**Behavior:**
- After the main `psort.py` invocation completes, run a second `psort.py` with a message filter for keywords: `bitcoin OR wallet OR seed OR ledger OR electrum OR metamask OR coinbase OR exodus OR phrase OR mnemonic`
- Output to `<export_root>/hits/plaso-crypto/<timestamp>/timeline-crypto.csv`
- Import each matched event into `findings` (`source_tool='plaso'`, `category='timeline'`, key='crypto_event')
- Cap import at 5000 rows to match `BULK_HIT_LIMIT` convention

**Acceptance:**
- Running `image-plaso.sh` against a DB with an existing .plaso file produces both the full timeline (existing behavior) and the new crypto sub-timeline
- `findings` table gains rows with `source_tool='plaso'` and `category='timeline'`

**Out of scope:** New plaso parser plugins. New keyword sources.

---

# T12 — TUI stage entries

**Scope:** Add stage entries for everything T1–T11 in `tui/stages.py`. This is the LAST ticket — all scripts must exist and be testable first.

**Files to touch:**
- `tui/stages.py`

**Stage defaults to apply:**
- `is_optional=True` for all new stages (no auto-run)
- `is_manual=True` for: `crack-wallet`, `crack-keepass`, `btcrecover`, `volatility3`
- `requires_prior` chains:
  - `text-seed-scan`: requires any one of `carve-foremost`, `carve-scalpel`, `carve-recoverjpeg`, `carve-magicrescue` to have completed
  - `dedup-photos`: requires `enrich-photos`
  - `enrich-trid`: requires any carving stage
  - `crack-wallet`: requires `wallet-inspect` AND (`yara-scan` OR `detect-wallets`)
  - `crack-keepass`: requires at least one carving stage to have surfaced .kdbx files
  - `volatility3`: requires `extract-winmem`
  - `extract-winmem`: requires `index-tsk`
- `pgrep_pattern` for each new stage so the TUI can detect a live run

**Numbering:** Continue from current stage 38. Group new entries under section comments matching the dependency cluster (e.g. `# ── Wallet cracking ──`).

**Acceptance:**
- `python3 -c "from tui.stages import STAGES; print(len(STAGES))"` returns 38 + count-of-new-stages
- Launching the TUI (`bin/tui.sh`) shows all new stages
- Each new stage's `script` path resolves to an existing file in `bin/`
- `requires_prior` keys all match an existing stage `key`

**Out of scope:** Reorganizing existing stages. Section/grouping abstraction — leave for Stage 2.

---

# T13 — README and TODO refresh

**Scope:** Bring user-facing docs in sync with everything T1–T12 added. Run LAST. No code changes.

**Files to touch:**
- `README.md` — root project README
- `docker/README.md` — only if Dockerfile changed
- `TODO.md` — move completed items out of "Toolchain — Remaining" into a new "Toolchain — Implemented (Stage 1, <date>)" section
- `CLAUDE.md` — update the "Scripts Reference" tables to include every new `bin/image-*.sh` from T1–T11

**Behavior:**
- Update `README.md` "Included tools" table to list every tool added (pywallet, john, hashcat, btcrecover, TrID, Volatility3, imagehash, keepass2john + keepass4brute)
- Update the "Full analysis workflow" section in `README.md` with a new "Wallet recovery (Wave A)" subsection and a "Windows memory + review (Wave B)" subsection — each lists the new scripts with one-line purpose and the dependency chain (e.g. wallet-inspect → crack-wallet)
- Add a "Cracking GPU setup" callout block in `README.md` explaining the NVIDIA Container Toolkit requirement and the silent-CPU-fallback risk; link to T2's GPU helper
- Update `CLAUDE.md` script-reference tables — append rows for every new script under appropriate sections (Analysis — Heavy Stages, Wallet Recovery, etc.)
- In `TODO.md`: every "Remaining" item that maps to a Stage 1 ticket gets ticked into "Implemented" with the script name and the ticket id (`T<n>`). Items not addressed stay in "Remaining."
- Append a "Stage 1 status" subsection to `TODO.md` with one line per ticket and its status from `STAGE1-PROGRESS.md`

**Acceptance criteria:**
- `grep -E "image-(wallet-inspect|crack-wallet|btcrecover|text-seed-scan|enrich-trid|extract-winmem|volatility-scan|dedup-photos|crack-keepass)" README.md CLAUDE.md` finds matches in both files
- `TODO.md` no longer lists "john + hashcat + bitcoin2john", "btcrecover", "Plain-text BIP39 scanner", "Photo deduplication", "Import plaso events", "psort filter preset" as remaining
- The "Cracking GPU setup" callout is present in `README.md`
- No diffs to any file outside the four listed above

**Out of scope:** New diagrams, new screenshots, the "Architecture" ASCII rework. Update text only.

---

## Things explicitly NOT in Stage 1

The owner brief draws this line; tickets must not creep over it.

- Job engine, queue, scheduler
- `config/stages/*.yml` registry
- `bin/image-orchestrate.py`
- `jobs` and `job_stage_queue` tables
- Workflow blocks / preset policies (wallet-first, photos-first, etc.)
- Event-driven branching ("hiberfil found → auto-trigger Volatility")
- TUI redesign as a job console
- OpenSuperClone integration (separate hardware-sensitive concern; revisit only if ddrescue stalls in production)
- Schema versioning table (pragma-based detection in T0 is enough for now)

---

## Per-ticket conventions every Sonnet/Codex run must follow

When picking up a ticket:

1. Read `CLAUDE.md` and `lib/common.sh` first. The `record_scan_start` / `record_scan_end` / `register_artifacts_from_dir` patterns must be matched, not reinvented.
2. Match the existing bash style. Look at `bin/image-yara-scan.sh` and `bin/image-pdf-extract.sh` as references — both have been recently updated to current standards.
3. Update `TODO.md` to mark the corresponding "Remaining" item as **done** when the ticket lands.
4. Update `docker/README.md` if the Dockerfile changed (new tools listed there).
5. Run `docker compose up -d --build` locally to confirm the container builds. Do not push images.
6. Do not invent new conventions. If something feels ambiguous, write the smallest version that satisfies acceptance criteria and flag it in the PR for owner review.
