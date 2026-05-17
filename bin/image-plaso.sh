#!/usr/bin/env bash
# Generate a plaso super-timeline from the recovered corpus.
# Parses 50+ artifact types (file timestamps, EXIF, LNK, prefetch, USN,
# shellbags, browser history, etc.) into a unified chronological view.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-plaso.sh <db-path> [--full] [--run]

Without --full: runs log2timeline against the recovered corpus only
  (exports/recovered/). Much faster; suitable for most investigations.

With --full: runs log2timeline against the raw image directly.
  WARNING: can take many hours on large images.

Outputs:
  <export_root>/timeline/<basename>.plaso          — plaso storage file (SQLite internally)
  <export_root>/timeline/<basename>.timeline.csv   — sortable l2tcsv timeline
  <export_root>/hits/plaso-crypto/<timestamp>/timeline-crypto.csv
                                                   — crypto keyword sub-timeline

Default is dry-run. Pass --run to execute.

The plaso storage file is itself a SQLite database and can be queried directly.
The CSV output has columns: date,time,timezone,MACB,source,sourcetype,type,user,host,
  short,desc,version,filename,inode,notes,format,extra

Requires: plaso-log2timeline, plaso-psort  (python3-plaso)
Homepage: https://plaso.readthedocs.io
EOF
}

db="${1:-}"
full_image=0
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) full_image=1; shift ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

export_root="$(db_image_export_root "$db")"
basename="$(db_image_basename "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
timeline_dir="$export_root/timeline"
mkdir -p "$timeline_dir"

plaso_file="$timeline_dir/${basename}.plaso"
plaso_csv="$timeline_dir/${basename}.timeline.csv"
log_path="$export_root/logs/plaso-timeline.log"
crypto_dir="$export_root/hits/plaso-crypto/$timestamp"
crypto_csv="$crypto_dir/timeline-crypto.csv"
crypto_tmp="$crypto_dir/timeline-all.csv"

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

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: plaso timeline only. Re-run with --run to execute.\n'
  printf 'Target: %s\n' "$target"
  printf 'Plaso file: %s\n' "$plaso_file"
  printf 'Full CSV: %s\n' "$plaso_csv"
  printf 'Crypto CSV: %s\n' "$crypto_csv"
  exit 0
fi

need_cmd plaso-log2timeline
need_cmd plaso-psort
run_id="$(record_scan_start "$db" "plaso-timeline" "$0 $db ${1:-}" "$log_path" "$timeline_dir")"
status="ok"
notes=""
mkdir -p "$crypto_dir"

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

    log "Running crypto-focused plaso psort/filter: $crypto_csv"
    [[ -f "$crypto_csv" ]] && mv "$crypto_csv" "${crypto_csv}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
    if plaso-psort \
      --status_view none \
      -o l2tcsv \
      -w "$crypto_tmp" \
      "$plaso_file"; then
      python3 - "$db" "$crypto_tmp" "$crypto_csv" <<'PY'
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone

db_path, input_csv, output_csv = sys.argv[1:4]
keywords = ("bitcoin", "wallet", "seed", "ledger", "electrum", "metamask", "coinbase", "exodus", "phrase", "mnemonic")
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
conn = sqlite3.connect(db_path)
imported = 0
with open(input_csv, "r", errors="replace", newline="") as src, open(output_csv, "w", newline="") as dst:
    reader = csv.reader(src)
    writer = csv.writer(dst)
    header = next(reader, None)
    if header:
        writer.writerow(header)
    for row in reader:
        text = " ".join(row)
        if not any(keyword in text.lower() for keyword in keywords):
            continue
        writer.writerow(row)
        if imported < 5000:
            conn.execute(
                """
                INSERT INTO findings(source_tool, category, key, value, score, created_at)
                VALUES('plaso', 'timeline', 'crypto_event', ?, 70, ?)
                """,
                (text[:4000], now),
            )
            imported += 1
conn.commit()
conn.close()
try:
    os.remove(input_csv)
except OSError:
    pass
print(f"crypto timeline rows imported: {imported}")
PY
      log "crypto plaso timeline complete: $crypto_csv"
    else
      log "crypto plaso psort failed"
      status="partial"
    fi
  else
    log "No plaso file produced — skipping psort"
    status="partial"
    notes="log2timeline produced no output"
  fi
} 2>&1 | tee "$log_path" || status="partial"

[[ -f "$plaso_csv" ]] && notes="plaso CSV: $plaso_csv"
record_scan_end "$db" "$run_id" "$status" "$notes"
