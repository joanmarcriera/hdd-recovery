#!/usr/bin/env bash
# Scan recovered text-like files for consecutive BIP39 seed word runs.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-text-seed-scan.sh <db-path> [--min-words <n>] [--wordlist <path>] [--run]

Default is dry-run. Pass --run to write findings and notes.

Scans recovered text-like files under <export_root>/recovered:
  .txt .html .htm .md .csv .rtf .json .log .ini .conf .yml .yaml

Environment:
  TEXT_SEED_SCAN_MAX_BYTES  maximum file size to read, default 10485760

Outputs:
  <export_root>/hits/text-seed/<timestamp>/hits.tsv
EOF
}

db="${1:-}"
min_words=6
wordlist_path=""
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --min-words) min_words="$2"; shift 2 ;;
    --wordlist) wordlist_path="$2"; shift 2 ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd python3

export_root="$(db_image_export_root "$db")"
corpus_dir="$export_root/recovered"
[[ -d "$corpus_dir" ]] || die "recovered corpus not found: $corpus_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/text-seed/$timestamp"
log_path="$export_root/logs/text-seed-scan-$timestamp.log"
hits_tsv="$out_dir/hits.tsv"
mkdir -p "$out_dir" "$(dirname "$log_path")"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: text seed scan only. Re-run with --run to write findings.\n'
  PYTHONPATH="$ROOT_DIR" python3 - "$db" "$corpus_dir" "$hits_tsv" "$min_words" "${wordlist_path:-}" "0" <<'PY'
from pathlib import Path
import sys

_, corpus_dir, *_ = sys.argv[1:]
exts = {".txt", ".html", ".htm", ".md", ".csv", ".rtf", ".json", ".log", ".ini", ".conf", ".yml", ".yaml"}
count = sum(1 for p in Path(corpus_dir).rglob("*") if p.is_file() and p.suffix.lower() in exts)
print(f"Candidate text files: {count}")
PY
  printf 'Output would be: %s\n' "$hits_tsv"
  exit 0
fi

run_id="$(record_scan_start "$db" "text-seed-scan" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
PYTHONPATH="$ROOT_DIR" python3 - "$db" "$corpus_dir" "$hits_tsv" "$min_words" "${wordlist_path:-}" "${TEXT_SEED_SCAN_MAX_BYTES:-10485760}" <<'PY'
import csv
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

from lib.seed_scan import load_wordlist, scan_text

db_path, corpus_dir, hits_tsv, min_words_raw, wordlist_arg, max_bytes_raw = sys.argv[1:7]
min_words = int(min_words_raw)
max_bytes = int(max_bytes_raw)
exts = {".txt", ".html", ".htm", ".md", ".csv", ".rtf", ".json", ".log", ".ini", ".conf", ".yml", ".yaml"}
bip39 = load_wordlist(wordlist_arg or None)
if not bip39:
    raise SystemExit("BIP39 wordlist not found")

conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def artifact_id_for(path: str) -> int | None:
    row = conn.execute("SELECT id FROM recovered_artifacts WHERE full_path = ? LIMIT 1", (path,)).fetchone()
    return row[0] if row else None

files = [
    p for p in Path(corpus_dir).rglob("*")
    if p.is_file() and p.suffix.lower() in exts and p.stat().st_size <= max_bytes
]
print(f"Scanning {len(files)} text-like files; max_bytes={max_bytes}")

hit_count = 0
high_count = 0
with open(hits_tsv, "w", newline="") as f:
    writer = csv.writer(f, delimiter="\t")
    writer.writerow(["score", "run_len", "artifact_id", "path", "sequence"])
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        matches = scan_text(text, min_words=min_words, wordlist=bip39)
        if not matches:
            continue
        art_id = artifact_id_for(str(path))
        for match in matches:
            score = 95 if match.run_len >= 12 else 70
            writer.writerow([score, match.run_len, art_id or "", str(path), match.sequence])
            conn.execute(
                """
                INSERT INTO findings(source_tool, category, artifact_id, path, key, value, score, notes, created_at)
                VALUES('text-seed-scan', 'seed_phrase', ?, ?, 'bip39_run', ?, ?, ?, ?)
                """,
                (art_id, str(path), match.sequence, score, f"run_len={match.run_len} min_words={min_words}", now),
            )
            hit_count += 1
            if match.run_len >= 12:
                high_count += 1
                conn.execute(
                    "INSERT INTO notes(created_at, note) VALUES(?, ?)",
                    (now, f"text-seed-scan: probable seed phrase (run={match.run_len}) in {path}: {match.sequence}"),
                )

conn.commit()
conn.close()
print(f"Done. files={len(files)} hits={hit_count} high_confidence={high_count}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="text seed scan failed or incomplete; check log"
}

if [[ -z "$notes" ]]; then
  hit_rows="$(($(wc -l < "$hits_tsv") - 1))"
  notes="seed hits=$hit_rows min_words=$min_words"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
