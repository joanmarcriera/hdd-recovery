#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
CONFIG_FILE="${HDD_RECOVERY_CONFIG:-$ROOT_DIR/config/analysis-pipeline.env}"
SCHEMA_FILE="$ROOT_DIR/sql/analysis-schema.sql"

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

timestamp_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  printf '[%s] %s\n' "$(timestamp_utc)" "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

abs_path() {
  readlink -f "$1"
}

image_name() {
  basename "$1"
}

image_basename() {
  local name
  name="$(basename "$1")"
  printf '%s\n' "${name%.*}"
}

default_db_path() {
  local image="$1"
  local suffix="${DB_SUFFIX:-.analysis.sqlite}"
  if [[ -n "${DB_ROOT:-}" ]]; then
    printf '%s/%s%s\n' "$DB_ROOT" "$(image_name "$image")" "$suffix"
  else
    printf '%s%s\n' "$image" "$suffix"
  fi
}

default_export_root() {
  local image="$1"
  local base
  base="$(image_basename "$image")"
  printf '%s/%s\n' "${EXPORT_ROOT:-/data/exports}" "$base"
}

ensure_parent_dir() {
  mkdir -p "$(dirname "$1")"
}

ensure_image_file() {
  [[ -n "${1:-}" ]] || die "image path is required"
  [[ -f "$1" ]] || die "image file not found: $1"
}

assert_storage_safe() {
  # Refuse to write into the container's writable overlay layer. Unmounted data
  # roots (the /data/* defaults) silently land there — on TrueNAS that fills the
  # FastPool SSD and is discarded on the next container recreate. Enforced only
  # inside the container; override with HDD_ALLOW_OVERLAY=1. See lib/storage_guard.py.
  [[ "${_HDD_STORAGE_CHECKED:-}" == "1" ]] && return 0
  [[ "${HDD_ALLOW_OVERLAY:-}" == "1" ]] && { _HDD_STORAGE_CHECKED=1; return 0; }
  if [[ "${HDD_IN_CONTAINER:-}" != "1" && ! -e /.dockerenv ]]; then
    _HDD_STORAGE_CHECKED=1
    return 0
  fi
  command -v python3 >/dev/null 2>&1 || return 0
  export IMAGE_ROOT EXPORT_ROOT LOG_ROOT DB_ROOT
  local bad
  bad="$(PYTHONPATH="$ROOT_DIR" python3 - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from lib.storage_guard import check_environment, dangerous_roots
print(",".join(f"{r.name}={r.path}" for r in dangerous_roots(check_environment())))
PY
)"
  if [[ -n "$bad" ]]; then
    die "data roots resolve to the container overlay (data would be lost on recreate): $bad
Bind-mount them to a dataset, or set the env var under an already-mounted path.
Run bin/storage-check.sh for details, or set HDD_ALLOW_OVERLAY=1 to override."
  fi
  _HDD_STORAGE_CHECKED=1
}

ensure_db() {
  [[ -n "${1:-}" ]] || die "database path is required"
  assert_storage_safe
  ensure_parent_dir "$1"
  sqlite3 "$1" < "$SCHEMA_FILE" >/dev/null
  apply_schema_migrations "$1"
}

apply_schema_migrations() {
  local db="$1"
  python3 - "$db" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
migrations = [
    ("recovered_artifacts", "trid_top_ext", "TEXT"),
    ("recovered_artifacts", "trid_top_score", "REAL"),
    ("recovered_artifacts", "trid_top3_json", "TEXT"),
    ("recovered_artifacts", "dedup_cluster_id", "INTEGER"),
    ("recovered_artifacts", "is_cluster_primary", "INTEGER DEFAULT 0"),
    ("recovered_artifacts", "quality_score", "REAL"),
    # scan_runs supervision columns (lib/runs.py reconciliation)
    ("scan_runs", "pid", "INTEGER"),
    ("scan_runs", "pgid", "INTEGER"),
    ("scan_runs", "host", "TEXT"),
    ("scan_runs", "heartbeat_at", "TEXT"),
    ("scan_runs", "last_progress_at", "TEXT"),
    ("scan_runs", "cancel_requested", "INTEGER DEFAULT 0"),
]

conn = sqlite3.connect(db_path)
for table, column, definition in migrations:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
conn.commit()
conn.close()
PY
}

db_value() {
  local db="$1"
  local sql="$2"
  sqlite3 -noheader -batch "$db" "$sql"
}

sql_escape() {
  printf "%s" "$1" | sed "s/'/''/g"
}

run_sql() {
  local db="$1"
  shift
  sqlite3 "$db" "$@"
}

