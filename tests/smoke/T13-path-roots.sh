#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

CONFIG="$WORK_DIR/analysis.env"
mkdir -p "$WORK_DIR/images" "$WORK_DIR/db" "$WORK_DIR/exports" "$WORK_DIR/logs"
touch "$WORK_DIR/images/sample.img"

cat >"$CONFIG" <<EOF
IMAGE_ROOT="$WORK_DIR/images"
DB_ROOT="$WORK_DIR/db"
EXPORT_ROOT="$WORK_DIR/exports"
LOG_ROOT="$WORK_DIR/logs"
DB_SUFFIX=".analysis.sqlite"
EOF

HDD_RECOVERY_ROOT="$ROOT_DIR"
HDD_RECOVERY_CONFIG="$CONFIG"
export HDD_RECOVERY_ROOT HDD_RECOVERY_CONFIG

# shellcheck disable=SC1090
source "$ROOT_DIR/lib/common.sh"

expected_db="$WORK_DIR/db/sample.img.analysis.sqlite"
actual_db="$(default_db_path "$WORK_DIR/images/sample.img")"
[[ "$actual_db" == "$expected_db" ]] || {
  printf 'expected DB path %s, got %s\n' "$expected_db" "$actual_db" >&2
  exit 1
}

expected_export="$WORK_DIR/exports/sample"
actual_export="$(default_export_root "$WORK_DIR/images/sample.img")"
[[ "$actual_export" == "$expected_export" ]] || {
  printf 'expected export root %s, got %s\n' "$expected_export" "$actual_export" >&2
  exit 1
}
