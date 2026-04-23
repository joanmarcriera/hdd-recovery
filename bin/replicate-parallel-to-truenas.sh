#!/usr/bin/env bash
set -Eeuo pipefail

SRC="${1:-/mnt/recovery16tb}"
DST="${2:-/mnt/CryptoBackup}"
JOBS="${3:-4}"

LOG_FILE="/tmp/parallel-rsync-$(date +%Y%m%d-%H%M%S).log"

command -v rsync >/dev/null 2>&1 || {
    echo "Error: rsync is not installed." >&2
    exit 1
}

command -v parallel >/dev/null 2>&1 || {
    echo "Error: GNU parallel is not installed." >&2
    exit 1
}

if [[ ! -d "$SRC" ]]; then
    echo "Error: source directory does not exist: $SRC" >&2
    exit 1
fi

if [[ ! -d "$DST" ]]; then
    echo "Error: destination directory does not exist: $DST" >&2
    exit 1
fi

TOTAL_ITEMS="$(find "$SRC" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"

echo "Source      : $SRC"
echo "Destination : $DST"
echo "Jobs        : $JOBS"
echo "Items       : $TOTAL_ITEMS"
echo "Log         : $LOG_FILE"
echo

find "$SRC" -mindepth 1 -maxdepth 4 -print0 | \
parallel -0 -j "$JOBS" --bar --tag --joblog "$LOG_FILE" \
    rsync -aAX --numeric-ids --chmod=go+rX -- {} "$DST"/

echo
echo "Done."
echo "Job log: $LOG_FILE"
