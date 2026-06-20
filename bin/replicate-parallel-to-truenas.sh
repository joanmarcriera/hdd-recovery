#!/usr/bin/env bash
# Replicate the recovery tree to a TrueNAS (or any) destination in parallel,
# with per-item logging, exit-code aggregation, a transfer manifest, and an
# optional checksum verification pass (#16).
#
# Safer and more correct than the previous version, which fanned out over
# `find -maxdepth 4` and rsynced every nested path into $DST/ — flattening the
# directory structure. This parallelizes over TOP-LEVEL entries only, so rsync
# preserves each subtree, and it never copies on a preview.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# Pick up TRUENAS_DEST_ROOT default without requiring the DB layer.
ENV_FILE="${HDD_RECOVERY_CONFIG:-$ROOT_DIR/config/analysis-pipeline.env}"
# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE" || true

usage() {
  cat <<'EOF'
Usage:
  replicate-parallel-to-truenas.sh [SRC] [DST] [JOBS] [options]

Positional (all optional; kept for backward compatibility):
  SRC    source tree            (default: /mnt/recovery16tb)
  DST    destination tree       (default: $TRUENAS_DEST_ROOT or /mnt/CryptoBackup)
  JOBS   parallel workers       (default: 4)

Options:
  --run         actually copy (default: dry-run preview, rsync -n)
  --checksum    after copying, run a checksum verification pass and report any
                files that still differ (detects silent corruption)
  --log-dir D   per-item rsync logs + manifest live here
                (default: /tmp/replicate-<timestamp>/)
  -h, --help    this help

Outputs:
  <log-dir>/manifest.tsv     item, status, rc, kbytes  (one row per top-level item)
  <log-dir>/<item>.rsync.log per-item rsync --log-file
  Exit status is non-zero if any item failed.

Uses GNU parallel when available, otherwise falls back to xargs -P.
EOF
}

SRC="/mnt/recovery16tb"
DST="${TRUENAS_DEST_ROOT:-/mnt/CryptoBackup}"
JOBS="4"
DO_RUN=0
DO_CHECKSUM=0
LOG_DIR=""
positional=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run)       DO_RUN=1; shift ;;
    --checksum)  DO_CHECKSUM=1; shift ;;
    --log-dir)   LOG_DIR="$2"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    --*)         echo "unknown option: $1" >&2; usage; exit 1 ;;
    *)           positional+=("$1"); shift ;;
  esac
