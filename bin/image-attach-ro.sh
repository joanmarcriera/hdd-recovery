#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-attach-ro.sh <db-path>

Attaches the image read-only as a loop device with partition scanning enabled.
Writes state to the per-image state directory for later cleanup.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd losetup

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
state_file="$export_root/state/loop.env"
mkdir -p "$(dirname "$state_file")"

loopdev="$(losetup -r --show -fP "$image")"
printf 'LOOPDEV=%s\nIMAGE=%s\n' "$loopdev" "$image" > "$state_file"
printf '%s\n' "$loopdev"

