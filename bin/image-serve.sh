#!/usr/bin/env bash
# Launch the hdd-recovery read-only web UI.
# Wraps image-serve.py; use this from the TUI or command line.
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
CONFIG_FILE="${HDD_RECOVERY_CONFIG:-$ROOT_DIR/config/analysis-pipeline.env}"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

usage() {
  cat <<'EOF'
Usage:
  image-serve.sh [options]

Options:
  --root DIR    Directory tree with *.analysis.sqlite files
                (default: DB_ROOT, or /data/db)
  --port PORT   TCP port (default: 7788)
  --host HOST   Bind address (default: 127.0.0.1)
  -h, --help    Show this help

Opens: http://127.0.0.1:7788/ (localhost only by default)
EOF
}

ROOT="${DB_ROOT:-/data/db}"
PORT=7788
HOST="127.0.0.1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)  ROOT="$2";  shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    --host)  HOST="$2";  shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'ERROR: unknown argument: %s\n' "$1" >&2; exit 1 ;;
  esac
done

[[ -d "$ROOT" ]] || { printf 'ERROR: root directory not found: %s\n' "$ROOT" >&2; exit 1; }

exec python3 "$ROOT_DIR/bin/image-serve.py" \
  --root "$ROOT" --port "$PORT" --host "$HOST"
