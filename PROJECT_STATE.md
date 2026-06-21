# Project State

## Current Objective

Backlog-driven recovery-effectiveness improvements. Most reliability work
(F1–F8) has shipped. The #18 `bin/image-serve.py` maintainability split is now
complete: the entrypoint is thin and the web UI logic lives in cohesive
`lib/serve_*.py` modules with focused offline coverage. The current follow-up
adds an in-app scan/register workflow for finished `.img` files so operators no
longer have to type the initial `image-analysis-init.sh` command by hand.

## Completed Work (2026-06-20/21 sessions)

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
- **#18 web UI split complete** — `bin/image-serve.py` is now a 124-line
  compatibility entrypoint. The former monolith is split into:
  `lib/serve_app.py` (Handler/main), `lib/serve_pages.py` (page renderers),
  `lib/serve_gallery.py` (file export, thumbnails, galleries),
  `lib/serve_pipeline.py` (pipeline/queue process helpers),
  `lib/serve_ui.py` (HTML/UI primitives), `lib/serve_queue_log.py` (queue log
  parsing/cache/render core), plus the earlier `lib/serve_db.py`,
  `lib/serve_auth.py`, and `lib/serve_mapfile.py`. `bin/image-serve.py`
  re-exports legacy helper names used by tests and older imports.
- **Operator Add image / Help page** — the web UI home page links to `/help`,
  which explains the imaging-host vs analysis-container split, source-disk
  safety checks, graduated `ddrescue` passes, tougher failing-disk alternatives,
  USB-stick imaging, and the `image-analysis-init.sh` step that makes an image
  appear in the UI. Route dispatch is covered by `tests/unit/test_serve_app.py`.
- **Scan for new images web flow** — the home page links to `/images/new`.
  `lib/serve_images.py` discovers finished `*.img` files across both legacy
  beside-image storage and the split `/data/images` + `/data/db` Docker layout,
  infers matching ddrescue mapfiles, initializes missing analysis DBs via
  `image-analysis-init.sh --db ... --print-db-path`, and can queue the existing
  `fast` preset sequentially through the supervised queue runner. Help text now
  points operators to the scan page and shows the corrected manual fallback.

## Tests

- `python3 -m unittest discover -s tests/unit -p test_serve_app.py` — 5 tests,
  all passing.
- `python3 -m unittest discover -s tests/unit -p 'test_serve*.py'` — 46 tests,
  all passing.
- `python3 -m py_compile bin/image-serve.py lib/serve_app.py lib/serve_pages.py
  lib/serve_images.py tests/unit/test_serve_app.py
  tests/unit/test_serve_images.py` — passed.
- `./tests/run-unit.sh` — 160 tests, all passing.
- `git diff --check` — passed.
- `./tests/run-unit.sh` — 155 tests, all passing.
- `git diff --check` — passed.
- `./tests/run-unit.sh` — 147 tests, all passing (added
  `tests/unit/test_encrypted.py`, `tests/unit/test_wordlist.py`,
  `tests/unit/test_serve_db.py`, `tests/unit/test_serve_queue_log.py`,
  `tests/unit/test_serve_ui.py`, `tests/unit/test_serve_pipeline.py`,
  `tests/unit/test_serve_gallery.py`, `tests/unit/test_serve_app.py`, and
  registry assertions in `tests/unit/test_pipeline.py`).
- Focused web/serve checks:
  - `python3 -m unittest discover -s tests/unit -p 'test_serve*.py'` — 36 tests,
    all passing.
  - `python3 -m unittest discover -s tests/unit -p 'test_queue_log.py'` — 9
    tests, all passing.
- Compile check over all `bin/image-serve.py` / `lib/serve_*.py` modules —
  passed.
- Both new stages smoke-tested end-to-end against synthetic images/DBs (no source
  media).

## Next Recommended Action

No active #18 work remains. Next exact action is to push the two local commits
when desired, then choose the next backlog item from `IMPROVEMENTS.md`.

## Known Blockers

None for #18. Owner-side fixture/GPU verification items remain tracked in
`STAGE1-PROGRESS.md`.