record_scan_start() {
  local db="$1" stage="$2" cmdline="$3" log_path="$4" output_dir="$5"
  # Make sure the supervision columns exist on older DBs before inserting them.
  apply_schema_migrations "$db"
  local started_at pid pgid host
  started_at="$(timestamp_utc)"
  # $$ is this script's PID (the long-lived process the runner would kill);
  # under start_new_session it is also the process-group leader.
  pid="$$"
  pgid="$(ps -o pgid= -p "$$" 2>/dev/null | tr -d ' ')"
  [[ "$pgid" =~ ^[0-9]+$ ]] || pgid="NULL"
  host="$(hostname 2>/dev/null || printf '')"
  local sql
  sql=$(cat <<EOF
INSERT INTO scan_runs(stage,status,started_at,command_line,log_path,output_dir,pid,pgid,host,heartbeat_at)
VALUES('$(sql_escape "$stage")','running','$(sql_escape "$started_at")','$(sql_escape "$cmdline")','$(sql_escape "$log_path")','$(sql_escape "$output_dir")',$pid,$pgid,'$(sql_escape "$host")','$(sql_escape "$started_at")');
SELECT last_insert_rowid();
EOF
)
  sqlite3 -batch "$db" "$sql"
}

record_scan_end() {
  local db="$1" run_id="$2" status="$3" notes="${4:-}"
  sqlite3 "$db" <<EOF
UPDATE scan_runs
SET status='$(sql_escape "$status")',
    ended_at='$(timestamp_utc)',
    notes='$(sql_escape "$notes")'
WHERE id=$run_id;
EOF
}

ensure_work_dirs() {
  local export_root="$1"
  assert_storage_safe
  mkdir -p \
    "$export_root/logs" \
    "$export_root/reports" \
    "$export_root/state" \
    "$export_root/structure" \
    "$export_root/recovered" \
    "$export_root/indexes" \
    "$export_root/hits" \
    "$export_root/exports"
}

# prepare_work_dirs <export_root> <hits_subdir> <log_stage> [extra_dirs...]
#
# Derive and create the standard per-stage work directories, setting the globals
# the caller then uses. <hits_subdir> and <log_stage> are separate because some
# stages name them differently (e.g. hits/trid + logs/enrich-trid-*). Timestamps
# use the compact, filename-safe form (no colons), distinct from timestamp_utc.
#   timestamp -> <YYYYMMDDTHHMMSSZ>
#   out_dir   -> <export_root>/hits/<hits_subdir>/<timestamp>
#   log_path  -> <export_root>/logs/<log_stage>-<timestamp>.log
prepare_work_dirs() {
  local export_root="$1" hits_subdir="$2" log_stage="$3"
  shift 3
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  out_dir="$export_root/hits/$hits_subdir/$timestamp"
  log_path="$export_root/logs/$log_stage-$timestamp.log"
  mkdir -p "$out_dir" "$(dirname "$log_path")" "$@"
}

# rotate_with_backup <path>
#
# If <path> exists (file or directory), move it aside to <path>.prev-<timestamp>
# and log the rotation; a missing path is a silent no-op. Preserves prior output
# additively per the evidence-preservation rules (never overwrite in place).
rotate_with_backup() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  local backup="${path}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
  log "Preserving previous $(basename "$path") at $backup"
  mv "$path" "$backup"
}

db_image_export_root() {
  db_value "$1" "SELECT export_root FROM image_info WHERE id=1;"
}

db_image_path() {
  db_value "$1" "SELECT image_path FROM image_info WHERE id=1;"
}

db_image_basename() {
  db_value "$1" "SELECT image_basename FROM image_info WHERE id=1;"
}

with_log() {
  local log_path="$1"
  shift
  ensure_parent_dir "$log_path"
  (
    set -o pipefail
    "$@" 2>&1 | tee "$log_path"
  )
}

register_artifacts_from_dir() {
  local db="$1" method="$2" root_dir="$3" run_id="$4"
  [[ -d "$root_dir" ]] || return 0
  log "Registering recovered artifacts for method=$method from $root_dir"
  python3 - "$db" "$method" "$root_dir" "$run_id" <<'PY'
import hashlib
import mimetypes
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

db_path, method, root_dir, run_id = sys.argv[1:]
conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

for dirpath, _, filenames in os.walk(root_dir):
    for name in filenames:
      full = os.path.join(dirpath, name)
      rel = os.path.relpath(full, root_dir)
      try:
          size = os.path.getsize(full)
      except OSError:
          size = None
      mime = mimetypes.guess_type(full)[0]
      file_output = ""
      try:
          file_output = subprocess.check_output(["file", "-b", full], text=True).strip()
      except Exception:
          file_output = ""
      digest = sha256(full)
      conn.execute(
          """
          INSERT INTO recovered_artifacts(method, relative_path, full_path, size_bytes, sha256, mime_type, file_output, source_run_id, created_at)
          VALUES(?,?,?,?,?,?,?,?,?)
          ON CONFLICT(method, relative_path) DO UPDATE SET
            full_path=excluded.full_path,
            size_bytes=excluded.size_bytes,
            sha256=excluded.sha256,
            mime_type=excluded.mime_type,
            file_output=excluded.file_output,
            source_run_id=excluded.source_run_id
          """,
          (method, rel, full, size, digest, mime, file_output, int(run_id), now),
      )

conn.commit()
print(f"registered artifacts for method={method}")
conn.close()
PY
}
