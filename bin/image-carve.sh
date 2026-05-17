#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
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
  if [[ -f "$log_path" ]]; then
    mv "$log_path" "${log_path}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
  fi
  # Back up any existing output so reruns don't overwrite partial results.
  # Scalpel refuses to run into a non-empty directory; others may silently clobber.
  if [[ -d "$tool_dir" ]] && [[ -n "$(ls -A "$tool_dir" 2>/dev/null)" ]]; then
    local backup_dir="${tool_dir}.prev-$(date -u +%Y%m%dT%H%M%SZ)"
    log "Backing up existing ${tool} output: $(basename "$tool_dir") -> $(basename "$backup_dir")"
    mv "$tool_dir" "$backup_dir"
  fi
  mkdir -p "$tool_dir"
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
    recoverjpeg)
      need_cmd recoverjpeg
      { recoverjpeg -o "$tool_dir" "$image"; } 2>&1 | tee "$log_path" || status="partial"
      ;;
    magicrescue)
      need_cmd magicrescue
      local recipes_dir="/usr/share/magicrescue/recipes"
      local recipe_args=()
      local default_recipes=(jpeg-jfif jpeg-exif png sqlite zip rar gzip msoffice)
      local extra_recipes=()
      if [[ -n "${MAGICRESCUE_EXTRA_RECIPES:-}" ]]; then
        # Optional space-separated recipe names, e.g. "mp3-id3v1 mp3-id3v2 avi".
        read -r -a extra_recipes <<< "$MAGICRESCUE_EXTRA_RECIPES"
      fi
      # wallet + pictures + documents. Audio/video recipes are opt-in because
      # mp3-id3v1 can produce huge false-positive streams on raw disk images.
      for recipe in "${default_recipes[@]}" "${extra_recipes[@]}"; do
        [[ -f "$recipes_dir/$recipe" ]] && recipe_args+=(-r "$recipes_dir/$recipe")
      done
      if [[ ${#recipe_args[@]} -eq 0 ]]; then
        printf 'No magicrescue recipes found in %s\n' "$recipes_dir" | tee "$log_path"
        status="failed"
        notes="no recipes found in $recipes_dir"
        return 1
      fi
      printf 'Using recipes: %s\n' "${recipe_args[*]}" | tee "$log_path"
      { magicrescue "${recipe_args[@]}" -d "$tool_dir" "$image"; } 2>&1 | tee -a "$log_path" || status="partial"
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
  all) for tool in foremost scalpel recoverjpeg magicrescue photorec; do run_one "$tool"; done ;;
  foremost|scalpel|recoverjpeg|magicrescue|photorec) run_one "$method" ;;
  *) die "unsupported method: $method" ;;
esac
