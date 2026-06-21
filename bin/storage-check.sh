#!/usr/bin/env bash
# Report where each data root (DB_ROOT/EXPORT_ROOT/IMAGE_ROOT/LOG_ROOT) actually
# lands. Inside the container, a root that shares a device with "/" resolved to
# the writable overlay layer instead of a mounted dataset — on TrueNAS that
# silently fills the FastPool SSD and is discarded on the next recreate.
#
# Use this to confirm a TrueNAS storage fix (env vars or bind mounts) landed.
# Read-only; exits non-zero when a root is on the overlay (unless overridden).
set -Eeuo pipefail

ROOT_DIR="${HDD_RECOVERY_ROOT:-/root/hdd-recovery}"
# shellcheck disable=SC1091
source "$ROOT_DIR/lib/common.sh"
need_cmd python3

export IMAGE_ROOT EXPORT_ROOT LOG_ROOT DB_ROOT

PYTHONPATH="$ROOT_DIR" python3 - <<'PY'
import os, sys
sys.path.insert(0, os.environ["PYTHONPATH"])
from lib.storage_guard import (
    check_environment, dangerous_roots, in_container, overlay_allowed,
)

results = check_environment()
print(f"In container: {in_container()}    "
      f"HDD_ALLOW_OVERLAY override: {overlay_allowed()}")
print()
print(f"{'ROOT':12} {'STATUS':9} {'PATH'}")
for r in results:
    flag = {"overlay": "DANGER", "ok": "ok", "skipped": "n/a",
            "unknown": "?"}.get(r.status, r.status)
    print(f"{r.name:12} {flag:9} {r.path}")
    if r.detail:
        print(f"{'':12} {'':9} -> {r.detail}")

danger = dangerous_roots(results)
if danger and not overlay_allowed():
    print()
    print("FIX: bind-mount these roots to a real dataset, or set the matching "
          "env var under an already-mounted path. See docker/docker-compose.yml.")
    sys.exit(1)
PY
