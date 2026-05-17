#!/usr/bin/env bash
# Crack recovered KeePass .kdbx candidates. KDBX 1-3 uses keepass2john plus
# hashcat -m 13400 with mandatory NVIDIA GPU. KDBX 4 / Argon2 only runs the
# slow keepass4brute CPU path when --allow-cpu-cracking is explicit.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/gpu_check.sh"

usage() {
  cat <<'EOF'
Usage:
  image-crack-keepass.sh <db> [--wordlist <path>] [--task-id <id>]
                              [--allow-cpu-cracking]
                              [--run]

Default is dry-run. Pass --run to execute cracking.

KDBX 1-3:
  keepass2john -> hashcat -m 13400 (requires NVIDIA GPU)

KDBX 4 / Argon2:
  keepass4brute.sh (CPU-only, slow; requires --allow-cpu-cracking)
EOF
}

find_keepass2john() {
  if command -v keepass2john >/dev/null 2>&1; then
    command -v keepass2john
  elif [[ -f /usr/share/john/keepass2john.py ]]; then
    printf '%s\n' /usr/share/john/keepass2john.py
  else
    return 1
  fi
}

db="${1:-}"
wordlist="$ROOT_DIR/config/wordlists/rockyou.txt"
task_id=""
allow_cpu=0
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wordlist) wordlist="$2"; shift 2 ;;
    --task-id) task_id="$2"; shift 2 ;;
    --allow-cpu-cracking) allow_cpu=1; shift ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
need_cmd python3
need_cmd sqlite3

export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/crack-keepass/$timestamp"
log_path="$export_root/logs/crack-keepass-$timestamp.log"
mkdir -p "$out_dir" "$(dirname "$log_path")" "$export_root/state/hashcat"

candidates_tsv="$out_dir/candidates.tsv"
python3 - "$db" "${task_id:-}" <<'PY' > "$candidates_tsv"
import os
import sqlite3
import struct
import sys

db_path, task_id = sys.argv[1:3]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

def kdbx_version(path: str) -> str:
    try:
        data = open(path, "rb").read(12)
    except OSError:
        return "unknown"
    if len(data) < 12:
        return "unknown"
    sig1, sig2, version = struct.unpack("<III", data)
    if sig2 != 0xB54BFB67:
        return "unknown"
    major = version >> 16
    if 1 <= major <= 3:
        return "kdbx3"
    if major >= 4:
        return "kdbx4"
    return "unknown"

if task_id:
    rows = conn.execute(
        """
        SELECT ct.id AS task_id, ra.id AS artifact_id, ra.full_path
        FROM crack_tasks ct
        JOIN recovered_artifacts ra ON ra.id = ct.target_artifact_id
        WHERE ct.id = ? AND ct.target_kind = 'kdbx'
        """,
        (task_id,),
    ).fetchall()
else:
    rows = conn.execute(
        """
        SELECT NULL AS task_id, id AS artifact_id, full_path
        FROM recovered_artifacts
        WHERE full_path IS NOT NULL
          AND (
            lower(COALESCE(mime_type,'')) LIKE '%keepass%'
            OR lower(relative_path) LIKE '%.kdbx'
            OR lower(full_path) LIKE '%.kdbx'
          )
          AND NOT EXISTS (
            SELECT 1 FROM crack_tasks ct
            WHERE ct.target_artifact_id = recovered_artifacts.id
              AND ct.target_kind = 'kdbx'
              AND ct.status = 'cracked'
          )
        ORDER BY id
        """
    ).fetchall()

for row in rows:
    path = row["full_path"] or ""
    print("|".join([str(row["task_id"] or ""), str(row["artifact_id"] or ""), path, kdbx_version(path)]))
conn.close()
PY
candidate_count="$(grep -c '.' "$candidates_tsv" 2>/dev/null || true)"
keepass2john_path="$(find_keepass2john || true)"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: KeePass cracking only. Re-run with --run to execute.\n'
  printf 'Candidates/tasks: %s\n' "$candidate_count"
  printf 'keepass2john: %s\n' "${keepass2john_path:-not found}"
  while IFS='|' read -r existing_task artifact_id kdbx_path version; do
    label="${existing_task:-new-$artifact_id}"
    printf '\nartifact_id=%s version=%s path=%s\n' "$artifact_id" "$version" "$kdbx_path"
    if [[ "$version" == "kdbx4" ]]; then
      printf '  keepass4brute.sh %q %q%s\n' "$kdbx_path" "$wordlist" "$([[ "$allow_cpu" -eq 1 ]] || printf '  # skipped unless --allow-cpu-cracking')"
    else
      printf '  %s %q > %q\n' "${keepass2john_path:-keepass2john}" "$kdbx_path" "$out_dir/$label.hash"
      printf '  hashcat -m 13400 --status --status-timer=60 %q %q\n' "$out_dir/$label.hash" "$wordlist"
    fi
  done < "$candidates_tsv"
  exit 0
fi

if [[ "$candidate_count" -eq 0 ]]; then
  run_id="$(record_scan_start "$db" "crack-keepass" "$0 $db" "$log_path" "$out_dir")"
  printf 'No KeePass .kdbx artifacts found.\n' | tee "$log_path"
  record_scan_end "$db" "$run_id" "ok" "no kdbx candidates"
  exit 0
