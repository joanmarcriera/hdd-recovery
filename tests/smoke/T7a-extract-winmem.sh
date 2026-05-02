#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"
DB="${WINMEM_DB:-${1:-}}"

cat <<'EOF'
T7a Windows memory extraction smoke test

Requires a TSK-indexed Windows image DB containing hiberfil.sys and/or pagefile.sys.

Expected verification SQL:
  SELECT method, relative_path, full_path, sha256 FROM recovered_artifacts WHERE method='winmem-extract';
EOF

[[ -n "$DB" && -f "$DB" ]] || {
  echo "Set WINMEM_DB or pass a DB path for an indexed Windows image" >&2
  exit 2
}

"$ROOT_DIR/bin/image-extract-winmem.sh" "$DB" --run
sqlite3 "$DB" "SELECT method, relative_path, full_path, sha256 FROM recovered_artifacts WHERE method='winmem-extract';"
export_root="$(sqlite3 -noheader "$DB" "SELECT export_root FROM image_info WHERE id=1;")"
find "$export_root/winmem" -type f -maxdepth 3 -print
sqlite3 "$DB" "SELECT CASE WHEN EXISTS(SELECT 1 FROM recovered_artifacts WHERE method='winmem-extract') THEN 'PASS' ELSE 'FAIL' END;"
