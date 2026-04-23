#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-analysis-init.sh <image-file> [--db <db-path>] [--map <ddrescue-map>] [--hash]

Creates or updates the per-image SQLite catalog and export directory tree.
EOF
}

image=""
db_path=""
map_path=""
do_hash=0
print_db_path=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) db_path="$2"; shift 2 ;;
    --map) map_path="$2"; shift 2 ;;
    --hash) do_hash=1; shift ;;
    --print-db-path) print_db_path=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) image="${image:-$1}"; shift ;;
  esac
done

[[ -n "$image" ]] || { usage; exit 1; }
image="$(abs_path "$image")"
ensure_image_file "$image"
db_path="${db_path:-$(default_db_path "$image")}"
ensure_db "$db_path"

export_root="$(default_export_root "$image")"
ensure_work_dirs "$export_root"

size_bytes="$(stat -c %s "$image")"
mtime_epoch="$(stat -c %Y "$image")"
sha256=""
if [[ "$do_hash" -eq 1 ]]; then
  sha256="$(sha256sum "$image" | awk '{print $1}')"
fi
now="$(timestamp_utc)"

sqlite3 "$db_path" <<EOF
INSERT INTO image_info(id,image_path,image_name,image_basename,image_sha256,image_size_bytes,image_mtime_epoch,ddrescue_map_path,export_root,created_at,updated_at)
VALUES(
  1,
  '$(sql_escape "$image")',
  '$(sql_escape "$(image_name "$image")")',
  '$(sql_escape "$(image_basename "$image")")',
  '$(sql_escape "$sha256")',
  $size_bytes,
  $mtime_epoch,
  '$(sql_escape "$map_path")',
  '$(sql_escape "$export_root")',
  '$(sql_escape "$now")',
  '$(sql_escape "$now")'
)
ON CONFLICT(id) DO UPDATE SET
  image_path=excluded.image_path,
  image_name=excluded.image_name,
  image_basename=excluded.image_basename,
  image_sha256=CASE WHEN excluded.image_sha256 = '' THEN image_info.image_sha256 ELSE excluded.image_sha256 END,
  image_size_bytes=excluded.image_size_bytes,
  image_mtime_epoch=excluded.image_mtime_epoch,
  ddrescue_map_path=CASE WHEN excluded.ddrescue_map_path = '' THEN image_info.ddrescue_map_path ELSE excluded.ddrescue_map_path END,
  export_root=excluded.export_root,
  updated_at=excluded.updated_at;
EOF

if [[ "$print_db_path" -eq 1 ]]; then
  printf '%s\n' "$db_path"
else
  printf 'DB: %s\n' "$db_path"
  printf 'Export root: %s\n' "$export_root"
fi