fi

[[ -f "$wordlist" ]] || die "wordlist not found: $wordlist"
run_id="$(record_scan_start "$db" "crack-keepass" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""

extract_password() {
  local path="$1"
  python3 - "$path" <<'PY'
import re
import sys

text = open(sys.argv[1], "r", errors="replace").read()
for line in text.splitlines():
    if ":" in line and not line.startswith("Session."):
        print(line.rsplit(":", 1)[-1].strip())
        sys.exit(0)
m = re.search(r"(?im)(?:password|passphrase).*?(?:found|is)\s*:?\s*(.+)$", text)
if m:
    print(m.group(1).strip())
    sys.exit(0)
sys.exit(1)
PY
}

record_result() {
  local task="$1" artifact_id="$2" path="$3" tool="$4" password="$5"
  sqlite3 "$db" <<SQL
UPDATE crack_tasks
SET status='cracked',
    result_value='$(sql_escape "$password")',
    ended_at='$(timestamp_utc)'
WHERE id=$task;
INSERT INTO findings(source_tool, category, artifact_id, path, key, value, score, notes, created_at)
VALUES('$(sql_escape "$tool")','crack-result',$artifact_id,'$(sql_escape "$path")','keepass_password','$(sql_escape "$password")',95,'crack_tasks.id=$task','$(timestamp_utc)');
SQL
}

{
  while IFS='|' read -r existing_task artifact_id kdbx_path version; do
    [[ -f "$kdbx_path" ]] || {
      printf 'Skipping missing KDBX path: %s\n' "$kdbx_path"
      continue
    }

    if [[ "$version" == "kdbx4" ]]; then
      if [[ "$allow_cpu" -ne 1 ]]; then
        printf 'KDBX4/Argon2 candidate %s skipped; pass --allow-cpu-cracking to run keepass4brute.\n' "$kdbx_path"
        continue
      fi
      need_cmd keepass4brute.sh
      task="$(sqlite3 -noheader "$db" <<SQL
INSERT INTO crack_tasks(cracker,target_artifact_id,target_kind,hash_mode,wordlist_path,status,notes)
VALUES('keepass4brute',$artifact_id,'kdbx','argon2-keepass4brute','$(sql_escape "$wordlist")','queued','$(sql_escape "$kdbx_path")');
SELECT last_insert_rowid();
SQL
)"
      output="$out_dir/$task.keepass4brute.txt"
      sqlite3 "$db" "UPDATE crack_tasks SET status='running', started_at='$(timestamp_utc)' WHERE id=$task;"
      keepass4brute.sh "$kdbx_path" "$wordlist" > "$output" 2>&1 || true
      cat "$output"
      password="$(extract_password "$output" || true)"
      if [[ -n "$password" ]]; then
        record_result "$task" "$artifact_id" "$kdbx_path" "keepass4brute" "$password"
      else
        sqlite3 "$db" "UPDATE crack_tasks SET status='exhausted', ended_at='$(timestamp_utc)' WHERE id=$task;"
      fi
      continue
    fi

    [[ -n "$keepass2john_path" ]] || die "keepass2john not found"
    require_nvidia_gpu >/dev/null
    need_cmd hashcat
    task="$(sqlite3 -noheader "$db" <<SQL
INSERT INTO crack_tasks(cracker,target_artifact_id,target_kind,hash_mode,wordlist_path,status,notes)
VALUES('hashcat',$artifact_id,'kdbx','13400','$(sql_escape "$wordlist")','queued','$(sql_escape "$kdbx_path")');
SELECT last_insert_rowid();
SQL
)"
    hash_file="$out_dir/$task.hash"
    show_file="$out_dir/$task.show.txt"
    checkpoint="$export_root/state/hashcat/keepass-$task/restore"
    mkdir -p "$(dirname "$checkpoint")"
    "$keepass2john_path" "$kdbx_path" > "$hash_file"
    sqlite3 "$db" "UPDATE crack_tasks SET status='running', started_at='$(timestamp_utc)', checkpoint_path='$(sql_escape "$checkpoint")' WHERE id=$task;"
    rc=0
    hashcat -m 13400 --status --status-timer=60 --restore-file-path="$checkpoint" "$hash_file" "$wordlist" || rc=$?
    hashcat -m 13400 --show "$hash_file" > "$show_file" 2>/dev/null || true
    password="$(extract_password "$show_file" || true)"
    if [[ -n "$password" ]]; then
      record_result "$task" "$artifact_id" "$kdbx_path" "hashcat" "$password"
    elif [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
      sqlite3 "$db" "UPDATE crack_tasks SET status='exhausted', ended_at='$(timestamp_utc)' WHERE id=$task;"
    else
      sqlite3 "$db" "UPDATE crack_tasks SET status='failed', ended_at='$(timestamp_utc)', notes=COALESCE(notes,'') || char(10) || 'hashcat rc=$rc' WHERE id=$task;"
      status="partial"
    fi
  done < "$candidates_tsv"
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="KeePass cracking failed or incomplete; check log"
}

if [[ -z "$notes" ]]; then
  notes="processed $candidate_count KeePass candidate(s)"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
