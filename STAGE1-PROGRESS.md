# Stage 1 Progress

## T0 — Schema migration mechanism
Commit: 19d8fe9
Status: complete

- [pass] Running `bin/image-analysis-init.sh` on an existing Hitachi DB does not error
- [pass] `sqlite3 <existing-db> "PRAGMA table_info(recovered_artifacts);"` shows the new columns after init runs
- [pass] `sqlite3 <new-db> "SELECT * FROM crack_tasks; SELECT * FROM wallet_keys;"` works on a fresh DB
- [pass] Re-running `ensure_db` is idempotent (no errors on second invocation)

Notes: Verified with the existing Hitachi analysis DB and isolated `/tmp` migration databases. The first non-escalated Hitachi check was blocked by the sandbox as a readonly database; rerun with approved escalation succeeded.

## T1 — pywallet wrapper
Commit: 41af3a6
Status: complete

- [pass] `bin/image-wallet-inspect.sh --help` prints usage with prerequisites and outputs
- [pass] Running against a DB with no wallet candidates exits cleanly with status `ok` and notes "no wallet candidates"
- [pending-owner-verification] Running against a DB with at least one wallet.dat candidate produces rows in `wallet_keys` and `findings` — see tests/smoke/T1-pywallet.sh
- [pending-owner-verification] Encrypted wallets produce `encrypted=1` rows and a clear instruction in the log to run T3 (image-crack-wallet.sh) next — see tests/smoke/T1-pywallet.sh

Notes: Docker build, pywallet import, real wallet fixtures, and loop-device raw scan require owner verification in an environment with Docker/network, fixtures, and loop privileges. The Dockerfile pins Great-Software-Company/pywallet to `5376a54a36f75cec7226de7cca1511e9cf058f37`.

## T2 — john + hashcat install + GPU preflight helper
Commit: 571a797
Status: complete

- [pending-owner-verification] `hashcat --version` works inside the container — see tests/smoke/T2-gpu-check.sh
- [pending-owner-verification] `john --list=formats | grep -i bitcoin` shows `bitcoin` format — see tests/smoke/T2-gpu-check.sh
- [pending-owner-verification] `bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'` exits 0 on a host with `--gpus all` configured — see tests/smoke/T2-gpu-check.sh
- [pass] Same command exits 1 with a clear message on a CPU-only host
- [pending-owner-verification] `bitcoin2john.py` is on PATH or at a known path inside the container (`/usr/share/john/bitcoin2john.py` per the host install) — see tests/smoke/T2-gpu-check.sh

Notes: Local CPU-only check exited 1 with the helper's hard-fail diagnostic. Docker build and GPU-positive verification are pending owner hardware/container validation.

## T3 — image-crack-wallet.sh
Commit: 3a754b1
Status: complete

- [pass] Without `--run`, prints the `bitcoin2john` and `hashcat` commands it would execute, plus the GPU detection result
- [pending-owner-verification] With `--run` on a DB containing one encrypted wallet.dat: produces a `crack_tasks` row, runs hashcat, updates row to `cracked` or `exhausted` — see tests/smoke/T3-crack-wallet.sh
- [pending-owner-verification] Ctrl-C during a run leaves the task in `paused` state with `checkpoint_path` set — see tests/smoke/T3-crack-wallet.sh
- [pending-owner-verification] Re-running the script finds the paused task and resumes from the checkpoint — see tests/smoke/T3-crack-wallet.sh
- [pass] On a host without `--gpus all`: refuses to run unless `--cpu-fallback` was passed

Notes: Local verification used a synthetic encrypted wallet_keys row to confirm dry-run output and no-GPU hard refusal. Full cracking, pause, and resume need a weak encrypted wallet fixture plus GPU/container execution.

## T4 — btcrecover wrapper
Commit: 4b36065
Status: complete

- [pass] `bin/image-btcrecover.sh --help` prints usage and links to btcrecover docs
- [pending-owner-verification] A config file targeting a known-good test seed (11 of 12 words, 1 missing) cracks within the timeout when `--run` is given — see tests/smoke/T4-btcrecover.sh
- [pending-owner-verification] TUI stage is `is_optional=True, is_manual=True` — operator must explicitly invoke — see tests/smoke/T4-btcrecover.sh

Notes: Local wrapper verification used a fake `echo found:` command to confirm `crack_tasks` and `wallet_keys` imports. Real btcrecover execution is pending container verification.

## T5 — Shared BIP-39 seed scanner + text-seed-scan
Commit: 15585f0
Status: complete

- [pending-owner-verification] After refactor, `bin/image-ocr-seed-scan.py` and `bin/image-pdf-extract.sh` continue to produce identical findings (same row count, same scores) on a fixed test corpus. Run them before refactor, snapshot the DB, run after refactor, diff. — see tests/smoke/T5-refactor-regression.sh
- [pass] Smoke test creates a minimal DB and recovered text file, runs `image-text-seed-scan.sh`, and produces one `findings` row with `category='seed_phrase'` and score 95 — see tests/smoke/T5-text-seed-scan.sh

Notes: Local checks passed for `lib.seed_scan`, OCR Python compile, PDF/text shell syntax, and the text scanner smoke test. The OCR/PDF regression needs owner fixtures plus `pdftotext` and `tesseract`.

## T6 — TrID enrichment
Commit: pending
Status: complete

- [pending-owner-verification] `trid --version` works inside container — see tests/smoke/T6-trid.sh
- [pending-owner-verification] Running on a DB with carved files updates the new columns — see tests/smoke/T6-trid.sh
- [pending-owner-verification] Re-running without `--force` skips already-enriched rows (`WHERE trid_top_ext IS NULL`) — see tests/smoke/T6-trid.sh
- [pending-owner-verification] Original file paths in `recovered_artifacts.full_path` are unchanged after the script runs (assert this in the smoke test) — see tests/smoke/T6-trid.sh

Notes: Local checks covered shell syntax and dry-run. TrID itself is installed from mark0.net during Docker build, which is pending owner verification in the container environment. The script intentionally never invokes TrID `--ae`.
