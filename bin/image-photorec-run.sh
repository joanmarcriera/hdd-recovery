#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-photorec-run.sh <db-path> [--profile broad|photos]

Runs PhotoRec in unattended /cmd mode when supported by the installed version.
Output is kept under recovered/photorec/<profile>-<timestamp>.
EOF
}

db="${1:-}"
profile="broad"
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) profile="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
case "$profile" in
  broad|photos) ;;
  *) die "unsupported profile: $profile" ;;
esac

need_cmd photorec
need_cmd timeout

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
tool_dir="$export_root/recovered/photorec/${profile}-${timestamp}"
log_path="$export_root/logs/photorec-${profile}-${timestamp}.log"
mkdir -p "$tool_dir"

run_id="$(record_scan_start "$db" "photorec-$profile" "$0 $db --profile $profile" "$log_path" "$tool_dir")"
status="ok"
notes=""

finish_run() {
  record_scan_end "$db" "$run_id" "$status" "$notes"
}
trap finish_run EXIT

case "$profile" in
  broad)
    cmd_sequence="fileopt,everything,enable,search"
    ;;
  photos)
    cmd_sequence="fileopt,everything,disable,jpg,enable,png,enable,gif,enable,bmp,enable,tif,enable,search"
    ;;
esac

{
  printf 'PhotoRec version:\n'
  photorec /version
  printf '\nImage: %s\nOutput: %s\nProfile: %s\nCommand sequence: %s\n\n' "$image" "$tool_dir" "$profile" "$cmd_sequence"
  printf 'Running unattended PhotoRec. If this installed build rejects /cmd, this stage will fail quickly and preserve the log.\n'
  photorec /log /d "$tool_dir" /cmd "$image" "$cmd_sequence"
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="PhotoRec unattended run exited non-zero; review log and consider manual interactive PhotoRec"
}

printf '\nRegistering PhotoRec artifacts...\n' | tee -a "$log_path"
register_artifacts_from_dir "$db" "photorec" "$tool_dir" "$run_id" 2>&1 | tee -a "$log_path" || {
  status="partial"
  notes="${notes:+$notes; }artifact registration failed after PhotoRec output was created"
}

trap - EXIT
record_scan_end "$db" "$run_id" "$status" "$notes"
