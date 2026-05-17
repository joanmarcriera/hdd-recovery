#!/usr/bin/env bash
# Recover deleted files from XFS partitions using xfs_undelete.
# Requires: xfs_undelete from https://github.com/ianka/xfs_undelete
#   apt install xfsprogs tcllib
#   git clone https://github.com/ianka/xfs_undelete /opt/xfs_undelete
#   ln -s /opt/xfs_undelete/xfs_undelete /usr/local/bin/xfs_undelete
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-xfs-recover.sh <db-path> [--types TYPES]

Options:
  --types TYPES   Comma-separated file types/extensions to recover (default: all)
                  Example: --types jpg,png,pdf,doc

Finds XFS partitions in the catalog, mounts each via read-only loop device,
and runs xfs_undelete to recover deleted inodes.

Installation (if not already done):
  apt install xfsprogs tcllib
  git clone https://github.com/ianka/xfs_undelete /opt/xfs_undelete
  ln -s /opt/xfs_undelete/xfs_undelete /usr/local/bin/xfs_undelete
EOF
}

db="${1:-}"
recover_types="*"
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --types) recover_types="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

XFS_UNDELETE=""
for candidate in xfs_undelete /usr/local/bin/xfs_undelete /opt/xfs_undelete/xfs_undelete; do
  if command -v "$candidate" >/dev/null 2>&1; then
    XFS_UNDELETE="$candidate"
    break
  fi
done

if [[ -z "$XFS_UNDELETE" ]]; then
  printf 'ERROR: xfs_undelete not found.\n' >&2
  printf 'Install with:\n' >&2
  printf '  apt install xfsprogs tcllib\n' >&2
  printf '  git clone https://github.com/ianka/xfs_undelete /opt/xfs_undelete\n' >&2
  printf '  ln -s /opt/xfs_undelete/xfs_undelete /usr/local/bin/xfs_undelete\n' >&2
  exit 1
fi

need_cmd losetup

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
out_root="$export_root/recovered/xfs"
log_path="$export_root/logs/xfs-recover.log"
mkdir -p "$out_root"

run_id="$(record_scan_start "$db" "xfs-recover" "$0 $db" "$log_path" "$out_root")"
status="ok"
notes=""

{
  printf '[%s] Starting XFS deleted-file recovery\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf 'Using: %s\n' "$XFS_UNDELETE"

  xfs_parts="$(sqlite3 -separator $'\t' "$db" "
    SELECT p.id, COALESCE(p.start_sector,0), COALESCE(p.length_sectors,0),
           COALESCE(p.sector_size,512), COALESCE(f.fs_type,'')
    FROM partitions p
    LEFT JOIN filesystems f ON f.partition_id = p.id
    WHERE lower(COALESCE(f.fs_type,'')) = 'xfs';
  ")"

  if [[ -z "$xfs_parts" ]]; then
    printf 'No XFS partitions found in catalog. Nothing to do.\n'
    printf 'Run structure-scan first if partitions are not listed.\n'
    status="ok"
    notes="no XFS partitions found"
    record_scan_end "$db" "$run_id" "$status" "$notes"
    exit 0
  fi

  echo "$xfs_parts" | while IFS=$'\t' read -r part_id start length sector_size _fs_type; do
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
    printf 'File types: %s\n' "$recover_types"

    xfs_undelete_args=(-o "$part_dir" -r "$recover_types" "$loopdev")
    "$XFS_UNDELETE" "${xfs_undelete_args[@]}" || {
      printf 'xfs_undelete returned non-zero for partition %s\n' "$part_id"
      status="partial"
    }

    losetup -d "$loopdev" || true
    register_artifacts_from_dir "$db" "xfs_undelete" "$part_dir" "$run_id"
  done

} 2>&1 | tee "$log_path" || { status="partial"; notes="one or more XFS recovery steps failed"; }

record_scan_end "$db" "$run_id" "$status" "$notes"
