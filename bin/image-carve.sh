#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

usage() {
  cat <<'EOF'
Usage:
  image-carve.sh <db-path> [--method photorec|scalpel|foremost|all]

Runs one or more carving tools against the image and registers carved outputs in
the per-image SQLite catalog.
EOF
}

db="${1:-}"
method="all"
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$db" ]] || { usage; exit 1; }
[[ -f "$db" ]] || die "database not found: $db"

image="$(db_image_path "$db")"
export_root="$(db_image_export_root "$db")"
out_root="$export_root/recovered"

run_one() {
  local tool="$1"
  local stage="carve-$tool"
  local tool_dir="$out_root/$tool"
  local log_path="$export_root/logs/${stage}.log"
  local status="ok"
  local notes=""
  mkdir -p "$tool_dir"
  if [[ -f "$log_path" ]]; then
    mv "$log_path" "${log_path}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  local run_id
  run_id="$(record_scan_start "$db" "$stage" "$0 $db --method $tool" "$log_path" "$tool_dir")"
  finish_run() {
    record_scan_end "$db" "$run_id" "$status" "$notes"
  }
  trap finish_run RETURN
  case "$tool" in
    foremost)
      { foremost -Q -o "$tool_dir" -i "$image"; } 2>&1 | tee "$log_path" || status="partial"
      ;;
    scalpel)
      { scalpel -c "$ROOT_DIR/config/scalpel/scalpel-wallet-and-docs.conf" -o "$tool_dir" "$image"; } 2>&1 | tee "$log_path" || status="partial"
      ;;
    photorec)
      if [[ ! -t 0 ]]; then
        printf 'PhotoRec requires an interactive terminal in the current implementation.\n' | tee "$log_path"
        printf 'Run it manually or implement a tested /cmd profile for this exact version before automation.\n' | tee -a "$log_path"
        status="skipped"
        notes="photorec requires an interactive terminal in the current implementation"
        return 0
      fi
      {
        log "PhotoRec is interactive in this implementation. Running only because a terminal is attached."
        photorec /log /d "$tool_dir" "$image"
      } 2>&1 | tee "$log_path" || status="partial"
      ;;
    *)
      die "unknown carve tool: $tool"
      ;;
  esac
  printf '\nRegistering carved artifacts for %s...\n' "$tool" | tee -a "$log_path"
  register_artifacts_from_dir "$db" "$tool" "$tool_dir" "$run_id" 2>&1 | tee -a "$log_path" || {
    status="partial"
    notes="artifact registration failed after carve output was created"
  }
  trap - RETURN
  record_scan_end "$db" "$run_id" "$status" "$notes"
}

case "$method" in
  all) for tool in foremost scalpel photorec; do run_one "$tool"; done ;;
  foremost|scalpel|photorec) run_one "$method" ;;
  *) die "unsupported method: $method" ;;
esac
