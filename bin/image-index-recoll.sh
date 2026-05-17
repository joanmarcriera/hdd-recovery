#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-index-recoll.sh <db-path> [--path <dir>]

Builds an optional Recoll index over a recovered/exported directory tree.
The default target is the per-image recovered directory.
EOF
}

db="${1:-}"
shift $(( $# > 0 ? 1 : 0 )) || true
target=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --path) target="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd recollindex

export_root="$(db_image_export_root "$db")"
target="${target:-$export_root/recovered}"
[[ -d "$target" ]] || die "index target directory not found: $target"

conf_dir="$export_root/indexes/recoll"
log_path="$export_root/logs/recoll-index.log"
mkdir -p "$conf_dir" "$conf_dir/db"

cat > "$conf_dir/recoll.conf" <<EOF
topdirs = $target
dbdir = $conf_dir/db
loglevel = 4
EOF

run_id="$(record_scan_start "$db" "recoll-index" "$0 $db --path $target" "$log_path" "$conf_dir")"
{
  RECOLL_CONFDIR="$conf_dir" recollindex -c "$conf_dir" -z
} 2>&1 | tee "$log_path"
record_scan_end "$db" "$run_id" "ok"
