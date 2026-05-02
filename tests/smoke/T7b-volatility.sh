#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
DB="${WINMEM_DB:-${1:-}}"

cat <<'EOF'
T7b Volatility3 smoke test

Requires a DB where T7a has registered winmem-extract artifacts.

Expected verification SQL:
  SELECT source_tool, category, key, COUNT(*) FROM findings WHERE source_tool='volatility3' GROUP BY source_tool, category, key;
  SELECT method, relative_path, full_path FROM recovered_artifacts WHERE method='volatility-dump';
EOF

[[ -n "$DB" && -f "$DB" ]] || {
  echo "Set WINMEM_DB or pass a DB path with winmem-extract artifacts" >&2
  exit 2
}
command -v vol >/dev/null || { echo "volatility3 command 'vol' missing" >&2; exit 2; }

"$ROOT_DIR/bin/image-volatility-scan.sh" "$DB" --run
sqlite3 "$DB" "SELECT source_tool, category, key, COUNT(*) FROM findings WHERE source_tool='volatility3' GROUP BY source_tool, category, key;"
sqlite3 "$DB" "SELECT method, relative_path, full_path FROM recovered_artifacts WHERE method='volatility-dump';"
sqlite3 "$DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM findings WHERE source_tool='volatility3') THEN 'PASS' ELSE 'FAIL' END;"
