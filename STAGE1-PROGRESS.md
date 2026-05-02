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
Commit: pending
Status: complete

- [pending-owner-verification] `hashcat --version` works inside the container — see tests/smoke/T2-gpu-check.sh
- [pending-owner-verification] `john --list=formats | grep -i bitcoin` shows `bitcoin` format — see tests/smoke/T2-gpu-check.sh
- [pending-owner-verification] `bash -c 'source lib/gpu_check.sh && require_nvidia_gpu'` exits 0 on a host with `--gpus all` configured — see tests/smoke/T2-gpu-check.sh
- [pass] Same command exits 1 with a clear message on a CPU-only host
- [pending-owner-verification] `bitcoin2john.py` is on PATH or at a known path inside the container (`/usr/share/john/bitcoin2john.py` per the host install) — see tests/smoke/T2-gpu-check.sh

Notes: Local CPU-only check exited 1 with the helper's hard-fail diagnostic. Docker build and GPU-positive verification are pending owner hardware/container validation.
