# Contributing to hdd-forensics

Thanks for your interest. This project is used for real disk-recovery work, so
correctness and data-safety matter more than speed. Please read the safety rules
before sending changes.

## Safety rules (non-negotiable)

These mirror the operational rules the tooling enforces:

- **Never write to source media.** All analysis runs against `.img` files; source
  disks are never mounted read-write or written to.
- **Stages default to a preview.** Any script that performs work defaults to a
  dry-run and requires an explicit `--run` flag to execute. New stages must keep
  this convention.
- **Outputs are additive.** Re-running a stage backs up prior output with a
  timestamp (`rotate_with_backup`); it never overwrites in place.
- **Every DB-writing stage records a `scan_runs` row** via `record_scan_start` /
  `record_scan_end` (bash) or `start_scan_run` / `end_scan_run` (`lib/db.py`).

## Dev setup

No third-party Python deps are needed for the unit tests (stdlib `unittest`).

```bash
git clone https://github.com/joanmarcriera/hdd-recovery
cd hdd-recovery
./tests/run-unit.sh          # offline unit suite — must stay green
```

Shell scripts are checked with [shellcheck](https://www.shellcheck.net/):

```bash
find bin lib tests docker -name '*.sh' -print0 | xargs -0 shellcheck --severity=error --external-sources
python3 -m py_compile lib/*.py bin/*.py
```

CI (`.github/workflows/ci.yml`) runs `bash -n`, shellcheck, `py_compile`, and the
unit suite. All four must pass.

## Conventions

- **Bash:** source `lib/common.sh`, use `set -Eeuo pipefail`, guard external tools
  with `need_cmd`, and build SQL with `sql_escape` (no raw string interpolation).
- **Python:** shared helpers live in `lib/` and import via `from lib.X import …`
  after the `sys.path` bootstrap. Prefer `lib/timestamp.py`, `lib/db.py` over
  re-implementing timestamps or DB connections.
- **Tests first** for any new pure logic or bugfix — see the patterns in
  `tests/unit/` (temp SQLite DBs from `sql/analysis-schema.sql`; bash helpers
  driven via subprocess). Hyphenated `bin/*.py` modules load via
  `tests/unit/_loader.py`.

## Pull requests

1. Branch off `main`.
2. Add or update tests; keep `./tests/run-unit.sh` green.
3. Keep changes focused; explain *why* in the description.
4. For larger features, a short design note under `docs/` is appreciated.

## Reporting bugs

Open an issue with the command you ran, what you expected, and the relevant
`scan_runs` row / log excerpt. Reproduction steps are gold.
