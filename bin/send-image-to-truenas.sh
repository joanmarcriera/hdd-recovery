#!/usr/bin/env bash
# Transfer a single disk image (+ its SQLite database + its logs) to TrueNAS.
# Run this on the Optiplex after ddrescue finishes.
#
# Usage:
#   send-image-to-truenas.sh <image-file> <truenas-host> [<remote-data-root>]
#
# Examples:
#   send-image-to-truenas.sh /mnt/recovery16tb/recovery/images/hitachi.img truenas
#   send-image-to-truenas.sh /mnt/recovery16tb/recovery/images/hitachi.img truenas.local /mnt/BigDisk/CryptoBackup
#
# The script expects SSH key auth to be configured for the TrueNAS host.
# After the transfer it prints the exact docker exec command to start analysis.

set -Eeuo pipefail

ROOT_DIR="$(dirname "$(readlink -f "$0")")/.."
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  send-image-to-truenas.sh <image-file> <truenas-host> [<remote-data-root>]

Arguments:
  image-file         Full path to the .img file on this machine
  truenas-host       SSH hostname or IP of the TrueNAS box
  remote-data-root   Dataset path on TrueNAS (default: /mnt/BigDisk/CryptoBackup)
EOF
}

image="${1:-}"
truenas_host="${2:-}"
remote_root="${3:-/mnt/BigDisk/CryptoBackup}"

[[ -n "$image" && -n "$truenas_host" ]] || { usage; exit 1; }
[[ -f "$image" ]] || die "image file not found: $image"

db="$(default_db_path "$image")"
image_name="$(image_name "$image")"
image_base="$(image_basename "$image")"
export_root="$(default_export_root "$image")"
logs_dir="${LOG_ROOT:-/mnt/recovery16tb/recovery/logs}"

# ── 1. ddrescue map coverage check ───────────────────────────────────────────
map_file="$(find "$logs_dir" -name "${image_base}.map" 2>/dev/null | head -1 || true)"
if [[ -n "$map_file" && -f "$map_file" ]]; then
  coverage_line="$("$ROOT_DIR/bin/ddrescue-status.sh" "$map_file" 2>&1 | grep -i 'coverage\|rescued' | head -1 || true)"
  log "ddrescue map coverage: ${coverage_line:-unknown}"
  if echo "$coverage_line" | grep -qE '([0-9]{1,2}\.[0-9]|^[0-9][0-9]?\s)%' 2>/dev/null; then
    pct="$(echo "$coverage_line" | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    if awk "BEGIN{exit !($pct < 95)}"; then
      log "WARNING: coverage is ${pct}% — consider running retry/reverse passes before transferring"
      read -r -p "Continue transfer anyway? [y/N] " yn
      [[ "${yn,,}" == "y" ]] || { log "Transfer cancelled."; exit 1; }
    fi
  fi
else
  log "No ddrescue map file found — skipping coverage check"
fi

# ── 2. Build the list of things to transfer ───────────────────────────────────
remote_images_dir="$remote_root/recovery/images"
remote_exports_dir="$remote_root/recovery/exports"
remote_logs_dir="$remote_root/recovery/logs"

log "Preparing transfer to $truenas_host"
log "  image   : $image"
log "  database: ${db:-none}"
log "  exports : ${export_root:-none}"
log "  logs    : ${logs_dir}/${image_base}.*"
log ""

# ── 3. Create remote directories ─────────────────────────────────────────────
ssh "$truenas_host" "mkdir -p '$remote_images_dir' '$remote_exports_dir' '$remote_logs_dir'"

# ── 4. Transfer image file ────────────────────────────────────────────────────
log "Transferring image file (this may take a long time)..."
rsync -avz --progress \
  --partial --partial-dir=".rsync-partial" \
  "$image" \
  "${truenas_host}:${remote_images_dir}/"

# ── 5. Transfer SQLite database ───────────────────────────────────────────────
if [[ -f "$db" ]]; then
  log "Transferring analysis database..."
  rsync -avz "$db" "${truenas_host}:${remote_images_dir}/"
else
  log "No analysis database found — run image-analysis-init.sh and image-structure-scan.sh first"
fi

# ── 6. Transfer ddrescue logs (map files, rate logs) ─────────────────────────
mapfiles_found=0
while IFS= read -r -d '' f; do
  rsync -avz "$f" "${truenas_host}:${remote_logs_dir}/"
  mapfiles_found=1
done < <(find "$logs_dir" -name "${image_base}.*" -print0 2>/dev/null || true)
[[ "$mapfiles_found" -eq 0 ]] && log "No ddrescue log files found in $logs_dir"

# ── 7. Transfer any existing export outputs ───────────────────────────────────
if [[ -d "$export_root" ]]; then
  log "Transferring existing export outputs..."
  rsync -avz --progress "$export_root/" "${truenas_host}:${remote_exports_dir}/${image_base}/"
else
  log "No export directory yet — will be created by the container"
fi

# ── 8. Done — print next steps ────────────────────────────────────────────────
remote_db="${remote_images_dir}/${image_name}.analysis.sqlite"

printf '\n'
log "Transfer complete. Next steps on TrueNAS:"
printf '
  1. Open the browser terminal: http://%s:7681
  2. Or run analysis directly:

     docker exec -it hdd-forensics bash -c "
       DB=%s
       bin/image-index-tsk.sh \$DB
       bin/image-detect-wallets.sh \$DB
       bin/image-detect-pictures.sh \$DB
       bin/image-bulk-extractor.sh \$DB --scope raw
       bin/image-ext-recover.sh \$DB
       bin/image-carve.sh \$DB --method foremost
       bin/image-carve.sh \$DB --method scalpel
       bin/image-bulk-extractor.sh \$DB --scope recovered
       bin/image-ocr-seed-scan.py \$DB
       bin/image-report.sh \$DB
     "

' "$truenas_host" "$remote_db"
