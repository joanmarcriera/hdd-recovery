#!/usr/bin/env bash
# Extract Windows hibernation/pagefile artifacts from the image using TSK inode
# data so Volatility3 can scan standalone files.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-extract-winmem.sh <db-path> [--run]

Default is dry-run. Pass --run to extract hiberfil.sys/pagefile.sys.

Requires:
  image-index-tsk.sh must have populated files.inode and partition data.

Outputs:
  <export_root>/winmem/<partition_id>/hiberfil.sys
  <export_root>/winmem/<partition_id>/pagefile.sys

Extracted files are registered in recovered_artifacts with method='winmem-extract'.
EOF
}

db="${1:-}"
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd sqlite3

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/winmem"
log_path="$export_root/logs/extract-winmem-$timestamp.log"
mkdir -p "$out_dir" "$(dirname "$log_path")"

candidates_tsv="$out_dir/candidates-$timestamp.tsv"
sqlite3 -separator '|' "$db" <<'SQL' > "$candidates_tsv"
SELECT
  f.id,
  COALESCE(f.partition_id, 0),
  COALESCE(f.inode, ''),
  COALESCE(f.name, ''),
  COALESCE(f.path, ''),
  COALESCE(p.start_sector, 0),
  COALESCE(p.sector_size, 512)
FROM files f
LEFT JOIN partitions p ON p.id = f.partition_id
WHERE lower(COALESCE(f.name, '')) IN ('hiberfil.sys', 'pagefile.sys')
  AND COALESCE(f.is_dir, 0) = 0
ORDER BY f.partition_id, f.name;
SQL
candidate_count="$(grep -c '.' "$candidates_tsv" 2>/dev/null || true)"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: Windows memory extraction only. Re-run with --run to execute.\n'
  printf 'Candidates: %s\n' "$candidate_count"
  while IFS='|' read -r file_id partition_id inode name path start_sector sector_size; do
    printf 'file_id=%s partition=%s inode=%s name=%s path=%s\n' "$file_id" "$partition_id" "$inode" "$name" "$path"
    printf '  icat -o %s -b %s %q %q > %q\n' "$start_sector" "$sector_size" "$image" "$inode" "$out_dir/$partition_id/$name"
  done < "$candidates_tsv"
  exit 0
fi

need_cmd icat
need_cmd sha256sum

run_id="$(record_scan_start "$db" "extract-winmem" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
  if [[ "$candidate_count" -eq 0 ]]; then
    printf 'No hiberfil.sys or pagefile.sys rows found in files table.\n'
  fi

  while IFS='|' read -r file_id partition_id inode name path start_sector sector_size; do
    if [[ -z "$inode" ]]; then
      printf 'Skipping file_id=%s because inode is empty.\n' "$file_id"
      continue
    fi
    dest_dir="$out_dir/$partition_id"
    dest_path="$dest_dir/$name"
    rel_path="$partition_id/$name"
    mkdir -p "$dest_dir"

    if [[ -f "$dest_path" ]]; then
      existing_sha="$(sha256sum "$dest_path" | awk '{print $1}')"
      if sqlite3 -noheader "$db" "SELECT 1 FROM recovered_artifacts WHERE method='winmem-extract' AND sha256='$(sql_escape "$existing_sha")' LIMIT 1;" | grep -q 1; then
        printf 'Skipping already extracted %s sha256=%s\n' "$dest_path" "$existing_sha"
        continue
      fi
      backup_path="${dest_path}.prev-$timestamp"
      printf 'Backing up existing output %s -> %s\n' "$dest_path" "$backup_path"
      mv "$dest_path" "$backup_path"
    fi

    printf 'Extracting file_id=%s inode=%s -> %s\n' "$file_id" "$inode" "$dest_path"
    icat -o "$start_sector" -b "$sector_size" "$image" "$inode" > "$dest_path"
    sha256="$(sha256sum "$dest_path" | awk '{print $1}')"
    size_bytes="$(stat -c %s "$dest_path")"
    sqlite3 "$db" <<SQL
INSERT INTO recovered_artifacts(method,relative_path,full_path,size_bytes,sha256,mime_type,file_output,source_run_id,created_at,notes)
VALUES('winmem-extract','$(sql_escape "$rel_path")','$(sql_escape "$dest_path")',$size_bytes,'$(sql_escape "$sha256")','application/octet-stream','Windows memory artifact',$run_id,'$(timestamp_utc)','source file_id=$file_id path=$(sql_escape "$path")')
ON CONFLICT(method, relative_path) DO UPDATE SET
  full_path=excluded.full_path,
  size_bytes=excluded.size_bytes,
  sha256=excluded.sha256,
  source_run_id=excluded.source_run_id,
  notes=excluded.notes;
SQL
  done < "$candidates_tsv"
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="winmem extraction failed or incomplete; check log"
}

if [[ -z "$notes" ]]; then
  extracted="$(sqlite3 -noheader "$db" "SELECT COUNT(*) FROM recovered_artifacts WHERE method='winmem-extract';")"
  notes="winmem artifacts=$extracted"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
