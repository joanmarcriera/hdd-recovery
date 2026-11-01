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


def isolate_tui_paths():
    """Point tui/config.py at an empty temp root before it is first imported,
    so TUI tests never read (or stat) the appliance's /root/hdd-recovery paths.
    Restore the caller's environment once the import is done:

        with isolate_tui_paths():
            import state
    """
    import atexit
    import contextlib
    import os
    import shutil
    import tempfile

    @contextlib.contextmanager
    def _ctx():
        tmp = tempfile.mkdtemp(prefix="hdd-tui-test-")
        atexit.register(shutil.rmtree, tmp, True)
        keys = ("HDD_RECOVERY_ROOT", "HDD_RECOVERY_CONFIG")
        saved = {k: os.environ.get(k) for k in keys}
        os.environ["HDD_RECOVERY_ROOT"] = tmp
        os.environ["HDD_RECOVERY_CONFIG"] = os.path.join(tmp, "analysis-pipeline.env")
        try:
            yield tmp
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    return _ctx()
