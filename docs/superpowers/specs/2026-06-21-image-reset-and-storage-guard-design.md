# Per-image reset + storage-misconfiguration guard

Date: 2026-06-21
Status: approved (verbal), implementing

## Background

Two operator-facing problems, both surfaced while running the dockerised
`hdd-forensics` image on TrueNAS:

1. **No way to start an image over.** After many app restarts mid-task (and the
   storage bug below, which wiped exports/DBs on the ephemeral overlay), the
   per-image DBs and `scan_runs`/`supervised_runs` state are inconsistent. The
   operator wants to wipe one image's analysis outputs and re-run from scratch.

2. **Silent wrong-storage writes.** The image defaults `DB_ROOT`/`EXPORT_ROOT`/
   `IMAGE_ROOT`/`LOG_ROOT` to `/data/*`. If those are not mounted (as in the
   TrueNAS custom-app config), writes fall through to the container's writable
   overlay layer — which on TrueNAS lives on the FastPool SSD. This filled the
   SSD and destroyed 400+ GB of exports on the next container recreate.

The queue "stops after the first image with `full`" bug is **out of scope** for
this spec — its orchestration layer was proven correct and the trigger needs the
real queue log to pinpoint.

## Item 1 — Per-image full reset

**Scope (chosen):** delete the `<image>.analysis.sqlite` (plus `-wal`/`-shm`)
and the entire `exports/<image>/` tree. **Never** touch the raw `.img` or the
ddrescue `.map` — re-imaging is expensive/irreversible.

### Components

- `lib/reset.py` — pure-ish core, unit-testable:
  - `resolve_plan(db_path, env=os.environ) -> ResetPlan` reads `image_info`
    (RO) for `export_root`, `image_path`, `ddrescue_map_path`; resolves the DB
    sidecar files and the export tree; computes sizes.
  - **Guards (raise `ResetError`):** export_root must be absolute, not `/`, not
    equal to `EXPORT_ROOT`/`IMAGE_ROOT`/`LOG_ROOT`, and must be a subdirectory
    of `EXPORT_ROOT` when that is set. No delete target may equal or be an
    ancestor of `image_path` or `ddrescue_map_path`.
  - `perform_reset(db_path, *, is_active=..., audit_log=...) -> ResetResult`:
    refuses if a pipeline/queue is active for the DB; appends an audit record
    to `LOG_ROOT/resets.log` (timestamp, image, targets, sizes, host) **before**
    deleting; removes DB sidecars and `shutil.rmtree` the export tree.
- `bin/image-reset.sh <db>` — preview by default; `--run` executes; interactive
  "type the image name" confirm, `--yes` for non-interactive.
- Web: red **Danger zone** panel on `/db`; `POST /reset_image` requires a typed
  confirmation equal to the image basename; calls `lib/reset.py`; redirects home
  with a notice. After reset the image is re-discoverable via `/images/new`.

## Item 2 — Storage-misconfiguration guard

### Detection — `lib/storage_guard.py` (unit-testable)

`check_roots(roots, *, in_container, root_dev, stat_fn) -> list[RootStatus]`.
For each data root, walk up to the nearest existing ancestor and compare its
`st_dev` to the device of `/`. **Inside the container**, a data root sharing a
device with `/` means it resolved to the writable overlay layer → status
`overlay` (danger). A separate device → `ok`. Outside the container the check is
advisory only (`skipped`), since everything legitimately lives on `/`.

`in_container()` = `HDD_IN_CONTAINER=1` (added to the Dockerfile) or
`/.dockerenv` exists. `HDD_ALLOW_OVERLAY=1` overrides enforcement.

### Enforcement (defense in depth)

1. Loud startup log line (Go supervisor + `image-serve`).
2. Blocking red banner on the web home page naming the offending roots + fix.
3. `lib/common.sh` write entry points (`ensure_db`, `ensure_work_dirs`) abort
   with a clear message when a target root is on the overlay, unless
   `HDD_ALLOW_OVERLAY=1`.

### Human CLI — `bin/storage-check.sh`

Prints each root → path, device, mounted?, on-overlay?, writable? — so the
operator can confirm the TrueNAS env-var fix actually landed.

## Testing

- `tests/unit/test_reset.py`: plan resolution paths; guards reject `/`,
  EXPORT_ROOT itself, and any target covering the image/map; `perform_reset`
  refuses when active; deletes only DB + export tree on a temp tree; writes the
  audit line.
- `tests/unit/test_storage_guard.py`: `overlay` when ancestor dev == root dev in
  container; `ok` when separate dev; `skipped` outside container; override.

## Out of scope

- Queue "stops after first image" bug (separate; pending real log).
- Partial/stage-level reset (full reset only — YAGNI).
- Any deletion of raw images or ddrescue maps.
