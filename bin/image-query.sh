#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  image-query.sh <db-path> <command> [args]

Commands:
  summary
  wallets
  pictures
  findings [tool]          all findings, or filtered by source_tool
  findings-summary         count + top score per tool and category
  files-like <pattern>
  artifacts <method>
  sql <statement>
EOF
}

db="${1:-}"
cmd="${2:-}"
arg="${3:-}"
[[ -n "$db" && -n "$cmd" ]] || { usage; exit 1; }
[[ -f "$db" ]] || { printf 'database not found: %s\n' "$db" >&2; exit 1; }
db_ro="file:$db?mode=ro"

case "$cmd" in
  summary)
    sqlite3 "$db_ro" <<'EOF'
.headers on
.mode column
SELECT image_name, image_sha256, image_size_bytes, export_root FROM image_info;
SELECT COUNT(*) AS partitions FROM partitions;
SELECT COUNT(*) AS files FROM files;
SELECT COUNT(*) AS wallet_candidates FROM wallet_candidates;
SELECT COUNT(*) AS picture_candidates FROM picture_candidates;
SELECT COUNT(*) AS recovered_artifacts FROM recovered_artifacts;
SELECT COUNT(*) AS findings FROM findings;
EOF
    ;;
  wallets)
    sqlite3 "$db_ro" <<'EOF'
.headers on
.mode column
SELECT wc.score, wc.reason, f.path, f.size_bytes
FROM wallet_candidates wc
JOIN files f ON f.id = wc.file_id
ORDER BY wc.score DESC, f.path;
EOF
    ;;
  pictures)
    sqlite3 "$db_ro" <<'EOF'
.headers on
.mode column
SELECT pc.score, pc.reason, f.path, f.size_bytes
FROM picture_candidates pc
JOIN files f ON f.id = pc.file_id
ORDER BY pc.score DESC, f.path;
EOF
    ;;
  findings)
    if [[ -n "$arg" ]]; then
      sqlite3 "$db_ro" <<EOF
.headers on
.mode column
SELECT source_tool, category, key, value, score, path, created_at
FROM findings
WHERE source_tool = '$(printf "%s" "$arg" | sed "s/'/''/g")'
ORDER BY score DESC, key;
EOF
    else
      sqlite3 "$db_ro" <<'EOF'
.headers on
.mode column
SELECT source_tool, category, key, value, score, path, created_at
FROM findings
ORDER BY score DESC, source_tool, category;
EOF
    fi
    ;;
  findings-summary)
    sqlite3 "$db_ro" <<'EOF'
.headers on
.mode column
SELECT source_tool, category, COUNT(*) AS count, MAX(score) AS top_score
FROM findings
GROUP BY source_tool, category
ORDER BY top_score DESC, count DESC;
EOF
    ;;
  files-like)
    [[ -n "$arg" ]] || die "pattern required"
    sqlite3 "$db_ro" <<EOF
.headers on
.mode column
SELECT id, path, inode, size_bytes, deleted
FROM files
WHERE lower(COALESCE(path,'')) LIKE lower('%$(printf "%s" "$arg" | sed "s/'/''/g")%')
ORDER BY path;
EOF
    ;;
  artifacts)
    [[ -n "$arg" ]] || die "method required"
    sqlite3 "$db_ro" <<EOF
.headers on
.mode column
SELECT method, relative_path, size_bytes, sha256
FROM recovered_artifacts
WHERE method = '$(printf "%s" "$arg" | sed "s/'/''/g")'
ORDER BY relative_path;
EOF
    ;;
  sql)
    [[ -n "$arg" ]] || die "SQL statement required"
    sqlite3 "$db_ro" "$arg"
    ;;
  *)
    usage
    exit 1
    ;;
esac
