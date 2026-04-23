#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-export.sh <db-path> --file-id <id> [--dest-dir <dir>]

Exports one filesystem-aware file candidate from the image using icat.
The file_id must refer to a row in the files table with inode information.
EOF
}

db="${1:-}"
shift $(( $# > 0 ? 1 : 0 )) || true
file_id=""
dest_dir=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file-id) file_id="$2"; shift 2 ;;
    --dest-dir) dest_dir="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" && -n "$file_id" ]] || { usage; exit 1; }
need_cmd icat

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
dest_dir="${dest_dir:-$export_root/exports/files}"
mkdir -p "$dest_dir"

row="$(sqlite3 -separator $'\t' "$db" "
SELECT COALESCE(f.inode,''), COALESCE(f.path,''), COALESCE(p.start_sector,0), COALESCE(p.sector_size,512)
FROM files f
LEFT JOIN partitions p ON p.id = f.partition_id
WHERE f.id = $file_id;
")"
[[ -n "$row" ]] || die "file id not found: $file_id"
IFS=$'\t' read -r inode src_path start_sector sector_size <<< "$row"
[[ -n "$inode" ]] || die "file id $file_id has no inode recorded"

safe_name="$(basename "${src_path:-file-$file_id}")"
dest_path="$dest_dir/$safe_name"
icat -o "$start_sector" -b "$sector_size" "$image" "$inode" > "$dest_path"
sha256="$(sha256sum "$dest_path" | awk '{print $1}')"
size_bytes="$(stat -c %s "$dest_path")"

sqlite3 "$db" <<EOF
INSERT INTO exports(source_kind,source_ref,relative_path,full_path,sha256,size_bytes,created_at,notes)
VALUES('file-id','$(sql_escape "$file_id")','$(sql_escape "$(realpath --relative-to "$export_root" "$dest_path")")','$(sql_escape "$dest_path")','$(sql_escape "$sha256")',$size_bytes,'$(timestamp_utc)','exported via icat');
EOF

printf '%s\n' "$dest_path"
