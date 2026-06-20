# Project State

## Current Objective

Backlog-driven recovery-effectiveness improvements. Most reliability work
(F1–F8) has shipped; the active work is the #18 `bin/image-serve.py`
maintainability split, keeping each extraction small and covered by offline
unit tests.

## Completed Work (2026-06-20 session)

- **#7 encrypted-container detection** — new stage `detect-encrypted`
  (`bin/image-detect-encrypted-containers.sh`). Parses the image partition table
  for LUKS/BitLocker volumes (no mount) and classifies the recovered corpus +
  file inventory for KeePass/PGP/encrypted-archive signatures and
  VeraCrypt/TrueCrypt by entropy+extension. Findings land in SQLite
  (`source_tool=encrypted-detect`). Pure logic in `lib/encrypted.py`; in the
  `full`/`wallet` presets.
- **#9 targeted wordlist generation** — `bin/image-gen-wordlist.sh` builds a
  disk-targeted password list from `bulk_extractor_hits` (email local-parts,
  screen names, non-common domain labels), personal candidates first then base
  (rockyou) appended/deduped. Pure logic in `lib/wordlist.py`; feed via
  `image-crack-wallet.sh --wordlist`.
- **#10 RAW photo support** — verified already covered (PhotoRec `--profile
  broad` enables all RAW formats; `PICTURE_EXTENSIONS` lists cr2/nef/arw/…). Not
  implemented; documented in `IMPROVEMENTS.md`.
- **Doc hygiene** — archived the stale 2026-05-04 Kingston handoff
  (`NEXT-RUN-TODO.md` → `.archive/`), removed stale live-pipeline state from
  `TODO.md`, refreshed the `image-serve.py` line count.
- **#18 web UI split, second safe slice** — extracted read-only SQLite helpers
  and analysis-DB discovery into `lib/serve_db.py`. `bin/image-serve.py`
  imports/re-exports the same helper names, so existing route code and tests keep
  their public call surface. Added direct coverage in
  `tests/unit/test_serve_db.py`.

## Tests

- `./tests/run-unit.sh` — 129 tests, all passing (added
  `tests/unit/test_encrypted.py`, `tests/unit/test_wordlist.py`,
  `tests/unit/test_serve_db.py`, and registry assertions in
  `tests/unit/test_pipeline.py`).
- Focused web/serve checks:
  - `python3 -m unittest discover -s tests/unit -p 'test_serve*.py'` — 18 tests,
    all passing.
  - `python3 -m unittest discover -s tests/unit -p 'test_queue_log.py'` — 9
    tests, all passing.
- Compile check: `python3 -m py_compile bin/image-serve.py lib/serve_db.py` —
  passed.
- Both new stages smoke-tested end-to-end against synthetic images/DBs (no source
  media).

## Next Recommended Action

Continue **#18 split `bin/image-serve.py`**. The next low-risk slice is likely
queue-log marker parsing/caching or generic HTML formatting helpers. Defer the
full `Handler`/route split until it can be exercised against a running server.

## Known Blockers

None for offline refactor slices. Owner-side fixture/GPU verification items
remain tracked in `STAGE1-PROGRESS.md`.
