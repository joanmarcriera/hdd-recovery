#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"

usage() {
  cat <<'EOF'
Usage:
  image-bulk-discovery-run.sh <image-file> [--map <ddrescue-map>] [--with-photorec]

Runs the broad discovery pipeline for an image:
  init
  structure scan
  filesystem-aware index
  wallet/picture detection
  ext recovery
  bulk_extractor on raw image
  foremost carve
  scalpel carve
  bulk_extractor on recovered corpus
  recoll index on recovered corpus
  report

PhotoRec remains manual unless --with-photorec is requested and an interactive
terminal is attached.
EOF
}

image=""
map_path=""
with_photorec=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --map) map_path="$2"; shift 2 ;;
    --with-photorec) with_photorec=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) image="${image:-$1}"; shift ;;
  esac
done

[[ -n "$image" ]] || { usage; exit 1; }

db="$("$ROOT_DIR/bin/image-analysis-init.sh" "$image" --map "$map_path" --print-db-path)"
[[ -n "$db" ]] || { printf 'failed to determine database path\n' >&2; exit 1; }

printf '[bulk-discovery] init complete: %s\n' "$db"
"$ROOT_DIR/bin/image-structure-scan.sh" "$db"
printf '[bulk-discovery] structure scan complete\n'
"$ROOT_DIR/bin/image-index-tsk.sh" "$db"
"$ROOT_DIR/bin/image-detect-wallets.sh" "$db"
"$ROOT_DIR/bin/image-detect-pictures.sh" "$db"
printf '[bulk-discovery] metadata-first path complete\n'
"$ROOT_DIR/bin/image-ext-recover.sh" "$db" || true
printf '[bulk-discovery] ext recovery complete\n'
"$ROOT_DIR/bin/image-bulk-extractor.sh" "$db" --scope raw || true
printf '[bulk-discovery] raw bulk_extractor complete\n'
"$ROOT_DIR/bin/image-carve.sh" "$db" --method foremost || true
printf '[bulk-discovery] foremost complete\n'
"$ROOT_DIR/bin/image-carve.sh" "$db" --method scalpel || true
printf '[bulk-discovery] scalpel complete\n'

if [[ "$with_photorec" -eq 1 ]]; then
  "$ROOT_DIR/bin/image-carve.sh" "$db" --method photorec || true
  printf '[bulk-discovery] photorec step finished\n'
fi

"$ROOT_DIR/bin/image-bulk-extractor.sh" "$db" --scope recovered || true
printf '[bulk-discovery] recovered bulk_extractor complete\n'
"$ROOT_DIR/bin/image-index-recoll.sh" "$db" --path "$(sqlite3 -readonly "$db" 'SELECT export_root || "/recovered" FROM image_info WHERE id=1;')" || true
printf '[bulk-discovery] recoll index complete\n'
"$ROOT_DIR/bin/image-report.sh" "$db" >/dev/null
"$ROOT_DIR/bin/image-status.sh" "$db"
