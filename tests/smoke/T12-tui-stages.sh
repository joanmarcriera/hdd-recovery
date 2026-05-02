#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-/root/hdd-recovery}"

cat <<'EOF'
T12 TUI stages smoke test

Expected checks:
  - all Stage 1 stage keys are present
  - each script exists under bin/
  - each requires_prior key references an existing stage
EOF

cd "$ROOT_DIR"
PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/hdd-pycache}" python3 - <<'PY'
from pathlib import Path
from tui.stages import STAGES

expected = {
    "wallet-inspect",
    "crack-wallet",
    "btcrecover",
    "crack-keepass",
    "text-seed-scan",
    "enrich-trid",
    "dedup-photos",
    "extract-winmem",
    "volatility3",
}
keys = {stage.key for stage in STAGES}
missing = sorted(expected - keys)
if missing:
    raise SystemExit(f"missing stage keys: {missing}")
missing_scripts = [stage.key for stage in STAGES if stage.script and not (Path("bin") / stage.script).exists()]
if missing_scripts:
    raise SystemExit(f"missing scripts: {missing_scripts}")
missing_requires = [(stage.key, req) for stage in STAGES for req in stage.requires_prior if req not in keys]
if missing_requires:
    raise SystemExit(f"missing requires_prior keys: {missing_requires}")
print(f"stage_count={len(STAGES)}")
print("PASS")
PY

if [[ "${RUN_TUI:-0}" == "1" ]]; then
  "$ROOT_DIR/bin/tui.sh"
fi
