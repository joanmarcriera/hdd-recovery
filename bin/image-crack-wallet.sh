#!/usr/bin/env bash
# Crack encrypted Bitcoin Core wallet.dat candidates recorded by pywallet.
# Default mode is dry-run. GPU cracking hard-fails unless an NVIDIA GPU is
# visible; john CPU fallback only runs when --cpu-fallback is explicit.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/gpu_check.sh"

usage() {
  cat <<'EOF'
Usage:
  image-crack-wallet.sh <db> [--wordlist <path>] [--rules <path>]
                             [--gpu | --cpu-fallback]
                             [--task-id <id>]
                             [--run]

Default is dry-run. Pass --run to execute cracking.

Behavior:
  - finds encrypted pywallet wallet_keys rows
  - runs bitcoin2john.py to create a hash
  - runs hashcat -m 11300 when an NVIDIA GPU is present
  - refuses to run without GPU unless --cpu-fallback is explicit
  - tracks queued/running/paused/cracked/exhausted/failed in crack_tasks

Outputs:
  <export_root>/logs/crack-wallet-<timestamp>.log
  <export_root>/hits/crack-wallet/<timestamp>/<wallet-id>.hash
  <export_root>/state/hashcat/<task-id>/
EOF
}

find_bitcoin2john() {
  if command -v bitcoin2john.py >/dev/null 2>&1; then
    command -v bitcoin2john.py
  elif [[ -f /usr/share/john/bitcoin2john.py ]]; then
    printf '%s\n' /usr/share/john/bitcoin2john.py
  else
    return 1
  fi
}

db="${1:-}"
wordlist="$ROOT_DIR/config/wordlists/rockyou.txt"
rules_path=""
cpu_fallback=0
task_id=""
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wordlist) wordlist="$2"; shift 2 ;;
    --rules) rules_path="$2"; shift 2 ;;
    --gpu) cpu_fallback=0; shift ;;
    --cpu-fallback) cpu_fallback=1; shift ;;
    --task-id) task_id="$2"; shift 2 ;;
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
out_dir="$export_root/hits/crack-wallet/$timestamp"
log_path="$export_root/logs/crack-wallet-$timestamp.log"
mkdir -p "$out_dir" "$(dirname "$log_path")" "$export_root/state/hashcat"

candidates_tsv="$out_dir/candidates.tsv"
python3 - "$db" "${task_id:-}" <<'PY' > "$candidates_tsv"
import sqlite3
import sys

db_path, task_id = sys.argv[1:3]
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

if task_id:
    rows = conn.execute(
        """
        SELECT ct.id AS task_id, NULL AS wallet_key_id, ct.target_artifact_id,
               COALESCE(ra.full_path, '') AS wallet_path, ct.status, 'task' AS source
        FROM crack_tasks ct
        LEFT JOIN recovered_artifacts ra ON ra.id = ct.target_artifact_id
        WHERE ct.id = ? AND ct.target_kind = 'wallet.dat'
        """,
        (task_id,),
    ).fetchall()
