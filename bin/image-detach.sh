#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-detach.sh <db-path>

Detaches any loop device previously attached by image-attach-ro.sh.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd losetup

export_root="$(db_image_export_root "$db")"
state_file="$export_root/state/loop.env"
[[ -f "$state_file" ]] || die "state file not found: $state_file"

# shellcheck disable=SC1090
source "$state_file"
[[ -n "${LOOPDEV:-}" ]] || die "LOOPDEV missing in state file"
losetup -d "$LOOPDEV"
rm -f "$state_file"

