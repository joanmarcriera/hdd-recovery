#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PYTHONDONTWRITEBYTECODE=1 python3 - "$ROOT_DIR/bin/image-tag-photos.py" <<'PY'
import importlib.util
import os
import sys

script = sys.argv[1]
spec = importlib.util.spec_from_file_location("image_tag_photos", script)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

env = {
    "OLLAMA_HOSTS": "http://ollama-a:11434, http://ollama-b:11434",
    "OLLAMA_HOST": "http://ignored:11434",
}
assert mod.parse_ollama_urls(None, env) == [
    "http://ollama-a:11434",
    "http://ollama-b:11434",
]
assert mod.default_worker_count([
    "http://ollama-a:11434",
    "http://ollama-b:11434",
], None) == 2
assert mod.parse_ollama_urls("http://manual:11434, http://second:11434", {}) == [
    "http://manual:11434",
    "http://second:11434",
]
assert mod.parse_ollama_urls(None, {"OLLAMA_HOST": "http://single:11434"}) == [
    "http://single:11434",
]
assert mod.parse_ollama_urls(None, {}) == ["http://localhost:11434"]
PY