else:
    rows = conn.execute(
        """
        SELECT ct.id AS task_id, NULL AS wallet_key_id, ct.target_artifact_id,
               COALESCE(ra.full_path, '') AS wallet_path, ct.status, 'paused' AS source
        FROM crack_tasks ct
        LEFT JOIN recovered_artifacts ra ON ra.id = ct.target_artifact_id
        WHERE ct.target_kind = 'wallet.dat'
          AND ct.status = 'paused'
        ORDER BY ct.id
        """
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT NULL AS task_id, wk.id AS wallet_key_id, wk.source_artifact_id AS target_artifact_id,
                   COALESCE(ra.full_path, wk.key_value) AS wallet_path, 'new' AS status, 'wallet_key' AS source
            FROM wallet_keys wk
            LEFT JOIN recovered_artifacts ra ON ra.id = wk.source_artifact_id
            WHERE wk.encrypted = 1
              AND wk.source_method = 'pywallet'
              AND NOT EXISTS (
                SELECT 1
                FROM crack_tasks ct
                WHERE ct.status = 'cracked'
                  AND (
                    ct.target_artifact_id = wk.source_artifact_id
                    OR (ct.target_artifact_id IS NULL AND wk.source_artifact_id IS NULL AND ct.notes LIKE '%' || wk.key_value || '%')
                  )
              )
            ORDER BY wk.id
            """
        ).fetchall()

for row in rows:
    print(
        "|".join(
            str(row[key] or "")
            for key in ("task_id", "wallet_key_id", "target_artifact_id", "wallet_path", "status", "source")
        )
    )
conn.close()
PY

candidate_count="$(grep -c '.' "$candidates_tsv" 2>/dev/null || true)"
bitcoin2john_path="$(find_bitcoin2john || true)"

print_gpu_status() {
  if ( require_nvidia_gpu ); then
    return 0
  fi
  printf 'GPU detection result: no usable NVIDIA GPU detected.\n'
  return 1
}

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: wallet cracking only. Re-run with --run to execute.\n'
  printf 'Candidates/tasks: %s\n' "$candidate_count"
  printf 'bitcoin2john: %s\n' "${bitcoin2john_path:-not found}"
  print_gpu_status || true
  while IFS='|' read -r existing_task wallet_key_id artifact_id wallet_path status source; do
    label="${existing_task:-new-$wallet_key_id}"
    hash_file="$out_dir/$label.hash"
    checkpoint_dir="$export_root/state/hashcat/${existing_task:-TASK_ID}"
    printf '\nwallet=%s source=%s status=%s artifact_id=%s\n' "$wallet_path" "$source" "$status" "${artifact_id:-NULL}"
    printf '  python3 %q %q > %q\n' "${bitcoin2john_path:-/usr/share/john/bitcoin2john.py}" "$wallet_path" "$hash_file"
    if [[ "$cpu_fallback" -eq 1 ]]; then
      printf '  john --format=bitcoin --wordlist=%q --pot=%q %q\n' "$wordlist" "$out_dir/$label.john.pot" "$hash_file"
    else
      printf '  hashcat -m 11300 --status --status-timer=60 --restore-file-path=%q %q %q\n' "$checkpoint_dir/restore" "$hash_file" "$wordlist"
    fi
  done < "$candidates_tsv"
  exit 0
fi

[[ -n "$bitcoin2john_path" ]] || die "bitcoin2john.py not found"

if [[ "$candidate_count" -eq 0 ]]; then
  run_id="$(record_scan_start "$db" "crack-wallet" "$0 $db" "$log_path" "$out_dir")"
  printf 'No encrypted pywallet wallet_keys rows found.\n' | tee "$log_path"
  record_scan_end "$db" "$run_id" "ok" "no encrypted wallets"
  exit 0
fi

[[ -f "$wordlist" ]] || die "wordlist not found: $wordlist"
if [[ -n "$rules_path" ]]; then
  [[ -f "$rules_path" ]] || die "rules file not found: $rules_path"
fi
if [[ "$cpu_fallback" -eq 1 ]]; then
  need_cmd john
else
  require_nvidia_gpu >/dev/null
  need_cmd hashcat
fi

run_id="$(record_scan_start "$db" "crack-wallet" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""
current_task_id=""
current_checkpoint=""

pause_current() {
  if [[ -n "$current_task_id" ]]; then
    sqlite3 "$db" <<SQL
UPDATE crack_tasks
SET status='paused',
    paused_at='$(timestamp_utc)',
    checkpoint_path='$(sql_escape "$current_checkpoint")',
    notes=COALESCE(notes,'') || char(10) || 'paused by signal'
WHERE id=$current_task_id;
SQL
  fi
  exit 130
}
trap pause_current INT TERM

update_task_status() {
  local id="$1" task_status="$2" result="${3:-}" task_notes="${4:-}"
  sqlite3 "$db" <<SQL
UPDATE crack_tasks
SET status='$(sql_escape "$task_status")',
    result_value=CASE WHEN '$(sql_escape "$result")' = '' THEN result_value ELSE '$(sql_escape "$result")' END,
    ended_at=CASE WHEN '$(sql_escape "$task_status")' IN ('cracked','exhausted','failed') THEN '$(timestamp_utc)' ELSE ended_at END,
    notes=CASE WHEN '$(sql_escape "$task_notes")' = '' THEN notes ELSE COALESCE(notes,'') || char(10) || '$(sql_escape "$task_notes")' END
WHERE id=$id;
SQL
}

extract_password() {
  local show_file="$1"
  python3 - "$show_file" <<'PY'
import sys

path = sys.argv[1]
try:
    lines = [line.strip() for line in open(path, "r", errors="replace") if line.strip()]
except FileNotFoundError:
    lines = []
for line in lines:
    if ":" in line:
        print(line.rsplit(":", 1)[-1])
        sys.exit(0)
sys.exit(1)
PY
}

import_cracked_password() {
  local task="$1" artifact_id="$2" wallet_path="$3" password="$4" dump_path="$5"
  python3 - "$db" "$task" "$artifact_id" "$wallet_path" "$password" "$dump_path" <<'PY'
import re
import sqlite3
import sys
from datetime import datetime, timezone

db_path, task_id, artifact_raw, wallet_path, password, dump_path = sys.argv[1:7]
artifact_id = int(artifact_raw) if artifact_raw else None
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
conn = sqlite3.connect(db_path)
conn.execute(
    """
    INSERT INTO findings(source_tool, category, artifact_id, path, key, value, score, notes, created_at)
    VALUES('hashcat', 'crack-result', ?, ?, 'wallet_password', ?, 95, ?, ?)
    """,
    (artifact_id, wallet_path, password, f"crack_tasks.id={task_id}", now),
)

try:
    text = open(dump_path, "r", errors="replace").read()
except FileNotFoundError:
    text = ""
wifs = sorted(set(re.findall(r"\b[5KL][1-9A-HJ-NP-Za-km-z]{50,51}\b", text)))
for key in wifs:
    conn.execute(
        """
        INSERT INTO wallet_keys(source_artifact_id, source_method, key_type, key_value, encrypted, decrypt_passphrase, notes, created_at)
        VALUES(?, 'pywallet+crack', 'wif', ?, 0, ?, ?, ?)
        """,
        (artifact_id, key, password, wallet_path, now),
    )
conn.commit()
conn.close()
PY
}

{
  if [[ "$candidate_count" -eq 0 ]]; then
    printf 'No encrypted pywallet wallet_keys rows found.\n'
    notes="no encrypted wallets"
  fi

  while IFS='|' read -r existing_task wallet_key_id artifact_id wallet_path row_status source; do
    [[ -n "$wallet_path" && -f "$wallet_path" ]] || {
      printf 'Skipping missing wallet path: %s\n' "$wallet_path"
      continue
    }

    if [[ -n "$existing_task" ]]; then
      task="$existing_task"
    else
      cracker="hashcat"
      hash_mode="11300"
      if [[ "$cpu_fallback" -eq 1 ]]; then
        cracker="john"
        hash_mode="bitcoin"
      fi
      task="$(sqlite3 -noheader "$db" <<SQL
INSERT INTO crack_tasks(cracker,target_artifact_id,target_kind,hash_mode,wordlist_path,rules_path,status,notes)
VALUES('$(sql_escape "$cracker")',$( [[ -n "$artifact_id" ]] && printf '%s' "$artifact_id" || printf 'NULL' ),'wallet.dat','$(sql_escape "$hash_mode")','$(sql_escape "$wordlist")','$(sql_escape "$rules_path")','queued','$(sql_escape "$wallet_path")');
SELECT last_insert_rowid();
SQL
)"
    fi

    current_task_id="$task"
    checkpoint_dir="$export_root/state/hashcat/$task"
    current_checkpoint="$checkpoint_dir/restore"
    mkdir -p "$checkpoint_dir"
    hash_file="$out_dir/$task.hash"
    show_file="$out_dir/$task.show.txt"
    dump_after_crack="$out_dir/$task-decrypted-dump.txt"

    printf 'Task %s: bitcoin2john %s\n' "$task" "$wallet_path"
    python3 "$bitcoin2john_path" "$wallet_path" > "$hash_file"
    sqlite3 "$db" <<SQL
UPDATE crack_tasks
SET status='running',
    started_at=COALESCE(started_at, '$(timestamp_utc)'),
    checkpoint_path='$(sql_escape "$current_checkpoint")'
WHERE id=$task;
SQL

    crack_rc=0
    if [[ "$cpu_fallback" -eq 1 ]]; then
      pot_file="$out_dir/$task.john.pot"
      john --format=bitcoin --wordlist="$wordlist" --pot="$pot_file" "$hash_file" || crack_rc=$?
      john --show --format=bitcoin --pot="$pot_file" "$hash_file" > "$show_file" 2>/dev/null || true
    else
      hashcat_args=(-m 11300 --status --status-timer=60 --restore-file-path="$current_checkpoint" "$hash_file" "$wordlist")
      if [[ -n "$rules_path" ]]; then
        hashcat_args+=(-r "$rules_path")
      fi
      hashcat "${hashcat_args[@]}" || crack_rc=$?
      hashcat -m 11300 --show "$hash_file" > "$show_file" 2>/dev/null || true
    fi

    password="$(extract_password "$show_file" || true)"
    if [[ -n "$password" ]]; then
      update_task_status "$task" "cracked" "$password" "password recovered"
      if command -v pywallet >/dev/null 2>&1; then
        pywallet --dumpwallet --wallet "$wallet_path" --passphrase "$password" > "$dump_after_crack" 2>&1 || true
      fi
      import_cracked_password "$task" "$artifact_id" "$wallet_path" "$password" "$dump_after_crack"
      printf 'Task %s cracked.\n' "$task"
    elif [[ "$crack_rc" -eq 0 || "$crack_rc" -eq 1 ]]; then
      update_task_status "$task" "exhausted" "" "wordlist exhausted without crack"
      printf 'Task %s exhausted.\n' "$task"
    else
      update_task_status "$task" "failed" "" "cracker exited rc=$crack_rc"
      status="partial"
      printf 'Task %s failed rc=%s.\n' "$task" "$crack_rc"
    fi
    current_task_id=""
  done < "$candidates_tsv"
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="crack-wallet failed or completed with errors"
}

if [[ -z "$notes" ]]; then
  notes="processed $candidate_count encrypted wallet task(s)"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
