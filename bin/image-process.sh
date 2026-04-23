#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"

usage() {
  cat <<'EOF'
Usage:
  image-process.sh <image-file> [--map <ddrescue-map>] [--with-bulk] [--with-ext] [--with-carve] [--with-recoll]

Initializes the per-image catalog and runs the default metadata-first analysis
pipeline:
  init -> structure scan -> TSK index -> wallet detection -> picture detection -> report
Optional stages can add ext recovery, carving, bulk_extractor, and Recoll.
EOF
}

image=""
map_path=""
with_bulk=0
with_ext=0
with_carve=0
with_recoll=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --map) map_path="$2"; shift 2 ;;
    --with-bulk) with_bulk=1; shift ;;
    --with-ext) with_ext=1; shift ;;
    --with-carve) with_carve=1; shift ;;
    --with-recoll) with_recoll=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) image="${image:-$1}"; shift ;;
  esac
done

[[ -n "$image" ]] || { usage; exit 1; }

db="$("$ROOT_DIR/bin/image-analysis-init.sh" "$image" --map "$map_path" --print-db-path)"
[[ -n "$db" ]] || { printf 'failed to determine database path\n' >&2; exit 1; }

"$ROOT_DIR/bin/image-structure-scan.sh" "$db"
"$ROOT_DIR/bin/image-index-tsk.sh" "$db"
"$ROOT_DIR/bin/image-detect-wallets.sh" "$db"
"$ROOT_DIR/bin/image-detect-pictures.sh" "$db"

if [[ "$with_ext" -eq 1 ]]; then
  "$ROOT_DIR/bin/image-ext-recover.sh" "$db" || true
fi

if [[ "$with_carve" -eq 1 ]]; then
  "$ROOT_DIR/bin/image-carve.sh" "$db" --method foremost || true
  "$ROOT_DIR/bin/image-carve.sh" "$db" --method scalpel || true
fi

if [[ "$with_bulk" -eq 1 ]]; then
  "$ROOT_DIR/bin/image-bulk-extractor.sh" "$db" --scope raw || true
fi

if [[ "$with_recoll" -eq 1 ]]; then
  "$ROOT_DIR/bin/image-index-recoll.sh" "$db" || true
fi

"$ROOT_DIR/bin/image-report.sh" "$db" >/dev/null
"$ROOT_DIR/bin/image-status.sh" "$db"
