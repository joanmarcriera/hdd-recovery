#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-ext-recover.sh <db-path>

Runs extundelete and ext4magic against ext3/ext4 partitions via read-only loop
devices. Outputs are kept separate by tool and partition.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

need_cmd losetup
need_cmd extundelete
need_cmd ext4magic

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
out_root="$export_root/recovered/ext"
log_path="$export_root/logs/ext-recover.log"
mkdir -p "$out_root"

run_id="$(record_scan_start "$db" "ext-recover" "$0 $db" "$log_path" "$out_root")"
status="ok"
notes=""

{
  sqlite3 -separator $'\t' "$db" "
    SELECT p.id, COALESCE(p.start_sector,0), COALESCE(p.length_sectors,0), COALESCE(p.sector_size,512), COALESCE(f.fs_type,'')
    FROM partitions p
    LEFT JOIN filesystems f ON f.partition_id = p.id
    WHERE lower(COALESCE(f.fs_type,'')) IN ('ext3','ext4');
  " | while IFS=$'\t' read -r part_id start length sector_size fs_type; do
    [[ -n "$part_id" ]] || continue
    offset_bytes=$(( start * sector_size ))
    sizelimit=""
    if [[ "$length" =~ ^[0-9]+$ && "$length" -gt 0 ]]; then
      sizelimit=$(( length * sector_size ))
    fi
    loop_args=(losetup -r --show -f --offset "$offset_bytes")
    if [[ -n "$sizelimit" ]]; then
      loop_args+=(--sizelimit "$sizelimit")
    fi
    loop_args+=("$image")
    loopdev="$("${loop_args[@]}")"

    extundelete_dir="$out_root/partition-${part_id}/extundelete"
    ext4magic_dir="$out_root/partition-${part_id}/ext4magic"
    mkdir -p "$extundelete_dir" "$ext4magic_dir"

    (cd "$extundelete_dir" && extundelete "$loopdev" --restore-all) || true
    ext4magic "$loopdev" -M -d "$ext4magic_dir" || true
    losetup -d "$loopdev" || true

    register_artifacts_from_dir "$db" "extundelete" "$extundelete_dir" "$run_id"
    register_artifacts_from_dir "$db" "ext4magic" "$ext4magic_dir" "$run_id"
  done
} 2>&1 | tee "$log_path" || { status="partial"; notes="one or more ext recovery commands failed"; }

record_scan_end "$db" "$run_id" "$status" "$notes"
