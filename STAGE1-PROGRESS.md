# Stage 1 Progress

## T0 — Schema migration mechanism
Commit: pending
Status: complete

- [pass] Running `bin/image-analysis-init.sh` on an existing Hitachi DB does not error
- [pass] `sqlite3 <existing-db> "PRAGMA table_info(recovered_artifacts);"` shows the new columns after init runs
- [pass] `sqlite3 <new-db> "SELECT * FROM crack_tasks; SELECT * FROM wallet_keys;"` works on a fresh DB
- [pass] Re-running `ensure_db` is idempotent (no errors on second invocation)

Notes: Verified with the existing Hitachi analysis DB and isolated `/tmp` migration databases. The first non-escalated Hitachi check was blocked by the sandbox as a readonly database; rerun with approved escalation succeeded.
