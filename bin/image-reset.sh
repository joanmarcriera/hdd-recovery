#!/usr/bin/env bash
# Per-image FULL RESET: delete an image's analysis DB and its export tree so the
# image can be re-analysed from scratch.
#
# Deletes:  <image>.analysis.sqlite (+ -wal/-shm) and exports/<image>/
# Keeps:    the raw .img and the ddrescue .map  (NEVER touched)
#
# Defaults to a dry-run preview; requires --run to delete. Pure deletion logic
# lives in lib/reset.py (unit-tested). This is destructive and irreversible —
# the export tree (carving/PhotoRec/recovered output) is removed for good.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-reset.sh <db-path> [--run] [--yes]

Deletes the analysis database and the exports/<image>/ tree for one image.
The raw disk image and the ddrescue map are never touched.

  (no flag)   preview only — print what would be deleted, with sizes
  --run       actually delete (prompts to type the image name to confirm)
  --yes       skip the interactive confirmation (for scripted use with --run)

After a reset, re-register the image from the web UI "Scan for new images"
page, or with image-analysis-init.sh, then re-run the pipeline.
EOF
}

db=""
do_run=0
assume_yes=0
for arg in "$@"; do
  case "$arg" in
    --run) do_run=1 ;;
    --yes|-y) assume_yes=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option: $arg" ;;
    *) [[ -z "$db" ]] && db="$arg" || die "unexpected argument: $arg" ;;
  esac
done
[[ -n "$db" ]] || { usage; exit 1; }
need_cmd python3

# Export the configured roots so lib/reset.py sees the same paths the pipeline
# writes to (config/analysis-pipeline.env only sets, does not export, these).
export IMAGE_ROOT EXPORT_ROOT LOG_ROOT DB_ROOT

# ── Preview ──────────────────────────────────────────────────────────────────
PYTHONPATH="$ROOT_DIR" python3 - "$db" <<'PY'
import os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from lib.reset import resolve_plan, ResetError

try:
    plan = resolve_plan(sys.argv[1])
except ResetError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    sys.exit(2)

def human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024

print("Image:       ", plan.image_path, "(kept)")
print("ddrescue map:", plan.map_path or "(none)", "(kept)")
print("Export root: ", plan.export_root)
print()
print("Would DELETE:")
for t in plan.delete_targets:
    kind = "dir " if os.path.isdir(t) else "file"
    print(f"  [{kind}] {t}")
print()
print(f"Total to free: {human(plan.total_bytes)}")
PY
preview_rc=$?
[[ $preview_rc -eq 0 ]] || exit $preview_rc

if [[ $do_run -ne 1 ]]; then
  echo
  echo "Preview only. Re-run with --run to delete."
  exit 0
fi

# ── Confirm ──────────────────────────────────────────────────────────────────
img_name="$(db_value "$db" "SELECT image_name FROM image_info WHERE id=1;" 2>/dev/null || true)"
[[ -n "$img_name" ]] || img_name="$(basename "$db")"
if [[ $assume_yes -ne 1 ]]; then
  echo
  echo "This permanently deletes the analysis DB and export tree above."
  read -r -p "Type the image name '${img_name}' to confirm: " reply
  [[ "$reply" == "$img_name" ]] || die "confirmation did not match — aborted"
fi

# ── Perform ──────────────────────────────────────────────────────────────────
PYTHONPATH="$ROOT_DIR" python3 - "$db" <<'PY'
import os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from lib.reset import perform_reset, ResetError

def human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024

try:
    result = perform_reset(sys.argv[1])
except ResetError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    sys.exit(2)

print(f"Deleted {len(result.deleted)} target(s), freed {human(result.freed_bytes)}.")
print(f"Audit:  {result.audit_log}")
PY
