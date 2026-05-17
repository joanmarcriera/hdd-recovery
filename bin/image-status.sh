#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

db="${1:-}"
[[ -n "$db" ]] || die "usage: image-status.sh <db-path>"
[[ -f "$db" ]] || die "database not found: $db"
db_ro="file:$db?mode=ro"

sqlite3 "$db_ro" <<'EOF'
.headers on
.mode column
SELECT image_name, image_sha256, image_size_bytes FROM image_info;
SELECT stage, status, started_at, ended_at FROM scan_runs ORDER BY id;
SELECT COUNT(*) AS files, SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END) AS deleted_files FROM files;
SELECT COUNT(*) AS wallet_candidates FROM wallet_candidates;
SELECT COUNT(*) AS picture_candidates FROM picture_candidates;
SELECT method, COUNT(*) AS count FROM recovered_artifacts GROUP BY method ORDER BY method;
EOF
