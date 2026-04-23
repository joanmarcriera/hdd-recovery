#!/usr/bin/env bash
# Detailed ddrescue map view — text summary + block-region listing.
# For a graphical view install ddrescueview and run: ddrescueview <mapfile>
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  image-mapview.sh <mapfile>

Prints a detailed text summary of a ddrescue map file using ddrescuelog.
Shows total coverage, individual block regions, and error clusters.

For a graphical block map, run in a separate terminal:
  ddrescueview <mapfile>
EOF
}

mapfile="${1:-}"
[[ -n "$mapfile" ]] || { usage; exit 1; }
[[ -f "$mapfile" ]] || { printf 'ERROR: map file not found: %s\n' "$mapfile" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || printf 'WARNING: %s not found\n' "$1" >&2; }
need_cmd ddrescuelog

printf '=== Map file: %s ===\n\n' "$mapfile"

printf '--- Coverage summary ---\n'
ddrescuelog -t "$mapfile" 2>/dev/null || printf '(ddrescuelog -t failed)\n'

printf '\n--- Block regions (pos / size / status) ---\n'
# ddrescuelog -p prints mapfile in human-readable position+status form
ddrescuelog -p "$mapfile" 2>/dev/null || {
  # Fallback: parse hex lines directly
  printf '(ddrescuelog -p not available — raw map follows)\n\n'
  grep -v '^#' "$mapfile" | head -100
}

printf '\n--- Non-rescued region count ---\n'
non_rescued="$(grep -v '^#' "$mapfile" | awk '$3 != "+" { count++ } END { print count+0 }')"
printf 'Non-rescued regions: %s\n' "$non_rescued"

printf '\n--- ddrescueview (graphical) ---\n'
if command -v ddrescueview >/dev/null 2>&1; then
  printf 'ddrescueview is installed. Run in a terminal with a display:\n'
  printf '  ddrescueview %s\n' "$mapfile"
else
  printf 'ddrescueview not found. Install with: apt install ddrescueview\n'
fi
