#!/usr/bin/env bash
# Run focused Volatility3 plugins against winmem-extract outputs.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-volatility-scan.sh <db-path> [--plugins <csv>] [--run]

Default is dry-run. Pass --run to execute Volatility3 and write findings.

Default plugins:
  windows.pslist
  windows.cmdline
  windows.dumpfiles
  windows.hashdump
  windows.lsadump
  windows.cachedump
  windows.netscan

Outputs:
  <export_root>/hits/volatility/<timestamp>/<memfile>/<plugin>.tsv
  <export_root>/recovered/volatility/<timestamp>/...
EOF
}

db="${1:-}"
plugins_csv="windows.pslist,windows.cmdline,windows.dumpfiles,windows.hashdump,windows.lsadump,windows.cachedump,windows.netscan"
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plugins) plugins_csv="$2"; shift 2 ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd sqlite3
need_cmd python3

export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/volatility/$timestamp"
dump_root="$export_root/recovered/volatility/$timestamp"
log_path="$export_root/logs/volatility-scan-$timestamp.log"
mkdir -p "$out_dir" "$dump_root" "$(dirname "$log_path")"

memfiles_tsv="$out_dir/memfiles.tsv"
sqlite3 -separator '|' "$db" <<'SQL' > "$memfiles_tsv"
SELECT id, full_path
FROM recovered_artifacts
WHERE method='winmem-extract'
  AND full_path IS NOT NULL
  AND full_path <> ''
ORDER BY id;
SQL
mem_count="$(grep -c '.' "$memfiles_tsv" 2>/dev/null || true)"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: Volatility3 scan only. Re-run with --run to execute.\n'
  printf 'Memory files: %s\n' "$mem_count"
  printf 'Plugins: %s\n' "$plugins_csv"
  while IFS='|' read -r artifact_id mem_path; do
    printf 'artifact_id=%s file=%s\n' "$artifact_id" "$mem_path"
  done < "$memfiles_tsv"
  exit 0
fi

need_cmd vol

run_id="$(record_scan_start "$db" "volatility3" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""
partial_marker="$out_dir/.partial"

{
  if [[ "$mem_count" -eq 0 ]]; then
    printf 'No winmem-extract artifacts found; run image-extract-winmem.sh first.\n'
  fi

  IFS=',' read -r -a plugins <<< "$plugins_csv"
  while IFS='|' read -r artifact_id mem_path; do
    [[ -f "$mem_path" ]] || {
      printf 'Skipping missing memory file: %s\n' "$mem_path"
      touch "$partial_marker"
      continue
    }
    mem_label="$(basename "$mem_path" | tr -c 'A-Za-z0-9._-' '_')"
    mem_out="$out_dir/$mem_label"
    mkdir -p "$mem_out"

    for plugin in "${plugins[@]}"; do
      plugin_clean="$(printf '%s' "$plugin" | xargs)"
      [[ -n "$plugin_clean" ]] || continue
      plugin_file="${plugin_clean//./_}.tsv"
      output_path="$mem_out/$plugin_file"
      printf 'Running Volatility3 %s on %s\n' "$plugin_clean" "$mem_path"

      rc=0
      if [[ "$plugin_clean" == "windows.dumpfiles" ]]; then
        plugin_dump_dir="$dump_root/$mem_label/dumpfiles"
        mkdir -p "$plugin_dump_dir"
        vol -r csv -f "$mem_path" "$plugin_clean" --regex 'wallet|electrum|metamask|keystore' --dump-dir "$plugin_dump_dir" > "$output_path" 2>&1 || rc=$?
      else
        vol -r csv -f "$mem_path" "$plugin_clean" > "$output_path" 2>&1 || rc=$?
      fi
      if [[ "$rc" -ne 0 ]]; then
        printf 'Volatility3 plugin %s failed rc=%s; see %s\n' "$plugin_clean" "$rc" "$output_path"
        touch "$partial_marker"
      fi

      python3 - "$db" "$artifact_id" "$mem_path" "$plugin_clean" "$output_path" <<'PY'
import csv
import sqlite3
import sys
from datetime import datetime, timezone

db_path, artifact_id, mem_path, plugin, output_path = sys.argv[1:6]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
conn = sqlite3.connect(db_path)
rows = []
try:
    with open(output_path, "r", errors="replace") as f:
        sample = f.read(4096)
        f.seek(0)
        if "," in sample:
            reader = csv.reader(f)
            for row in reader:
                text = " | ".join(cell.strip() for cell in row if cell.strip())
                if text:
                    rows.append(text)
        else:
            rows = [line.strip() for line in f if line.strip()]
except FileNotFoundError:
    rows = []

for text in rows[:5000]:
    score = 60 if any(k in text.lower() for k in ("wallet", "seed", "electrum", "metamask", "keystore")) else 0
    conn.execute(
        """
        INSERT INTO findings(source_tool, category, artifact_id, path, key, value, score, created_at)
        VALUES('volatility3', 'volatility', ?, ?, ?, ?, ?, ?)
        """,
        (int(artifact_id), mem_path, plugin, text[:4000], score, now),
    )
conn.commit()
conn.close()
PY
    done
  done < "$memfiles_tsv"

  register_artifacts_from_dir "$db" "volatility-dump" "$dump_root" "$run_id"
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="volatility3 scan failed or incomplete; check log"
}

if [[ -f "$partial_marker" ]]; then
  status="partial"
fi
if [[ -z "$notes" ]]; then
  findings="$(sqlite3 -noheader "$db" "SELECT COUNT(*) FROM findings WHERE source_tool='volatility3';")"
  notes="volatility findings=$findings memfiles=$mem_count"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
