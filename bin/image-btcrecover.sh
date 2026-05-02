#!/usr/bin/env bash
# Operator-driven btcrecover wrapper for partial seeds and partial passwords.
# The operator supplies the btcrecover command in a YAML config; this script
# handles dry-run, crack_tasks state, checkpoint path recording, and DB import.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/gpu_check.sh"

usage() {
  cat <<'EOF'
Usage:
  image-btcrecover.sh <db> --config <yml-path> [--gpu] [--run]
  image-btcrecover.sh <db> --resume <task-id> [--run]

Default is dry-run. Pass --run to execute.

Config file:
  target_type: seed | password
  task_name: short-name
  command: "btcrecover.py ... OR seedrecover.py ..."
  checkpoint_path: optional explicit checkpoint path

Docs:
  https://github.com/3rdIteration/btcrecover

Outputs:
  <export_root>/logs/btcrecover-<timestamp>.log
  <export_root>/hits/btcrecover/<timestamp>/task-<id>.log
  <export_root>/state/btcrecover/<task-name>/
EOF
}

db="${1:-}"
config_path=""
resume_task=""
use_gpu=0
run=0
if [[ "${db:-}" == "-h" || "${db:-}" == "--help" ]]; then
  usage
  exit 0
fi
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config_path="$2"; shift 2 ;;
    --resume) resume_task="$2"; shift 2 ;;
    --gpu) use_gpu=1; shift ;;
    --run) run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"
if [[ -n "$config_path" && -n "$resume_task" ]]; then
  die "use either --config or --resume, not both"
fi
if [[ -z "$config_path" && -z "$resume_task" ]]; then
  usage
  exit 1
fi
need_cmd python3
need_cmd sqlite3

export_root="$(db_image_export_root "$db")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="$export_root/hits/btcrecover/$timestamp"
log_path="$export_root/logs/btcrecover-$timestamp.log"
state_root="$export_root/state/btcrecover"
mkdir -p "$out_dir" "$(dirname "$log_path")" "$state_root"

parsed="$(
python3 - "$db" "$export_root" "${config_path:-}" "${resume_task:-}" <<'PY'
import os
import re
import sqlite3
import sys

db_path, export_root, config_path, resume_task = sys.argv[1:5]

def scalar(text: str, key: str) -> str:
    m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text, re.M)
    if not m:
        return ""
    value = m.group(1).strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return value

if resume_task:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT target_kind, checkpoint_path, notes FROM crack_tasks WHERE id=? AND cracker='btcrecover'",
        (resume_task,),
    ).fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"btcrecover task not found: {resume_task}")
    target_kind, checkpoint, notes = row
    command = ""
    config = ""
    for line in (notes or "").splitlines():
        if line.startswith("command="):
            command = line.split("=", 1)[1]
        elif line.startswith("config="):
            config = line.split("=", 1)[1]
    task_name = f"task-{resume_task}"
else:
    if not os.path.isfile(config_path):
        raise SystemExit(f"config file not found: {config_path}")
    text = open(config_path, "r", errors="replace").read()
    target_type = scalar(text, "target_type").lower()
    if target_type == "seed":
        target_kind = "seed-partial"
    elif target_type == "password":
        target_kind = "password-partial"
    else:
        raise SystemExit("config target_type must be seed or password")
    task_name = scalar(text, "task_name") or os.path.splitext(os.path.basename(config_path))[0]
    command = scalar(text, "command")
    if not command:
        raise SystemExit("config must include command: \"btcrecover.py ...\"")
    checkpoint = scalar(text, "checkpoint_path")
    if not checkpoint:
        checkpoint = os.path.join(export_root, "state", "btcrecover", task_name, "checkpoint")
    config = config_path

print("\t".join([target_kind, task_name, checkpoint or "", command, config]))
PY
)"
IFS=$'\t' read -r target_kind task_name checkpoint_path command_line resolved_config <<< "$parsed"
[[ -n "$command_line" ]] || die "btcrecover command could not be resolved"
task_state_dir="$state_root/$task_name"
mkdir -p "$task_state_dir"

