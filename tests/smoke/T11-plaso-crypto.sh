#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
DB="${PLASO_DB:-${1:-}}"

cat <<'EOF'
T11 plaso crypto timeline smoke test

Requires a DB with a recovered corpus suitable for image-plaso.sh.

Expected verification SQL:
  SELECT source_tool, category, key, COUNT(*) FROM findings WHERE source_tool='plaso' AND category='timeline' GROUP BY source_tool, category, key;
EOF

[[ -n "$DB" && -f "$DB" ]] || {
  echo "Set PLASO_DB or pass a DB path with a recovered corpus" >&2
  exit 2
}
command -v plaso-log2timeline >/dev/null || { echo "plaso-log2timeline missing" >&2; exit 2; }
command -v plaso-psort >/dev/null || { echo "plaso-psort missing" >&2; exit 2; }

"$ROOT_DIR/bin/image-plaso.sh" "$DB" --run
export_root="$(sqlite3 -noheader "$DB" "SELECT export_root FROM image_info WHERE id=1;")"
find "$export_root/hits/plaso-crypto" -name timeline-crypto.csv -print
sqlite3 "$DB" "SELECT source_tool, category, key, COUNT(*) FROM findings WHERE source_tool='plaso' AND category='timeline' GROUP BY source_tool, category, key;"
sqlite3 "$DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM findings WHERE source_tool='plaso' AND category='timeline') THEN 'PASS' ELSE 'CHECK-CSV' END;"
