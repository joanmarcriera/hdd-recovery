#!/usr/bin/env bash
# Enrich recovered_artifacts with TrID top guesses. This never uses TrID --ae
# and never renames original carved files; all enrichment is stored in SQLite.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-enrich-trid.sh <db-path> [--limit <n>] [--force] [--run]

Default is dry-run. Pass --run to execute TrID and update SQLite.

Writes recovered_artifacts:
  trid_top_ext
  trid_top_score
  trid_top3_json

Also writes informational findings rows with source_tool='trid'.

Hard rule: this script never uses TrID --ae and never renames files.
EOF
}

db="${1:-}"
limit=""
force=0
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit) limit="$2"; shift 2 ;;
    --force) force=1; shift ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd python3

export_root="$(db_image_export_root "$db")"
prepare_work_dirs "$export_root" trid enrich-trid

candidate_count="$(sqlite3 -noheader "$db" "
SELECT COUNT(*)
FROM recovered_artifacts
WHERE full_path IS NOT NULL
  AND full_path <> ''
  AND ($force = 1 OR trid_top_ext IS NULL);
")"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: TrID enrichment only. Re-run with --run to execute.\n'
  printf 'Candidates: %s\n' "$candidate_count"
  printf 'Limit: %s\n' "${limit:-none}"
  printf 'Force: %s\n' "$force"
  printf 'No files will be renamed; TrID --ae is never used.\n'
  exit 0
fi

need_cmd trid

run_id="$(record_scan_start "$db" "enrich-trid" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

{
PYTHONPATH="$ROOT_DIR" python3 - "$db" "$out_dir" "$force" "${limit:-}" <<'PY'
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

db_path, out_dir, force_raw, limit_raw = sys.argv[1:5]
force = force_raw == "1"
limit = int(limit_raw) if limit_raw else None
conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

where = "WHERE full_path IS NOT NULL AND full_path <> ''"
if not force:
    where += " AND trid_top_ext IS NULL"
sql = f"SELECT id, full_path FROM recovered_artifacts {where} ORDER BY id"
if limit:
    sql += f" LIMIT {limit}"
rows = conn.execute(sql).fetchall()
print(f"TrID candidates: {len(rows)} force={force}")

hit_path = os.path.join(out_dir, "trid.tsv")
pattern = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)%\s+\(\.([^)]+)\)\s+(.+?)\s*$")

def parse_output(text: str) -> list[dict]:
    guesses = []
    for line in text.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        ext = m.group(2).strip().lstrip(".")
        guesses.append(
            {
                "score": float(m.group(1)),
                "ext": ext,
                "description": m.group(3).strip(),
            }
        )
        if len(guesses) == 3:
            break
    return guesses

with open(hit_path, "w", newline="") as f:
    writer = csv.writer(f, delimiter="\t")
    writer.writerow(["artifact_id", "top_ext", "top_score", "top3_json", "path"])
    for artifact_id, full_path in rows:
        if not os.path.isfile(full_path):
            continue
        result = subprocess.run(
            ["trid", full_path, "-n:3"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw_path = os.path.join(out_dir, f"{artifact_id}.txt")
        with open(raw_path, "w", errors="replace") as raw:
            raw.write(result.stdout)
            raw.write(result.stderr)
        guesses = parse_output(result.stdout + "\n" + result.stderr)
        if not guesses:
            continue
        top = guesses[0]
        top3_json = json.dumps(guesses, sort_keys=True)
        conn.execute(
            """
            UPDATE recovered_artifacts
            SET trid_top_ext = ?, trid_top_score = ?, trid_top3_json = ?
            WHERE id = ?
            """,
            (top["ext"], top["score"], top3_json, artifact_id),
        )
        conn.execute(
            """
            INSERT INTO findings(source_tool, category, artifact_id, path, key, value, score, created_at)
            VALUES('trid', 'enrichment', ?, ?, 'top3', ?, 0, ?)
            """,
            (artifact_id, full_path, top3_json, now),
        )
        writer.writerow([artifact_id, top["ext"], top["score"], top3_json, full_path])

conn.commit()
conn.close()
print(f"Results: {hit_path}")
PY
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="TrID enrichment failed or incomplete; check log"
}

if [[ -z "$notes" ]]; then
  enriched="$(sqlite3 -noheader "$db" "SELECT COUNT(*) FROM recovered_artifacts WHERE trid_top_ext IS NOT NULL;")"
  notes="trid-enriched artifacts=$enriched force=$force"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
