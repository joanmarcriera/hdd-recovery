#!/usr/bin/env bash
set -Eeuo pipefail

# Watch live iostat extended metrics for one block device.
#
# Default device: /dev/sdc
# Usage:
#   ./watch-iostat-device.sh
#   ./watch-iostat-device.sh /dev/sdb
#   ./watch-iostat-device.sh sdb
#
# Requires:
#   sudo apt install sysstat
#
# Notes:
#   - iostat -x shows extended disk statistics.
#   - iostat -z hides devices with no activity.
#   - iostat -y skips the first report, which is the average since boot.
#   - This script prints the header once, then only the selected device rows.

DEVICE="${1:-/dev/sdc}"
INTERVAL="${2:-1}"

# Accept either "sdc" or "/dev/sdc".
DEVICE_NAME="$(basename "$DEVICE")"

if ! command -v iostat >/dev/null 2>&1; then
    echo "Error: iostat is not installed. Install it with: sudo apt install sysstat" >&2
    exit 1
fi

if [[ ! -b "/dev/$DEVICE_NAME" ]]; then
    echo "Error: /dev/$DEVICE_NAME is not a block device." >&2
    exit 1
fi

cat <<'EOF'
Column notes for iostat -xz:

Device    Block device name.
r/s       Read operations per second.
rkB/s     Kilobytes read per second.
rrqm/s    Read requests merged per second.
%rrqm     Percentage of read requests merged.
r_await   Average read latency in ms, including queue time.
rareq-sz  Average read request size in kB.

w/s       Write operations per second.
wkB/s     Kilobytes written per second.
wrqm/s    Write requests merged per second.
%wrqm     Percentage of write requests merged.
w_await   Average write latency in ms, including queue time.
wareq-sz  Average write request size in kB.

d/s       Discard/TRIM operations per second.
dkB/s     Kilobytes discarded per second.
d_await   Average discard latency in ms.

f/s       Flush requests per second.
f_await   Average flush latency in ms.

aqu-sz    Average queue size. If this rises, work is waiting on the disk.
%util     Percentage of time the device was busy. Near 100% usually means saturation.
EOF

echo
echo "Watching /dev/$DEVICE_NAME every ${INTERVAL}s..."
echo

iostat -xz -y "$INTERVAL" "/dev/$DEVICE_NAME" | awk -v dev="$DEVICE_NAME" '
/^Device/ && !header_printed {
    print
    header_printed = 1
    next
}

$1 == dev {
    print
    fflush()
}
'
