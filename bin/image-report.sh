#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-report.sh <db-path>

Generates a concise per-image markdown report from the SQLite catalog.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
db_ro="file:$db?mode=ro"

export_root="$(db_image_export_root "$db")"
report_path="$export_root/reports/summary.md"

{
  printf '# Image Analysis Report\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT image_name AS image, image_path AS path, image_sha256 AS sha256, image_size_bytes AS size_bytes
FROM image_info;
EOF
  printf '\n## Partitions\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT slot, start_sector, length_sectors, filesystem_hint, description
FROM partitions
ORDER BY start_sector IS NULL, start_sector;
EOF
  printf '\n## Scan Runs\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT stage, status, started_at, ended_at, log_path
FROM scan_runs
ORDER BY id;
EOF
  printf '\n## Top Wallet Candidates\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT wc.score, wc.reason, f.path, f.size_bytes
FROM wallet_candidates wc
JOIN files f ON f.id = wc.file_id
ORDER BY wc.score DESC, f.path
LIMIT 25;
EOF
  printf '\n## Top Picture Candidates\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT pc.score, pc.reason, f.path, f.size_bytes
FROM picture_candidates pc
JOIN files f ON f.id = pc.file_id
ORDER BY pc.score DESC, f.path
LIMIT 25;
EOF
  printf '\n## Recovered Artifact Counts\n\n'
  sqlite3 "$db_ro" <<'EOF'
.mode markdown
SELECT method, COUNT(*) AS count
FROM recovered_artifacts
GROUP BY method
ORDER BY method;
EOF
} > "$report_path"

printf '%s\n' "$report_path"
