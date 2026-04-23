#!/usr/bin/env bash
# Launch the HDD Recovery TUI using uv.
set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TUI_DIR="$ROOT_DIR/tui"

UV="${HOME}/.local/bin/uv"
if ! command -v uv &>/dev/null && [[ -x "$UV" ]]; then
  export PATH="${HOME}/.local/bin:$PATH"
fi

if ! command -v uv &>/dev/null; then
  printf 'uv not found. Install with:\n  curl -LsSf https://astral.sh/uv/install.sh | sh\n' >&2
  exit 1
fi

cd "$TUI_DIR"
exec uv run python main.py "$@"
