#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-detect-pictures.sh <db-path>

Scores filesystem-aware file inventory entries for likely picture content.
This stage uses path, extension, and directory heuristics; richer metadata can
be added later after targeted export.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
[[ -n "${PICTURE_EXTENSIONS:-}" ]] || die "PICTURE_EXTENSIONS is not set"

export_root="$(db_image_export_root "$db")"
log_path="$export_root/logs/detect-pictures.log"
run_id="$(record_scan_start "$db" "detect-pictures" "$0 $db" "$log_path" "$export_root/hits")"

ext_csv="$(printf "'%s'" "$(printf '%s' "${PICTURE_EXTENSIONS}" | sed "s/ /','/g")")"

{
  sqlite3 "$db" "DELETE FROM picture_candidates WHERE source_stage='detect-pictures';"
  sqlite3 "$db" <<EOF
INSERT OR IGNORE INTO picture_candidates(file_id,source_stage,score,reason,details,created_at)
SELECT id,'detect-pictures',80,'picture-extension','image-like extension','$(timestamp_utc)'
FROM files
WHERE is_dir = 0
  AND lower(COALESCE(extension,'')) IN ($ext_csv);

INSERT OR IGNORE INTO picture_candidates(file_id,source_stage,score,reason,details,created_at)
SELECT id,'detect-pictures',65,'camera-directory','camera/DCIM/photo directory pattern','$(timestamp_utc)'
FROM files
WHERE is_dir = 0
  AND (
    lower(COALESCE(path,'')) LIKE '%/dcim/%'
    OR lower(COALESCE(path,'')) LIKE '%/pictures/%'
    OR lower(COALESCE(path,'')) LIKE '%/photos/%'
    OR lower(COALESCE(path,'')) LIKE '%/camera/%'
  );
EOF

  sqlite3 "$db" <<'EOF'
.headers on
.mode column
SELECT pc.score, f.path, pc.reason
FROM picture_candidates pc
JOIN files f ON f.id = pc.file_id
ORDER BY pc.score DESC, f.path
LIMIT 30;
EOF
} 2>&1 | tee "$log_path"

record_scan_end "$db" "$run_id" "ok"
