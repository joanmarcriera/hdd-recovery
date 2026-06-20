#!/usr/bin/env bash
# Run the offline unit-test suite (stdlib unittest, no third-party deps, no
# fixtures). Safe to run anywhere — CI, the repo checkout, or inside the
# container. The fixture-dependent forensic checks live in tests/smoke/.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
exec python3 -m unittest discover -s tests/unit -p 'test_*.py' "$@"