done
[[ ${#positional[@]} -ge 1 ]] && SRC="${positional[0]}"
[[ ${#positional[@]} -ge 2 ]] && DST="${positional[1]}"
[[ ${#positional[@]} -ge 3 ]] && JOBS="${positional[2]}"

command -v rsync >/dev/null 2>&1 || { echo "Error: rsync not installed." >&2; exit 1; }
[[ -d "$SRC" ]] || { echo "Error: source directory does not exist: $SRC" >&2; exit 1; }
if [[ $DO_RUN -eq 1 && ! -d "$DST" ]]; then
  echo "Error: destination directory does not exist: $DST" >&2; exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
[[ -n "$LOG_DIR" ]] || LOG_DIR="/tmp/replicate-$TS"
mkdir -p "$LOG_DIR"
MANIFEST="$LOG_DIR/manifest.tsv"
printf 'item\tstatus\trc\tkbytes\n' > "$MANIFEST"

# Archive flags preserve ACLs (-A) and xattrs (-X) for forensic fidelity on the
# Linux recovery host. Overridable via REPLICATE_RSYNC_OPTS for platforms whose
# rsync lacks them (e.g. macOS openrsync) or special cases.
read -r -a RSYNC_OPTS <<< "${REPLICATE_RSYNC_OPTS:- -aAX --numeric-ids --chmod=go+rX}"
[[ $DO_RUN -eq 1 ]] || RSYNC_OPTS+=(-n)

TOTAL_ITEMS="$(find "$SRC" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
echo "Source      : $SRC"
echo "Destination : $DST"
echo "Jobs        : $JOBS"
echo "Items       : $TOTAL_ITEMS"
echo "Mode        : $([[ $DO_RUN -eq 1 ]] && echo RUN || echo 'dry-run (use --run to copy)')"
echo "Checksum    : $([[ $DO_CHECKSUM -eq 1 ]] && echo 'verify after copy' || echo off)"
echo "Log dir     : $LOG_DIR"
echo

# Worker: replicate ONE top-level item, log to its own file, append a manifest
# row with the real rsync exit code. Exported so parallel/xargs can call it.
# Arrays don't survive `export`, so the rsync options travel as a flat string
# and the worker rebuilds the array from it.
sync_one() {
  local item="$1"
  local name; name="$(basename "$item")"
  local logf="$LOG_DIR/$name.rsync.log"
  local bytes; bytes="$(du -sk "$item" 2>/dev/null | cut -f1 || echo 0)"
  local opts; IFS=' ' read -r -a opts <<< "$RSYNC_OPTS_STR"
  local rc=0
  rsync "${opts[@]}" --log-file="$logf" -- "$item" "$DST/" || rc=$?
  local status; status="$([[ $rc -eq 0 ]] && echo ok || echo FAILED)"
  # Single short line — atomic append under PIPE_BUF across workers.
  printf '%s\t%s\t%s\t%s\n' "$name" "$status" "$rc" "$bytes" >> "$MANIFEST"
  return "$rc"
}
export -f sync_one
export LOG_DIR DST MANIFEST
export RSYNC_OPTS_STR="${RSYNC_OPTS[*]}"

run_failed=0
if command -v parallel >/dev/null 2>&1; then
  find "$SRC" -mindepth 1 -maxdepth 1 -print0 \
    | parallel -0 -j "$JOBS" --bar sync_one || run_failed=$?
else
  echo "(GNU parallel not found — using xargs -P fallback)"
  find "$SRC" -mindepth 1 -maxdepth 1 -print0 \
    | xargs -0 -P "$JOBS" -I{} bash -c 'sync_one "$@"' _ {} || run_failed=$?
fi

# ── Aggregate ────────────────────────────────────────────────────────────────
failed_count="$(awk -F'\t' 'NR>1 && $2=="FAILED"' "$MANIFEST" | wc -l | tr -d ' ')"
ok_count="$(awk -F'\t' 'NR>1 && $2=="ok"' "$MANIFEST" | wc -l | tr -d ' ')"
echo
echo "Copied OK   : $ok_count"
echo "Failed      : $failed_count"
if [[ "$failed_count" -gt 0 ]]; then
  echo "Failed items:"
  awk -F'\t' -v ld="$LOG_DIR" \
    'NR>1 && $2=="FAILED" {printf "  %s (rc=%s) — see %s/%s.rsync.log\n", $1, $3, ld, $1}' \
    "$MANIFEST"
fi

# ── Optional checksum verification pass ──────────────────────────────────────
verify_mismatch=0
if [[ $DO_CHECKSUM -eq 1 && $DO_RUN -eq 1 ]]; then
  echo
  echo "Checksum verification pass…"
  while IFS= read -r -d '' item; do
    name="$(basename "$item")"
    # -n dry-run + --checksum + itemize; any '>f' line means content still differs.
    diffs="$(rsync -nai --checksum -aAX -- "$item" "$DST/" 2>/dev/null \
              | grep -c '^>f' || true)"
    if [[ "$diffs" -gt 0 ]]; then
      echo "  MISMATCH: $name ($diffs file(s) differ after copy)"
      verify_mismatch=$((verify_mismatch + diffs))
    fi
  done < <(find "$SRC" -mindepth 1 -maxdepth 1 -print0)
  echo "Verification mismatches: $verify_mismatch"
fi

echo
echo "Manifest    : $MANIFEST"
[[ $DO_RUN -eq 1 ]] || echo "Preview only — no data copied. Re-run with --run."

# Non-zero if anything failed or verification found mismatches.
[[ "$failed_count" -eq 0 && "$verify_mismatch" -eq 0 ]] || exit 1
