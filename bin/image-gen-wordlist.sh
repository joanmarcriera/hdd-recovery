#!/usr/bin/env bash
# Build a targeted password wordlist from artifacts already found on the disk
# (bulk_extractor email/domain/name features) and, by default, append a base
# wordlist (rockyou) after the personal candidates. Feed the result to:
#   image-crack-wallet.sh <db> --wordlist <out> --run
#
# Personal candidates are tried first, so hashcat/john exhaust the high-value
# guesses before falling through to the generic list. Read-only against the DB
# except for a provenance row in scan_runs.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-gen-wordlist.sh <db-path> [options]

Options:
  --out <path>     output wordlist (default: <export_root>/wordlists/targeted-<ts>.txt)
  --base <path>    base wordlist appended after targeted tokens
                   (default: WORDLIST_PATH or config/wordlists/rockyou.txt)
  --no-base        emit only disk-derived tokens (no base wordlist)
  --min-len N      minimum token length (default 3)
  --max-len N      maximum token length (default 40)
  --max-total N    cap total lines written (0 = unlimited, default 0)

Requires a prior raw bulk_extractor pass (bulk-extractor-raw) to have populated
bulk_extractor_hits; otherwise only the base wordlist is emitted.
EOF
}

db="${1:-}"
[[ -n "$db" ]] || { usage; exit 1; }
case "$db" in -h|--help) usage; exit 0 ;; esac
[[ -f "$db" ]] || die "database not found: $db"
shift

out=""
base="${WORDLIST_PATH:-$ROOT_DIR/config/wordlists/rockyou.txt}"
use_base=1
min_len=3
max_len=40
max_total=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)       out="$2"; shift 2 ;;
    --base)      base="$2"; shift 2 ;;
    --no-base)   use_base=0; shift ;;
    --min-len)   min_len="$2"; shift 2 ;;
    --max-len)   max_len="$2"; shift 2 ;;
    --max-total) max_total="$2"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

need_cmd python3

export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
[[ -n "$out" ]] || out="$export_root/wordlists/targeted-$timestamp.txt"
mkdir -p "$(dirname "$out")" "$export_root/logs"
log_path="$export_root/logs/gen-wordlist-$timestamp.log"

[[ "$use_base" -eq 1 && -f "$base" ]] || base=""
[[ "$use_base" -eq 1 && -z "$base" ]] && log "base wordlist not found or disabled — emitting targeted tokens only"

run_id="$(record_scan_start "$db" "gen-wordlist" "$0 $db" "$log_path" "$out")"
status="ok"
notes=""

{
  PYTHONPATH="$ROOT_DIR" python3 - "$db" "$out" "$base" "$min_len" "$max_len" "$max_total" <<'PY'
import os, sqlite3, sys

sys.path.insert(0, os.environ["PYTHONPATH"])
from lib.wordlist import build_wordlist, merge_with_base  # noqa: E402

db_path, out_path, base_path, min_len_s, max_len_s, max_total_s = sys.argv[1:7]
min_len, max_len, max_total = int(min_len_s), int(max_len_s), int(max_total_s)

conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
try:
    rows = conn.execute(
        "SELECT feature_file, value FROM bulk_extractor_hits "
        "WHERE value IS NOT NULL AND value != ''"
    ).fetchall()
except sqlite3.OperationalError as e:
    print(f"bulk_extractor_hits unavailable ({e}); base-only wordlist")
    rows = []
finally:
    conn.close()

targeted = build_wordlist(rows, min_len=min_len, max_len=max_len)
print(f"Disk-derived candidate tokens: {len(targeted)} (from {len(rows)} feature rows)")

written = 0
def base_lines():
    if base_path and os.path.exists(base_path):
        with open(base_path, "r", errors="replace") as fh:
            yield from fh

with open(out_path, "w") as out_fh:
    for line in merge_with_base(targeted, base_lines(), max_total=max_total):
        out_fh.write(line + "\n")
        written += 1

base_note = base_path if (base_path and os.path.exists(base_path)) else "none"
print(f"Base wordlist: {base_note}")
print(f"Total lines written: {written}")
print(f"Output: {out_path}")
# expose counts for the scan_runs note
with open(out_path + ".meta", "w") as m:
    m.write(f"targeted={len(targeted)} total={written} base={base_note}\n")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="wordlist generation failed or completed with errors"
}

if [[ -f "$out.meta" ]]; then
  notes="${notes:-$(cat "$out.meta")}"
  rm -f "$out.meta"
fi
notes="${notes:-wrote $out}"
record_scan_end "$db" "$run_id" "$status" "$notes"
log "wordlist ready: $out"
