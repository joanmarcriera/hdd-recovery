"""Paths and configuration loaded from analysis-pipeline.env."""
import os
import subprocess
from pathlib import Path

ROOT_DIR = Path("/root/hdd-recovery")
JOBS_DIR = ROOT_DIR / "jobs"
BIN_DIR = ROOT_DIR / "bin"
CONFIG_FILE = Path(os.environ.get("HDD_RECOVERY_CONFIG", str(ROOT_DIR / "config/analysis-pipeline.env")))


def _load_simple_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_env = _load_simple_env(CONFIG_FILE)
IMAGE_ROOT = Path(_env.get("IMAGE_ROOT", "/mnt/recovery16tb/recovery/images"))
EXPORT_ROOT = Path(_env.get("EXPORT_ROOT", "/mnt/recovery16tb/recovery/exports"))
LOG_ROOT = Path(_env.get("LOG_ROOT", "/mnt/recovery16tb/recovery/logs"))
DB_SUFFIX = _env.get("DB_SUFFIX", ".analysis.sqlite")
TEMPLATE_CONF = BIN_DIR / "ddrescue-job-template.conf"


def load_job_conf(conf_path: Path) -> dict[str, str]:
    """Source a bash job conf and extract key variables via subprocess."""
    keys = [
        "JOB_NAME", "SOURCE_DEV", "SOURCE_BY_PATH", "SOURCE_MODEL",
        "SOURCE_SERIAL", "BASENAME", "IMAGE_FILE", "MAP_FILE",
        "EVENT_LOG", "RATE_LOG", "DEST_MOUNT", "IMAGES_DIR", "LOGS_DIR",
        "SOURCE_SIZE_BYTES",
    ]
    printcmds = "\n".join(f'printf "%s=%s\\n" "{k}" "${{{k}:-}}"' for k in keys)
    script = f"source {conf_path} 2>/dev/null\n{printcmds}"
    try:
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, timeout=5,
        )
        out: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k] = v
        return out
    except Exception:
        return {}
