#!/usr/bin/env bash
# Recover files from Btrfs partitions using btrfs restore.
# btrfs restore is read-only against the source — safe to use on images.
# Requires: apt install btrfs-progs
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-btrfs-recover.sh <db-path>

Finds Btrfs partitions in the image catalog, mounts each via a read-only
loop device, and runs btrfs restore to extract all recoverable files.

Installation (if not already done):
  apt install btrfs-progs
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

need_cmd btrfs
need_cmd losetup

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
out_root="$export_root/recovered/btrfs"
log_path="$export_root/logs/btrfs-recover.log"
mkdir -p "$out_root"

run_id="$(record_scan_start "$db" "btrfs-recover" "$0 $db" "$log_path" "$out_root")"
status="ok"
notes=""

{
  printf '[%s] Starting Btrfs file recovery\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf 'btrfs version: %s\n' "$(btrfs version 2>/dev/null || echo unknown)"

  btrfs_parts="$(sqlite3 -separator $'\t' "$db" "
    SELECT p.id, COALESCE(p.start_sector,0), COALESCE(p.length_sectors,0),
           COALESCE(p.sector_size,512), COALESCE(f.fs_type,'')
    FROM partitions p
    LEFT JOIN filesystems f ON f.partition_id = p.id
    WHERE lower(COALESCE(f.fs_type,'')) = 'btrfs';
  ")"

  if [[ -z "$btrfs_parts" ]]; then
    printf 'No Btrfs partitions found in catalog. Nothing to do.\n'
    printf 'Run structure-scan first if partitions are not listed.\n'
    status="ok"
    notes="no Btrfs partitions found"
    record_scan_end "$db" "$run_id" "$status" "$notes"
    exit 0
  fi

  echo "$btrfs_parts" | while IFS=$'\t' read -r part_id start length sector_size _fs_type; do
    [[ -n "$part_id" ]] || continue
    offset_bytes=$(( start * sector_size ))
    sizelimit=$(( length * sector_size ))

    loop_args=(losetup -r --show -f --offset "$offset_bytes")
    [[ "$sizelimit" -gt 0 ]] && loop_args+=(--sizelimit "$sizelimit")
    loop_args+=("$image")

    printf '\n--- Partition %s (offset %d bytes) ---\n' "$part_id" "$offset_bytes"

    loopdev="$("${loop_args[@]}")" || { printf 'losetup failed for partition %s\n' "$part_id"; continue; }

    part_dir="$out_root/partition-${part_id}"
    mkdir -p "$part_dir"

    printf 'Loop device: %s\n' "$loopdev"
    printf 'Output dir: %s\n' "$part_dir"

    # Show superblock info first
    printf '\n[Btrfs inspect]\n'
    btrfs inspect-internal dump-super "$loopdev" 2>/dev/null | head -20 || true

    # Restore all recoverable files (-v verbose, -o overwrite, -i skip inode errors)
    printf '\n[btrfs restore]\n'
    btrfs restore -v -o -i "$loopdev" "$part_dir" || {
      # -i: ignore errors and continue — useful for damaged filesystems
      printf 'btrfs restore returned non-zero for partition %s — output may be partial\n' "$part_id"
      status="partial"
      notes="btrfs restore reported errors on partition ${part_id}"
    }

    losetup -d "$loopdev" || true
    register_artifacts_from_dir "$db" "btrfs-restore" "$part_dir" "$run_id"
  done

} 2>&1 | tee "$log_path" || { status="partial"; notes="one or more Btrfs recovery steps failed"; }

record_scan_end "$db" "$run_id" "$status" "$notes"
