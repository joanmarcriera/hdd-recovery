#!/usr/bin/env bash
# Generate a plaso super-timeline from the recovered corpus.
# Parses 50+ artifact types (file timestamps, EXIF, LNK, prefetch, USN,
# shellbags, browser history, etc.) into a unified chronological view.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-plaso.sh <db-path> [--full]

Without --full: runs log2timeline against the recovered corpus only
  (exports/recovered/). Much faster; suitable for most investigations.

With --full: runs log2timeline against the raw image directly.
  WARNING: can take many hours on large images.

Outputs:
  <export_root>/timeline/<basename>.plaso          — plaso storage file (SQLite internally)
  <export_root>/timeline/<basename>.timeline.csv   — sortable l2tcsv timeline

The plaso storage file is itself a SQLite database and can be queried directly.
The CSV output has columns: date,time,timezone,MACB,source,sourcetype,type,user,host,
  short,desc,version,filename,inode,notes,format,extra

Requires: plaso-log2timeline, plaso-psort  (python3-plaso)
Homepage: https://plaso.readthedocs.io
EOF
}

db="${1:-}"
full_image=0
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) full_image=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd plaso-log2timeline
need_cmd plaso-psort

export_root="$(db_image_export_root "$db")"
basename="$(db_image_basename "$db")"
timeline_dir="$export_root/timeline"
mkdir -p "$timeline_dir"

plaso_file="$timeline_dir/${basename}.plaso"
plaso_csv="$timeline_dir/${basename}.timeline.csv"
log_path="$export_root/logs/plaso-timeline.log"

if [[ "$full_image" -eq 1 ]]; then
  image_path="$(db_image_path "$db")"
  [[ -f "$image_path" ]] || die "image file not found: $image_path"
  target="$image_path"
  target_desc="raw image (SLOW)"
else
  target="$export_root/recovered"
  [[ -d "$target" ]] || die "recovered corpus not found: $target (run a carving stage first)"
  target_desc="recovered corpus"
fi

run_id="$(record_scan_start "$db" "plaso-timeline" "$0 $db ${1:-}" "$log_path" "$timeline_dir")"
status="ok"
notes=""

log "Running log2timeline on $target_desc: $target"
log "plaso storage file: $plaso_file"

{
  # Remove stale plaso file if present (log2timeline refuses to overwrite)
  [[ -f "$plaso_file" ]] && {
    mv "$plaso_file" "${plaso_file}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
  }

  plaso-log2timeline \
    --status_view none \
    --no_dependencies_check \
    --storage_file "$plaso_file" \
    "$target" \
    && log "plaso-log2timeline complete" \
    || { log "plaso-log2timeline exited non-zero; continuing to plaso-psort"; status="partial"; }

  if [[ -f "$plaso_file" ]]; then
    log "Running plaso-psort → l2tcsv: $plaso_csv"
    [[ -f "$plaso_csv" ]] && mv "$plaso_csv" "${plaso_csv}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
    plaso-psort \
      --status_view none \
      -o l2tcsv \
      -w "$plaso_csv" \
      "$plaso_file" \
      && log "plaso-psort complete: $plaso_csv" \
      || { log "plaso-psort failed"; status="partial"; }
  else
    log "No plaso file produced — skipping psort"
    status="partial"
    notes="log2timeline produced no output"
  fi
} 2>&1 | tee "$log_path" || status="partial"

[[ -f "$plaso_csv" ]] && notes="plaso CSV: $plaso_csv"
record_scan_end "$db" "$run_id" "$status" "$notes"
