#!/usr/bin/env bash
# Recover deleted files from FAT12/FAT16/FAT32/exFAT partitions using fatcat.
# Works directly on the image with a byte offset; no loop device needed.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-fat-recover.sh <db-path>

Finds FAT/exFAT partitions in the image catalog and uses fatcat to extract
all files including deleted ones to the export directory.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

need_cmd fatcat

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
out_root="$export_root/recovered/fat"
log_path="$export_root/logs/fat-recover.log"
mkdir -p "$out_root"

run_id="$(record_scan_start "$db" "fat-recover" "$0 $db" "$log_path" "$out_root")"
status="ok"
notes=""

{
  printf '[%s] Starting FAT deleted-file recovery\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  fat_parts="$(sqlite3 -separator $'\t' "$db" "
    SELECT p.id, COALESCE(p.start_sector,0), COALESCE(p.length_sectors,0),
           COALESCE(p.sector_size,512), COALESCE(f.fs_type,'')
    FROM partitions p
    LEFT JOIN filesystems f ON f.partition_id = p.id
    WHERE lower(COALESCE(f.fs_type,'')) IN ('fat','fat12','fat16','fat32','vfat','exfat');
  ")"

  if [[ -z "$fat_parts" ]]; then
    printf 'No FAT/exFAT partitions found in catalog. Nothing to do.\n'
    printf 'Run structure-scan first if partitions are not listed.\n'
    status="ok"
    notes="no FAT/exFAT partitions found"
    record_scan_end "$db" "$run_id" "$status" "$notes"
    exit 0
  fi

  echo "$fat_parts" | while IFS=$'\t' read -r part_id start _length sector_size fs_type; do
    [[ -n "$part_id" ]] || continue
    offset_bytes=$(( start * sector_size ))

    printf '\n--- Partition %s (%s, offset %d bytes) ---\n' "$part_id" "$fs_type" "$offset_bytes"

    part_dir="$out_root/partition-${part_id}"
    mkdir -p "$part_dir"
    printf 'Output dir: %s\n' "$part_dir"

    # Show disk info first
    printf '\n[Info]\n'
    fatcat "$image" -O "$offset_bytes" -i || true

    # Extract all files including deleted
    printf '\n[Extract with deleted files]\n'
    fatcat "$image" -O "$offset_bytes" -x "$part_dir" -d || {
      printf 'fatcat returned non-zero for partition %s\n' "$part_id"
      status="partial"
    }

    register_artifacts_from_dir "$db" "fatcat" "$part_dir" "$run_id"
  done

} 2>&1 | tee "$log_path" || { status="partial"; notes="one or more FAT recovery steps failed"; }

record_scan_end "$db" "$run_id" "$status" "$notes"
