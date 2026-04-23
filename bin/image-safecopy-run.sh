#!/usr/bin/env bash
# Alternative imager for disks that ddrescue cannot handle — runs safecopy stage1/2/3.
# Stages chain via badblocks files: stage2 reads stage1's badblocks, stage3 reads stage2's.
# Use when ddrescue exits immediately or produces extremely poor read rates.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  image-safecopy-run.sh <job.conf> <stage> [--run]

Stages:
  stage1  Fast pass: rescue most data, no retries, skip bad areas.
  stage2  Slow pass: find exact bad-area boundaries (reads stage1 badblocks).
  stage3  Maximum-retry pass: head realignment tricks (reads stage2 badblocks).
  all     Run stage1 → stage2 → stage3 in sequence.

Default behavior is preview only. Add --run to execute.

NOTE: safecopy writes to the same IMAGE_FILE as ddrescue. Use one or the other,
not both, unless you are intentionally supplementing a partial ddrescue image.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

job_conf="${1:-}"
stage="${2:-}"
mode="${3:-}"

[[ -n "$job_conf" && -n "$stage" ]] || { usage; exit 1; }
[[ -f "$job_conf" ]] || die "job config not found: $job_conf"

# shellcheck disable=SC1090
source "$job_conf"

need_cmd safecopy
need_cmd lsblk
need_cmd findmnt

[[ -n "${SOURCE_DEV:-}" ]]  || die "SOURCE_DEV is empty in $job_conf"
[[ -n "${BASENAME:-}" ]]    || die "BASENAME is empty in $job_conf"
[[ -b "$SOURCE_DEV" ]]      || die "SOURCE_DEV is not a block device: $SOURCE_DEV"
findmnt -rn "$DEST_MOUNT" >/dev/null 2>&1 || die "destination mount not mounted: $DEST_MOUNT"

mounted_parts="$(lsblk -nr -o NAME,MOUNTPOINT "$SOURCE_DEV" | awk 'NF >= 2 { print }')"
[[ -z "$mounted_parts" ]] || die "refusing to proceed — partition under $SOURCE_DEV is mounted"

mkdir -p "$IMAGES_DIR" "$LOGS_DIR"

BB1="$LOGS_DIR/${BASENAME}-safecopy-stage1.badblocks"
BB2="$LOGS_DIR/${BASENAME}-safecopy-stage2.badblocks"
BB3="$LOGS_DIR/${BASENAME}-safecopy-stage3.badblocks"
DONE1="$LOGS_DIR/${BASENAME}-safecopy-stage1.done"
DONE2="$LOGS_DIR/${BASENAME}-safecopy-stage2.done"
DONE3="$LOGS_DIR/${BASENAME}-safecopy-stage3.done"

run_stage() {
  local s="$1"
  local log="$LOGS_DIR/${BASENAME}-safecopy-${s}.log"
  local cmd=()

  case "$s" in
    stage1) cmd=(safecopy --stage1 -o "$BB1" "$SOURCE_DEV" "$IMAGE_FILE") ;;
    stage2) cmd=(safecopy --stage2 -I "$BB1" -o "$BB2" "$SOURCE_DEV" "$IMAGE_FILE") ;;
    stage3) cmd=(safecopy --stage3 -I "$BB2" -o "$BB3" "$SOURCE_DEV" "$IMAGE_FILE") ;;
  esac

  printf 'Stage: %s\n' "$s"
  printf 'Source: %s\n' "$SOURCE_DEV"
  printf 'Image: %s\n'  "$IMAGE_FILE"
  printf '\nCommand:\n '
  printf '%q ' "${cmd[@]}"
  printf '\n'

  if [[ "$mode" != "--run" ]]; then
    printf '\nPreview only. Re-run with --run to execute.\n'
    return 0
  fi

  printf '\n--- running %s ---\n' "$s"
  "${cmd[@]}" 2>&1 | tee "$log"
  local exit_code="${PIPESTATUS[0]}"
  # Mark done regardless of exit — partial output is still useful
  touch "${!DONE_VAR}"
  return "$exit_code"
}

# Resolve the per-stage done marker variable name
case "$stage" in
  stage1) DONE_VAR="DONE1" ;;
  stage2) DONE_VAR="DONE2" ;;
  stage3) DONE_VAR="DONE3" ;;
  all)    DONE_VAR="DONE3" ;;
  *)      die "unknown stage: $stage (expected stage1|stage2|stage3|all)" ;;
esac

case "$stage" in
  stage1) run_stage stage1 ;;
  stage2)
    [[ -f "$BB1" ]] || die "stage1 badblocks file not found ($BB1). Run stage1 first."
    run_stage stage2
    ;;
  stage3)
    [[ -f "$BB2" ]] || die "stage2 badblocks file not found ($BB2). Run stage2 first."
    run_stage stage3
    ;;
  all)
    DONE_VAR="DONE1"; run_stage stage1
    DONE_VAR="DONE2"; run_stage stage2
    DONE_VAR="DONE3"; run_stage stage3
    ;;
esac
