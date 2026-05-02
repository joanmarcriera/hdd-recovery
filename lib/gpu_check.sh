#!/usr/bin/env bash
# GPU preflight helpers for cracking stages. These functions intentionally
# hard-fail on missing NVIDIA/CUDA devices so hashcat never silently burns days
# on CPU.
set -Eeuo pipefail

ROOT_DIR="/root/hdd-recovery"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"

_hashcat_info() {
  need_cmd hashcat
  local hashcat_home="${HASHCAT_HOME:-${HOME:-/tmp}}"
  if ! mkdir -p "$hashcat_home/.local/share/hashcat/sessions" 2>/dev/null; then
    hashcat_home="/tmp/hashcat-home-${UID:-0}"
    mkdir -p "$hashcat_home/.local/share/hashcat/sessions"
  fi
  HOME="$hashcat_home" hashcat -I 2>&1
}

_require_hashcat_gpu() {
  local require_cuda="${1:-0}"
  local info
  if ! info="$(_hashcat_info)"; then
    printf '%s\n' "$info" >&2
    die "hashcat -I failed; cannot verify GPU availability"
  fi

  local name
  if name="$(HASHCAT_INFO="$info" python3 - "$require_cuda" <<'PY'
import os
import re
import sys

require_cuda = sys.argv[1] == "1"
info = os.environ.get("HASHCAT_INFO", "")
blocks = re.split(r"(?=Backend Device ID #)", info)
for block in blocks:
    if not re.search(r"Type\.*:\s*GPU", block, re.IGNORECASE):
        continue
    if not re.search(r"Vendor\.*:\s*NVIDIA", block, re.IGNORECASE):
        continue
    if require_cuda and not (
        re.search(r"Backend\.*:\s*CUDA", block, re.IGNORECASE)
        or re.search(r"CUDA", info, re.IGNORECASE)
    ):
        continue
    name = re.search(r"Name\.*:\s*(.+)", block)
    print(name.group(1).strip() if name else "NVIDIA GPU")
    sys.exit(0)
sys.exit(1)
PY
)"; then
    printf '%s\n' "$name"
  else
    printf 'hashcat -I output did not contain a usable NVIDIA GPU%s:\n' \
      "$([[ "$require_cuda" -eq 1 ]] && printf ' with CUDA backend' || true)" >&2
    printf '%s\n' "$info" >&2
    exit 1
  fi
}

require_nvidia_gpu() {
  local name
  if ! name="$(_require_hashcat_gpu 0)"; then
    return 1
  fi
  printf 'NVIDIA GPU detected: %s\n' "$name"
}

require_cuda() {
  local name
  if ! name="$(_require_hashcat_gpu 1)"; then
    return 1
  fi
  printf 'NVIDIA CUDA GPU detected: %s\n' "$name"
}
