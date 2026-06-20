"""Load bin/*.py modules whose filenames contain hyphens (not importable by
name) so unit tests can exercise their pure functions. Offline, no fixtures."""
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_module(relpath: str):
    """Import a module from a path relative to the repo root (e.g.
    'bin/image-serve.py') and return it. Module name is derived from the stem."""
    path = REPO_ROOT / relpath
    name = "hdd_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
