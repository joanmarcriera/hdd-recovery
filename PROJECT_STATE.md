# Project State

## Current Objective

Backlog-driven recovery-effectiveness improvements. Most reliability work
(F1–F8) has shipped; the focus is now on items that materially increase what the
pipeline can recover, plus keeping the trackers honest.

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

## Tests

- `./tests/run-unit.sh` — 106 tests, all passing (added
  `tests/unit/test_encrypted.py`, `tests/unit/test_wordlist.py`, and registry
  assertions in `tests/unit/test_pipeline.py`).
- Both new stages smoke-tested end-to-end against synthetic images/DBs (no source
  media).

## Next Recommended Action

From `TASKS.md` "Next": **#18 split `bin/image-serve.py`** (maintainability) and
**#8 wallet-candidate deduplication** (review-noise reduction). Both are below
the 10%-recovery-benefit bar but are reasonable hygiene work. No higher-leverage
recovery item is currently outstanding.

## Known Blockers

None. Owner-side fixture/GPU verification items remain tracked in
`STAGE1-PROGRESS.md`.
