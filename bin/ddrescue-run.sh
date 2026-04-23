#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ddrescue-run.sh <job.conf> <phase> [--run]

Phases:
  plan     Show resolved paths, checks, and the ddrescue command for the phase.
  first    Native first pass: rescue easy data first, no retry passes.
  retry    Direct retry pass over remaining unread areas.
  reverse  Reverse-direction retry pass for remaining unread areas.
  retrim   Mark failed blocks for retrim and try again conservatively.

Default behavior is preview only. Add --run to actually execute ddrescue.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

job_conf="${1:-}"
phase="${2:-}"
mode="${3:-}"

[[ -n "$job_conf" && -n "$phase" ]] || { usage; exit 1; }
[[ -f "$job_conf" ]] || die "job config not found: $job_conf"

# shellcheck disable=SC1090
source "$job_conf"

need_cmd ddrescue
need_cmd lsblk
need_cmd findmnt

[[ -n "${SOURCE_DEV:-}" ]] || die "SOURCE_DEV is empty in $job_conf"
[[ -n "${SOURCE_BY_PATH:-}" ]] || die "SOURCE_BY_PATH is empty in $job_conf"
[[ -n "${BASENAME:-}" ]] || die "BASENAME is empty in $job_conf"
[[ -b "$SOURCE_DEV" ]] || die "SOURCE_DEV is not a block device: $SOURCE_DEV"
[[ -L "/dev/disk/by-path/$SOURCE_BY_PATH" ]] || die "by-path does not exist: /dev/disk/by-path/$SOURCE_BY_PATH"
[[ "$(readlink -f "/dev/disk/by-path/$SOURCE_BY_PATH")" == "$(readlink -f "$SOURCE_DEV")" ]] || die "by-path target does not match SOURCE_DEV"
findmnt -rn "$DEST_MOUNT" >/dev/null 2>&1 || die "destination mount is not mounted: $DEST_MOUNT"

mkdir -p "$IMAGES_DIR" "$LOGS_DIR" "$MANIFESTS_DIR"

mounted_parts="$(lsblk -nr -o NAME,MOUNTPOINT "$SOURCE_DEV" | awk 'NF >= 2 { print }')"
if [[ -n "$mounted_parts" ]]; then
  printf 'Mounted entries under source disk:\n%s\n' "$mounted_parts" >&2
  die "refusing to proceed while any partition under $SOURCE_DEV appears mounted"
fi

case "$phase" in
  plan|first|retry|reverse|retrim) ;;
  *) die "unknown phase: $phase" ;;
esac

common_args=(
  --ask
  -v
  "--mapfile-interval=${MAPFILE_INTERVAL}"
)

case "$phase" in
  plan|first)
    phase_name="first-pass"
    phase_args=(
      -n
      -p
      "--log-events=${EVENT_LOG}"
      "--log-rates=${RATE_LOG}"
    )
    ;;
  retry)
    phase_name="retry-pass"
    phase_args=(
      -d
      -O
      "-r${RETRY_PASSES}"
      "--pause-on-error=${PAUSE_ON_ERROR}"
      "--log-events=${EVENT_LOG}"
      "--log-rates=${RATE_LOG}"
    )
    ;;
  reverse)
    phase_name="reverse-pass"
    phase_args=(
      -d
      -O
      -R
      "-r${REVERSE_RETRY_PASSES}"
      "--pause-on-error=${PAUSE_ON_ERROR}"
      "--log-events=${EVENT_LOG}"
      "--log-rates=${RATE_LOG}"
    )
    ;;
  retrim)
    phase_name="retrim-pass"
    phase_args=(
      -d
      -O
      -M
      "-r${RETRIM_RETRY_PASSES}"
      "--pause-on-error=${PAUSE_ON_ERROR}"
      "--log-events=${EVENT_LOG}"
      "--log-rates=${RATE_LOG}"
    )
    ;;
esac

cmd=(ddrescue "${common_args[@]}" "${phase_args[@]}" "$SOURCE_DEV" "$IMAGE_FILE" "$MAP_FILE")

printf 'Job: %s\n' "${JOB_NAME:-unnamed}"
printf 'Phase: %s\n' "$phase_name"
printf 'Source dev: %s\n' "$SOURCE_DEV"
printf 'Source by-path: %s\n' "$SOURCE_BY_PATH"
printf 'Image file: %s\n' "$IMAGE_FILE"
printf 'Map file: %s\n' "$MAP_FILE"
printf 'Event log: %s\n' "$EVENT_LOG"
printf 'Rate log: %s\n' "$RATE_LOG"
printf '\nCommand:\n'
printf ' '
printf '%q ' "${cmd[@]}"
printf '\n'

if [[ "$phase" == "plan" || "$mode" != "--run" ]]; then
  printf '\nPreview only. Re-run with --run to execute.\n'
  exit 0
fi

_smart_snap="/root/recovery-monitor/smart-snapshot.sh"
if [[ -x "$_smart_snap" ]]; then
  printf 'Capturing pre-run SMART snapshot...\n'
  ONCE=1 SNAP_DIR="${LOGS_DIR}/smart" "$_smart_snap" "$SOURCE_DEV" "$DEST_DEV" 2>/dev/null || true
fi

exec "${cmd[@]}"

