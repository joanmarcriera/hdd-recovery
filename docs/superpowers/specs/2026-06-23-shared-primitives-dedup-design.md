# Shared Primitives Extraction — De-duplication Cycle 1

**Date:** 2026-06-23
**Status:** Implemented (commits 142229f, 7e6ff69, e843375) — 189 unit tests green
**Scope:** First of several de-dup/modularization cycles toward open-source release. This cycle extracts the highest-value, lowest-risk shared primitives. Deferred to later cycles: `serve_pages.py` decomposition, bash retrofit of the remaining ~28 stage scripts, public-release hygiene, and best-practices tooling.

## Goal

Eliminate the most-duplicated logic in the codebase by extracting two cohesive Python modules and two bash helpers. Every change is a **behavior-preserving substitution** — no public CLI/API changes, no change to what the live pipeline does. Only internal call sites move.

## Motivation (measured duplication)

Three read-only analysis sweeps found:

- **`utc_now()` reimplemented 7×** — `bin/image-pipeline.py` (`now_iso`), `bin/image-tag-photos.py` (`now_iso`), `bin/image-queue.py` (`_ts`), `bin/image-ocr-seed-scan.py` (`utc_now`), `lib/progress.py` (`_utc_now`), `lib/runs.py` (`_now`), `lib/supervised.py` (`_now`) — all identical: `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`. Plus a bash twin `lib/common.sh::timestamp_utc`.
- **Read-only SQLite connect boilerplate** (`sqlite3.connect(f"file:{db}?mode=ro", uri=True)` + try/finally) repeated in `image-pipeline.py`, `lib/progress.py`, `lib/reset.py` (and ~10× more in the web layer, out of scope this cycle).
- **`image_info` (id=1) row fetch** reimplemented 3× in Python (`image-pipeline.py::db_image_context`, `image-ocr-seed-scan.py`, `lib/reset.py`).
- **`scan_runs` start/end** reimplemented in the two standalone tools that bypass `common.sh`: `image-tag-photos.py` (`record_start`/`record_end`) and `image-ocr-seed-scan.py` (`record_scan_start`/`record_scan_end`).
- **Bash work-dir derivation** (`export_root` + `timestamp` + `out_dir` + `log_path` + `mkdir`) in ~31 scripts; **timestamped output backup** in ~7.

## Non-goals (explicitly out of scope this cycle)

- Splitting `lib/serve_pages.py` (1,427 lines) and extracting `lib/serve_html.py` — next cycle.
- Retrofitting the remaining ~28 bash scripts onto the new helpers.
- Speculative abstractions the sweeps proposed and we rejected: bash `dry_run_gate`/`with_status_tracking` callback wrappers, per-icon micro-helpers, extracting the unique `usage()`/argument-parsing blocks.
- Release hygiene (de-hardcoding paths/IPs/serials, docs curation) and tooling (ruff/pre-commit) — their own cycles.

## Design

All new code is low-level and imported via the established bootstrap already used everywhere:
`sys.path.insert(0, ROOT)` then `from lib.X import …`.

### Component 1 — `lib/timestamp.py`

```python
def utc_now() -> str:
    """ISO-8601 UTC at second precision, e.g. '2026-06-23T22:10:05Z'.

    Python twin of lib/common.sh::timestamp_utc — keep the two formats in sync.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

Each of the 7 Python files removes its private copy and switches to `from lib.timestamp import utc_now`, renaming call sites (`now_iso()`/`_ts()`/`_now()`/`_utc_now()` → `utc_now()`).

### Component 2 — `lib/db.py`

DB-centric helpers (one clear boundary rather than four near-empty files):

```python
@contextmanager
def ro_db(db_path):
    """Read-only connection (file:…?mode=ro), row_factory=Row, auto-closed."""

DEFAULT_PRAGMAS = ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON")

def open_writable_db(db_path, *, pragmas=DEFAULT_PRAGMAS, ddl=None):
    """Writable connection with standard pragmas and optional DDL bootstrap."""

