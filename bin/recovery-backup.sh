#!/usr/bin/env bash
# Tiered backup of recovery metadata to a secondary destination.
#
# Tier 1 (always):  SQLite catalogs (*.analysis.sqlite + WAL)  — small, irreplaceable
# Tier 2 (always):  ddrescue maps and event/rate logs           — imaging provenance
# Tier 3 (--full):  per-image export directories               — carved files, reports
# Tier 4 (--images): raw disk images (*.img)                   — very large
#
# Based on replicate-parallel-to-truenas.sh; uses rsync + GNU parallel.
# Default mode is preview only.  Add --run to actually execute.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  recovery-backup.sh [options]

Options:
  --src DIR        Source root  (default: /mnt/recovery16tb/recovery)
  --dst DIR        Destination  (default: /mnt/CryptoBackup/recovery)
  --full           Also sync exports/ directories (Tier 3)
  --images         Also sync raw *.img files     (Tier 4, implies --full)
  --jobs N         Parallel rsync jobs           (default: 2)
  --run            Execute — without this flag, only a preview is shown
  -h, --help       Show this help

Examples:
  # Preview what would be synced (safe, nothing written)
  recovery-backup.sh

  # Back up catalogs + maps only
  recovery-backup.sh --run

  # Full backup including carved exports
  recovery-backup.sh --full --run

  # Everything including raw images (slow)
  recovery-backup.sh --images --run
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

SRC="/mnt/recovery16tb/recovery"
DST="/mnt/CryptoBackup/recovery"
JOBS=2
FULL=0
INCLUDE_IMAGES=0
RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)    SRC="$2";    shift 2 ;;
    --dst)    DST="$2";    shift 2 ;;
    --jobs)   JOBS="$2";   shift 2 ;;
    --full)   FULL=1;      shift   ;;
    --images) INCLUDE_IMAGES=1; FULL=1; shift ;;
    --run)    RUN=1;       shift   ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

need_cmd rsync
need_cmd parallel
need_cmd find

[[ -d "$SRC" ]] || die "source directory not found: $SRC"
[[ -d "$DST" ]] || die "destination directory not found: $DST"

LOGFILE="/tmp/recovery-backup-$(date +%Y%m%dT%H%M%S).log"
RSYNC_OPTS=(-aAX --numeric-ids --no-whole-file --inplace --human-readable)
[[ "$RUN" -eq 0 ]] && RSYNC_OPTS+=(--dry-run)

printf '=== Recovery Backup ===\n'
printf 'Source : %s\n' "$SRC"
printf 'Dest   : %s\n' "$DST"
printf 'Jobs   : %s\n' "$JOBS"
printf 'Full   : %s\n' "$([[ $FULL -eq 1 ]] && echo yes || echo no)"
printf 'Images : %s\n' "$([[ $INCLUDE_IMAGES -eq 1 ]] && echo yes || echo no)"
printf 'Mode   : %s\n' "$([[ $RUN -eq 1 ]] && echo LIVE || echo DRY-RUN)"
printf 'Log    : %s\n\n' "$LOGFILE"

[[ $RUN -eq 0 ]] && printf '[DRY-RUN] No files will be written.\n\n'

mkdir -p "$DST"

# ── Tier 1: SQLite catalogs ───────────────────────────────────────────────
printf '--- Tier 1: SQLite catalogs (*.analysis.sqlite) ---\n'
find "$SRC" -maxdepth 3 \
  \( -name "*.analysis.sqlite" -o -name "*.analysis.sqlite-wal" -o -name "*.analysis.sqlite-shm" \) \
  -print0 \
| parallel -0 -j "$JOBS" --tag --joblog "$LOGFILE" \
  rsync "${RSYNC_OPTS[@]}" --relative -- {} "$DST/"

# ── Tier 2: ddrescue maps and logs ────────────────────────────────────────
printf '\n--- Tier 2: ddrescue maps and logs ---\n'
if [[ -d "$SRC/logs" ]]; then
  rsync "${RSYNC_OPTS[@]}" \
    --include="*.map" \
    --include="*event*.log" \
    --include="*rate*.log" \
    --include="*ddrescue*.log" \
    --include="*safecopy*.log" \
    --include="*safecopy*.badblocks" \
    --include="*safecopy*.done" \
    --exclude="*" \
    "$SRC/logs/" "$DST/logs/" 2>&1 | tee -a "$LOGFILE"
fi

# ── Tier 3: exports (optional) ────────────────────────────────────────────
if [[ $FULL -eq 1 ]]; then
  printf '\n--- Tier 3: export directories ---\n'
  if [[ -d "$SRC/exports" ]]; then
    EXPORT_RSYNC_OPTS=("${RSYNC_OPTS[@]}")
    # Exclude raw images stashed in exports (shouldn't happen, but just in case)
    EXPORT_RSYNC_OPTS+=(--exclude="*.img")
    rsync "${EXPORT_RSYNC_OPTS[@]}" "$SRC/exports/" "$DST/exports/" 2>&1 | tee -a "$LOGFILE"
  fi

  # Manifests and per-job configs (small, always include with --full)
  printf '\n--- Tier 3b: manifests and job configs ---\n'
  if [[ -d "$SRC/manifests" ]]; then
    rsync "${RSYNC_OPTS[@]}" "$SRC/manifests/" "$DST/manifests/" 2>&1 | tee -a "$LOGFILE"
  fi
fi

# ── Tier 4: raw images (optional) ─────────────────────────────────────────
if [[ $INCLUDE_IMAGES -eq 1 ]]; then
  printf '\n--- Tier 4: raw disk images (*.img) ---\n'
  printf 'WARNING: raw images can be hundreds of GB each.\n'
  find "$SRC/images" -maxdepth 1 -name "*.img" -print0 \
  | parallel -0 -j 1 --tag --joblog "$LOGFILE" \
    rsync "${RSYNC_OPTS[@]}" --relative -- {} "$DST/"
fi

printf '\n=== Done. Log: %s ===\n' "$LOGFILE"
[[ $RUN -eq 0 ]] && printf '[DRY-RUN] Re-run with --run to execute.\n'
