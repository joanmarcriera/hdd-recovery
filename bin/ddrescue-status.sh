#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ddrescue-status.sh <mapfile> [max_ranges]

Shows the ddrescuelog summary and the first unfinished map ranges from a GNU
ddrescue mapfile. Unfinished types are any status other than '+'.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

mapfile="${1:-}"
max_ranges="${2:-20}"

[[ -n "$mapfile" ]] || { usage; exit 1; }
[[ -f "$mapfile" ]] || die "mapfile not found: $mapfile"
command -v ddrescuelog >/dev/null 2>&1 || die "ddrescuelog not found"

printf 'Mapfile: %s\n\n' "$mapfile"
ddrescuelog -t "$mapfile"

printf '\nFirst %s unfinished map ranges:\n' "$max_ranges"
awk -v limit="$max_ranges" '
  BEGIN { shown = 0 }
  /^#/ { next }
  NF < 3 { next }
  $3 == "+" { next }
  {
    shown++
    printf "%3d  pos=%s  size=%s  status=%s\n", shown, $1, $2, $3
    if (shown >= limit) exit
  }
  END {
    if (shown == 0) {
      print "All ranges finished (+)."
    }
  }
' "$mapfile"