if [[ "$run" -ne 1 ]]; then
  printf 'DRY RUN: btcrecover only. Re-run with --run to execute.\n'
  printf 'target_kind: %s\n' "$target_kind"
  printf 'task_name: %s\n' "$task_name"
  printf 'checkpoint_path: %s\n' "$checkpoint_path"
  printf 'command: %s\n' "$command_line"
  if [[ "$use_gpu" -eq 1 ]]; then
    if ( require_nvidia_gpu ); then
      :
    else
      printf 'GPU detection result: no usable NVIDIA GPU detected.\n'
    fi
  fi
  exit 0
fi

if [[ "$use_gpu" -eq 1 ]]; then
  require_nvidia_gpu >/dev/null
fi

run_id="$(record_scan_start "$db" "btcrecover" "$0 $db" "$log_path" "$out_dir")"
status="ok"
notes=""
task_id="$resume_task"

if [[ -z "$task_id" ]]; then
  task_id="$(sqlite3 -noheader "$db" <<SQL
INSERT INTO crack_tasks(cracker,target_kind,checkpoint_path,status,notes)
VALUES('btcrecover','$(sql_escape "$target_kind")','$(sql_escape "$checkpoint_path")','queued','config=$(sql_escape "$resolved_config")
command=$(sql_escape "$command_line")');
SELECT last_insert_rowid();
SQL
)"
fi

pause_task() {
  sqlite3 "$db" <<SQL
UPDATE crack_tasks
SET status='paused',
    paused_at='$(timestamp_utc)',
    checkpoint_path='$(sql_escape "$checkpoint_path")',
    notes=COALESCE(notes,'') || char(10) || 'paused by signal'
WHERE id=$task_id;
SQL
  exit 130
}
trap pause_task INT TERM

task_log="$out_dir/task-$task_id.log"
{
  sqlite3 "$db" <<SQL
UPDATE crack_tasks
SET status='running',
    started_at=COALESCE(started_at, '$(timestamp_utc)'),
    checkpoint_path='$(sql_escape "$checkpoint_path")'
WHERE id=$task_id;
SQL

  printf 'Running btcrecover task %s\n' "$task_id"
  printf 'Command: %s\n' "$command_line"
  rc=0
  bash -lc "$command_line" > "$task_log" 2>&1 || rc=$?
  cat "$task_log"

  result="$(
    python3 - "$task_log" <<'PY' || true
import re
import sys

text = open(sys.argv[1], "r", errors="replace").read()
patterns = [
    r"(?im)^\s*(?:password|passphrase|seed|mnemonic)\s+(?:found|found:)\s*:?\s*(.+?)\s*$",
    r"(?im)^\s*found\s*:?\s*(.+?)\s*$",
]
for pattern in patterns:
    m = re.search(pattern, text)
    if m:
        print(m.group(1).strip())
        sys.exit(0)
sys.exit(1)
PY
  )"

  if [[ -n "$result" ]]; then
    sqlite3 "$db" <<SQL
UPDATE crack_tasks
SET status='cracked',
    result_value='$(sql_escape "$result")',
    ended_at='$(timestamp_utc)'
WHERE id=$task_id;
SQL
    if [[ "$target_kind" == "seed-partial" ]]; then
      sqlite3 "$db" <<SQL
INSERT INTO wallet_keys(source_method,key_type,key_value,encrypted,notes,created_at)
VALUES('btcrecover','bip39_seed','$(sql_escape "$result")',0,'crack_tasks.id=$task_id','$(timestamp_utc)');
SQL
    fi
    printf 'btcrecover result found for task %s\n' "$task_id"
  elif [[ "$rc" -eq 0 ]]; then
    sqlite3 "$db" "UPDATE crack_tasks SET status='exhausted', ended_at='$(timestamp_utc)' WHERE id=$task_id;"
    printf 'btcrecover completed without a parsed result for task %s\n' "$task_id"
  else
    sqlite3 "$db" "UPDATE crack_tasks SET status='failed', ended_at='$(timestamp_utc)', notes=COALESCE(notes,'') || char(10) || 'btcrecover rc=$rc' WHERE id=$task_id;"
    status="partial"
    printf 'btcrecover failed rc=%s for task %s\n' "$rc" "$task_id"
  fi
} 2>&1 | tee "$log_path" || {
  status="partial"
  notes="btcrecover failed or completed with errors"
}

if [[ -z "$notes" ]]; then
  notes="btcrecover task_id=$task_id"
fi
record_scan_end "$db" "$run_id" "$status" "$notes"
