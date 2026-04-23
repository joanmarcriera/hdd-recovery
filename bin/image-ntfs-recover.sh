#!/usr/bin/env bash
# Recover deleted files from NTFS partitions using ntfsundelete.
# Works via read-only loop devices; never writes to source.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-ntfs-recover.sh <db-path> [--min-pct N]

Options:
  --min-pct N   Minimum percentage recoverable to attempt (default: 0 = all)

Finds NTFS partitions in the image catalog, mounts each via a read-only
loop device, and runs ntfsundelete to recover deleted files.
EOF
}

db="${1:-}"
min_pct=0
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --min-pct) min_pct="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

need_cmd losetup
need_cmd ntfsundelete

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
out_root="$export_root/recovered/ntfs"
log_path="$export_root/logs/ntfs-recover.log"
mkdir -p "$out_root"

run_id="$(record_scan_start "$db" "ntfs-recover" "$0 $db" "$log_path" "$out_root")"
status="ok"
notes=""

{
  printf '[%s] Starting NTFS deleted-file recovery\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  ntfs_parts="$(sqlite3 -separator $'\t' "$db" "
    SELECT p.id, COALESCE(p.start_sector,0), COALESCE(p.length_sectors,0),
           COALESCE(p.sector_size,512), COALESCE(f.fs_type,'')
    FROM partitions p
    LEFT JOIN filesystems f ON f.partition_id = p.id
    WHERE lower(COALESCE(f.fs_type,'')) IN ('ntfs','ntfs-3g');
  ")"

  if [[ -z "$ntfs_parts" ]]; then
    printf 'No NTFS partitions found in catalog. Nothing to do.\n'
    printf 'Run structure-scan first if partitions are not listed.\n'
    status="ok"
    notes="no NTFS partitions found"
    record_scan_end "$db" "$run_id" "$status" "$notes"
    exit 0
  fi

  echo "$ntfs_parts" | while IFS=$'\t' read -r part_id start length sector_size _fs_type; do
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

    # Scan pass (informational)
    printf '\n[Scan]\n'
    ntfsundelete -s -p "$min_pct" "$loopdev" || true

    # Undelete pass
    printf '\n[Undelete]\n'
    ntfsundelete -u -p "$min_pct" -T -d "$part_dir" "$loopdev" || {
      printf 'ntfsundelete returned non-zero for partition %s\n' "$part_id"
    }

    losetup -d "$loopdev" || true
    register_artifacts_from_dir "$db" "ntfsundelete" "$part_dir" "$run_id"
  done

} 2>&1 | tee "$log_path" || { status="partial"; notes="one or more NTFS recovery steps failed"; }

record_scan_end "$db" "$run_id" "$status" "$notes"
