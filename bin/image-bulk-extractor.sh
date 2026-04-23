#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-bulk-extractor.sh <db-path> [--scope raw|recovered]

Runs bulk_extractor against the raw image or the recovered corpus directory and
stores a small summary in SQLite while retaining the full bulk_extractor output.
EOF
}

db="${1:-}"
scope="raw"
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope) scope="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd bulk_extractor

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
target="$image"
scope_dir="$export_root/indexes/bulk_extractor_${scope}"
log_path="$export_root/logs/bulk-extractor-${scope}.log"
hit_limit="${BULK_HIT_LIMIT:-5000}"

if [[ "$scope" == "recovered" ]]; then
  target="$export_root/recovered"
  [[ -d "$target" ]] || die "recovered corpus directory not found: $target"
fi

run_id="$(record_scan_start "$db" "bulk-extractor-$scope" "$0 $db --scope $scope" "$log_path" "$scope_dir")"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -d "$scope_dir" ]]; then
  backup_dir="${scope_dir}.prev-${timestamp}"
  log "Preserving previous bulk_extractor output at $backup_dir"
  mv "$scope_dir" "$backup_dir"
fi
if [[ -f "$log_path" ]]; then
  backup_log="${log_path}.prev-${timestamp}"
  log "Preserving previous bulk_extractor log at $backup_log"
  mv "$log_path" "$backup_log"
fi

mkdir -p "$scope_dir"

{
  if [[ "$scope" == "recovered" ]]; then
    bulk_extractor -o "$scope_dir" -R "$target"
  else
    bulk_extractor -o "$scope_dir" "$target"
  fi
} 2>&1 | tee "$log_path"

python3 - "$db" "$scope" "$scope_dir" "$run_id" "$hit_limit" <<'PY'
import os
import sqlite3
import sys
from datetime import datetime, timezone

db_path, scope, out_dir, run_id, hit_limit = sys.argv[1:]
conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
hit_limit = int(hit_limit)

feature_files = [
    name for name in os.listdir(out_dir)
    if os.path.isfile(os.path.join(out_dir, name))
    and not name.startswith(".")
    and not name.endswith("_histogram.txt")
    and name not in {"report.xml", "wordlist.txt", "identified_blocks.txt"}
]

for name in sorted(feature_files):
    path = os.path.join(out_dir, name)
    count = 0
    truncated = False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            offset_ref = parts[0]
            value = parts[1]
            context = parts[2] if len(parts) > 2 else None
            conn.execute(
                """
                INSERT INTO bulk_extractor_hits(source_scope,feature_file,offset_ref,value,context,source_run_id,created_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (scope, name, offset_ref, value, context, int(run_id), now),
            )
            count += 1
            if count >= hit_limit:
                truncated = True
                break
    if truncated:
        conn.execute(
            "INSERT INTO notes(created_at, note) VALUES(?, ?)",
            (now, f"bulk_extractor import truncated for {name} at {hit_limit} rows in scope {scope}"),
        )

conn.commit()
conn.close()
PY

record_scan_end "$db" "$run_id" "ok"