@dataclass
class ImageInfo:
    image_path: str
    export_root: str
    ddrescue_map_path: str

def fetch_image_info(db_path) -> ImageInfo:
    """Read the single image_info row (id=1); raise LookupError if missing."""

def start_scan_run(conn, stage, command_line, log_path="", output_dir="") -> int:
    """INSERT a scan_runs row (status='running'); return its id."""

def end_scan_run(conn, run_id, status, notes=""):
    """UPDATE the scan_runs row with terminal status, ended_at, notes."""
```

- `ro_db` replaces the `connect(…ro…)`+try/finally blocks in `image-pipeline.py`, `lib/progress.py`, `lib/reset.py`.
- `fetch_image_info` unifies the 3 Python reimplementations.
- `start_scan_run`/`end_scan_run` replace the bespoke copies in `image-tag-photos.py` and `image-ocr-seed-scan.py`. The INSERT/UPDATE column set matches bash `record_scan_start`/`record_scan_end` so both layers remain schema-compatible (the bash version additionally sets `pid`/`pgid`/`host`/`heartbeat_at`; the Python tools historically did not, and this cycle preserves that — no behavior change).

### Component 3 — `lib/common.sh` bash helpers

```bash
# Derive + create the standard per-stage work dirs. Exports: out_dir, log_path, timestamp.
prepare_work_dirs <export_root> <scope> [extra_dirs…]

# If <path> exists, move it aside to <path>.prev-<timestamp> and log the rotation.
rotate_with_backup <path>
```

Convert **3 exemplar scripts** this cycle: `image-bulk-extractor.sh`, `image-carve.sh`, `image-enrich-trid.sh`. Remaining scripts adopt incrementally (tracked as a follow-up). Other live stage-runners untouched.

### Cross-language drift guard

Reciprocal comments link `lib/timestamp.py::utc_now` ↔ `lib/common.sh::timestamp_utc`. A comment on `fetch_image_info` documents that it reads the DB (source of truth) rather than re-deriving paths from env, and is therefore safe against the bash `default_export_root`/`default_db_path` fallbacks.

## Testing (TDD; stdlib `unittest` via `tests/run-unit.sh`)

Tests are written first (red) for each component, then implementation (green):

- `tests/unit/test_timestamp.py` — `utc_now()` matches `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`.
- `tests/unit/test_db.py` — `ro_db` opens read-only (writes raise); `open_writable_db` applies pragmas + DDL; `fetch_image_info` returns fields and raises `LookupError` on a DB with no `image_info` row; `start_scan_run`/`end_scan_run` round-trip in a temp DB (status transitions running→ok, notes persisted).
- `tests/unit/test_common_sh_workdirs.py` — drives `prepare_work_dirs` and `rotate_with_backup` via a bash subprocess against temp dirs (asserts dirs created, vars exported, rotation renames with `.prev-` prefix and leaves no original).
- All existing unit tests for the 8 edited files must remain green.

## Risk & rollout

- **Blast radius:** internal substitutions only; behavior preserved. Highest-care files are the live `image-pipeline.py`/`image-queue.py`, covered by existing + new tests.
- **Deployment is decoupled:** changes land in the repo; the running container keeps the old code until a deliberate rebuild (see the `deploy-hdd-forensics` skill). The active analysis queue is **not** disturbed by this work.
- **Reversibility:** each component is an isolated commit; any can be reverted independently.

## Acceptance criteria

1. `lib/timestamp.py` and `lib/db.py` exist with the APIs above and their own passing unit tests.
2. The 7 Python files import `utc_now`; no private timestamp helper remains in them.
3. `image-pipeline.py`, `progress.py`, `reset.py` use `ro_db`/`fetch_image_info`; `image-tag-photos.py` and `image-ocr-seed-scan.py` use `start_scan_run`/`end_scan_run`/`fetch_image_info`.
4. `prepare_work_dirs` and `rotate_with_backup` exist in `common.sh` with a passing test; the 3 exemplar scripts use them.
5. `tests/run-unit.sh` is fully green.
6. No public CLI/API change; no deployment performed as part of this cycle.
