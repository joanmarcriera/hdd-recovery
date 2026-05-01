"""Disk discovery and stage-status derivation from real system state."""
from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from config import (
    BIN_DIR, DB_SUFFIX, EXPORT_ROOT, IMAGE_ROOT, JOBS_DIR, LOG_ROOT,
    load_job_conf,
)
from stages import StageDef, STAGES

# ---------------------------------------------------------------------------
# ddrescue rate-log parsing
# ---------------------------------------------------------------------------

def parse_rate_log(path: Path) -> Optional[dict]:
    """
    Parse the last entry of a ddrescue --log-rates file.
    Format (whitespace-separated, no units): time_s rate_Bps avg_Bps errors rescued_bytes
    Returns dict with rescued_bytes, rate_bps, elapsed_s, or None on failure.
    """
    try:
        lines = [
            ln for ln in path.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        if not lines:
            return None
        last = lines[-1].split()
        if len(last) < 5:
            return None
        elapsed_s = float(last[0])
        rate_bps = float(last[1])
        rescued_bytes = int(last[4])
        return {
            "elapsed_s": elapsed_s,
            "rate_bps": rate_bps,
            "rescued_bytes": rescued_bytes,
        }
    except Exception:
        return None


def fetch_smart_attrs(dev: str) -> str:
    """
    Run smartctl -A on dev and return a compact summary of key attributes.
    Only read (no self-test). Returns empty string on failure.
    """
    if not dev:
        return ""
    try:
        r = subprocess.run(
            ["smartctl", "-A", dev],
            capture_output=True, text=True, timeout=8,
        )
        attrs_of_interest = {
            "190": "Temp (190)",
            "194": "Temp (194)",
            "5":   "Reallocated",
            "197": "Pending",
            "198": "Uncorrectable",
            "9":   "Power-on h",
        }
        results: list[str] = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            num = parts[0]
            if num in attrs_of_interest and len(parts) >= 10:
                raw = parts[-1]
                label = attrs_of_interest[num]
                results.append(f"{label}: {raw}")
        return "  ".join(results) if results else "(no SMART attrs parsed)"
    except FileNotFoundError:
        return "(smartctl not found)"
    except Exception as exc:
        return f"(smartctl error: {exc})"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class StageStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    PARTIAL = "partial"
    FAILED  = "failed"
    SKIPPED = "skipped"


ICON = {
    StageStatus.PENDING: ("·",  "dim"),
    StageStatus.RUNNING: ("⟳",  "bold cyan"),
    StageStatus.DONE:    ("✓",  "green"),
    StageStatus.PARTIAL: ("~",  "yellow"),
    StageStatus.FAILED:  ("✗",  "bold red"),
    StageStatus.SKIPPED: ("○",  "dim"),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScanRun:
    id: int
    stage: str
    status: str
    started_at: str
    ended_at: Optional[str]
    command_line: Optional[str]
    log_path: Optional[str]
    output_dir: Optional[str]
    notes: Optional[str]


@dataclass
class DiskInfo:
    conf_path: Optional[Path]
    basename: str
    job_name: str
    source_model: str
    source_serial: str
    source_dev: str
    image_path: Path
    map_path: Optional[Path]
    db_path: Path
    export_root: Path
    # populated by _populate
    image_exists: bool = False
    image_size_bytes: int = 0
    db_exists: bool = False
    map_exists: bool = False
    map_coverage_pct: Optional[float] = None
    image_info_exists: bool = False
    scan_runs: list[ScanRun] = field(default_factory=list)
    # safecopy stage marker files (written by image-safecopy-run.sh)
    safecopy_s1_done: bool = False
    safecopy_s2_done: bool = False
    safecopy_s3_done: bool = False
    # extra fields populated from job conf
    rate_log_path: Optional[Path] = None
    source_size_bytes: int = 0

    @property
    def display_name(self) -> str:
        m = self.source_model or self.basename
        if len(m) > 32:
            m = m[:29] + "…"
        return m

    def latest_run(self, stage_key: str) -> Optional[ScanRun]:
        hits = [r for r in self.scan_runs if r.stage == stage_key]
        return hits[-1] if hits else None

    def all_runs(self, stage_key: str) -> list[ScanRun]:
        return [r for r in self.scan_runs if r.stage == stage_key]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_disks() -> list[DiskInfo]:
    disks: list[DiskInfo] = []
    seen: set[str] = set()

    if JOBS_DIR.exists():
        for conf in sorted(JOBS_DIR.glob("*.conf")):
            if "template" in conf.name:
                continue
            try:
                d = _disk_from_conf(conf)
                if d and d.basename not in seen:
                    disks.append(d)
                    seen.add(d.basename)
            except Exception:
                pass

    if IMAGE_ROOT.exists():
        for img in sorted(IMAGE_ROOT.glob("*.img")):
            basename = img.stem
            if basename in seen:
                continue
            d = DiskInfo(
                conf_path=None,
                basename=basename,
                job_name=basename,
                source_model="",
                source_serial="",
                source_dev="",
                image_path=img,
                map_path=_find_map(basename),
                db_path=Path(str(img) + DB_SUFFIX),
                export_root=EXPORT_ROOT / basename,
            )
            _populate(d)
            disks.append(d)
            seen.add(basename)

    return disks


def _disk_from_conf(conf_path: Path) -> Optional[DiskInfo]:
    conf = load_job_conf(conf_path)
    basename = conf.get("BASENAME", "")
    if not basename:
        return None
    image_file = conf.get("IMAGE_FILE", "")
    image_path = Path(image_file) if image_file else IMAGE_ROOT / f"{basename}.img"
    map_file = conf.get("MAP_FILE", "")
    map_path = Path(map_file) if map_file else _find_map(basename)
    db_path = Path(str(image_path) + DB_SUFFIX)
    rate_log_str = conf.get("RATE_LOG", "")
    rate_log_path = Path(rate_log_str) if rate_log_str else LOG_ROOT / f"{basename}.rates.log"
    try:
        source_size_bytes = int(conf.get("SOURCE_SIZE_BYTES", "0") or "0")
    except ValueError:
        source_size_bytes = 0
    d = DiskInfo(
        conf_path=conf_path,
        basename=basename,
        job_name=conf.get("JOB_NAME", basename),
        source_model=conf.get("SOURCE_MODEL", ""),
        source_serial=conf.get("SOURCE_SERIAL", ""),
        source_dev=conf.get("SOURCE_DEV", ""),
        image_path=image_path,
        map_path=map_path,
        db_path=db_path,
        export_root=EXPORT_ROOT / basename,
        rate_log_path=rate_log_path,
        source_size_bytes=source_size_bytes,
    )
    _populate(d)
    return d


def _find_map(basename: str) -> Optional[Path]:
    candidate = LOG_ROOT / f"{basename}.map"
    return candidate if candidate.exists() else None


def _populate(d: DiskInfo) -> None:
    d.image_exists = d.image_path.exists()
    d.image_size_bytes = d.image_path.stat().st_size if d.image_exists else 0
    d.db_exists = d.db_path.exists()
    if not d.map_path:
        d.map_path = _find_map(d.basename)
    d.map_exists = bool(d.map_path and d.map_path.exists())
    if d.map_exists and d.map_path:
        d.map_coverage_pct = _map_coverage(d.map_path)
    if d.db_exists:
        d.scan_runs = _load_scan_runs(d.db_path)
        d.image_info_exists = _check_image_info(d.db_path)
    # safecopy stage marker files
    d.safecopy_s1_done = (LOG_ROOT / f"{d.basename}-safecopy-stage1.done").exists()
    d.safecopy_s2_done = (LOG_ROOT / f"{d.basename}-safecopy-stage2.done").exists()
    d.safecopy_s3_done = (LOG_ROOT / f"{d.basename}-safecopy-stage3.done").exists()
    # default rate log path if not already set from conf
    if not d.rate_log_path:
        candidate = LOG_ROOT / f"{d.basename}.rates.log"
        d.rate_log_path = candidate if candidate.exists() else candidate


# ---------------------------------------------------------------------------
# Map coverage
# ---------------------------------------------------------------------------

def _map_coverage(path: Path) -> Optional[float]:
    """Parse ddrescuelog -t output for rescued percentage."""
    try:
        r = subprocess.run(
            ["ddrescuelog", "-t", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            # "     rescued:  160041 MB,  in … (100%)"
            m = re.search(r'rescued:.*?\(\s*([\d.]+)\s*%\)', line)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return _map_coverage_direct(path)


def _map_coverage_direct(path: Path) -> Optional[float]:
    """Fallback: parse hex pos/size/status lines in the map file."""
    try:
        total = 0
        rescued = 0
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:
                    size = int(parts[1], 16)
                    status = parts[2]
                    total += size
                    if status == "+":
                        rescued += size
                except ValueError:
                    pass
        if total > 0:
            return (rescued / total) * 100.0
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _load_scan_runs(db_path: Path) -> list[ScanRun]:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,stage,status,started_at,ended_at,command_line,log_path,output_dir,notes "
            "FROM scan_runs ORDER BY id"
        ).fetchall()
        conn.close()
        return [
            ScanRun(
                id=r["id"], stage=r["stage"], status=r["status"],
                started_at=r["started_at"], ended_at=r["ended_at"],
                command_line=r["command_line"], log_path=r["log_path"],
                output_dir=r["output_dir"], notes=r["notes"],
            )
            for r in rows
        ]
    except Exception:
        return []


def _check_image_info(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        count = conn.execute("SELECT COUNT(*) FROM image_info WHERE id=1").fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Live process detection
# ---------------------------------------------------------------------------

def is_process_live(pattern: str, disk: Optional[DiskInfo] = None) -> bool:
    """True if any process matching pattern is running (optionally for this disk)."""
    for p in pattern.split("|"):
        try:
            r = subprocess.run(
                ["pgrep", "-fa", p],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode != 0 or not r.stdout.strip():
                continue
            if disk is None:
                return True
            for line in r.stdout.strip().splitlines():
                if disk.basename in line or str(disk.image_path) in line or str(disk.db_path) in line:
                    return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------------------
# Stage status derivation
# ---------------------------------------------------------------------------

def get_stage_status(disk: DiskInfo, stage: StageDef) -> StageStatus:  # noqa: C901
    key = stage.key

    # ── Manual/wizard stages ──────────────────────────────────────────────
    if key in ("identify-source", "create-job-config"):
        if disk.conf_path and disk.conf_path.exists():
            return StageStatus.DONE
        # Image already exists → these steps ran before this session
        return StageStatus.DONE if disk.image_exists else StageStatus.PENDING

    if key == "ddrescue-preview":
        # Image already exists → the preview and first pass already ran
        if disk.image_exists:
            return StageStatus.DONE
        return StageStatus.DONE if disk.conf_path else StageStatus.PENDING

    # ── ddrescue phases ───────────────────────────────────────────────────
    ddrescue_live = stage.pgrep_pattern and is_process_live(stage.pgrep_pattern, disk)

    if key == "ddrescue-first":
        if ddrescue_live:
            return StageStatus.RUNNING
        if not disk.map_exists:
            # No map — if image exists it was acquired somehow, call it done
            return StageStatus.DONE if disk.image_exists else StageStatus.PENDING
        if disk.map_coverage_pct is not None:
            return StageStatus.DONE if disk.map_coverage_pct >= 99.0 else StageStatus.PARTIAL
        return StageStatus.DONE if disk.image_exists else StageStatus.PARTIAL

    if key == "ddrescue-map-status":
        if not disk.map_exists:
            return StageStatus.SKIPPED if disk.image_exists else StageStatus.PENDING
        return StageStatus.DONE

    if key == "ddrescue-mapview":
        if not disk.map_exists:
            return StageStatus.SKIPPED if disk.image_exists else StageStatus.PENDING
        return StageStatus.DONE

    if key in ("ddrescue-retry", "ddrescue-reverse", "ddrescue-retrim"):
        if ddrescue_live:
            return StageStatus.RUNNING
        if not disk.map_exists:
            return StageStatus.SKIPPED if disk.image_exists else StageStatus.PENDING
        if disk.map_coverage_pct is not None and disk.map_coverage_pct >= 99.9:
            return StageStatus.SKIPPED
        return StageStatus.PENDING

    # ── safecopy (marker-file tracked, no DB until init-db) ──────────────
    if key == "safecopy-stage1":
        if stage.pgrep_pattern and is_process_live(stage.pgrep_pattern, disk):
            return StageStatus.RUNNING
        return StageStatus.DONE if disk.safecopy_s1_done else StageStatus.PENDING

    if key == "safecopy-stage2":
        if stage.pgrep_pattern and is_process_live(stage.pgrep_pattern, disk):
            return StageStatus.RUNNING
        if not disk.safecopy_s1_done:
            return StageStatus.PENDING
        return StageStatus.DONE if disk.safecopy_s2_done else StageStatus.PENDING

    if key == "safecopy-stage3":
        if stage.pgrep_pattern and is_process_live(stage.pgrep_pattern, disk):
            return StageStatus.RUNNING
        if not disk.safecopy_s2_done:
            return StageStatus.PENDING
        return StageStatus.DONE if disk.safecopy_s3_done else StageStatus.PENDING

    # ── init-db ───────────────────────────────────────────────────────────
    if key == "init-db":
        if not disk.image_exists:
            return StageStatus.PENDING
        return StageStatus.DONE if disk.image_info_exists else StageStatus.PENDING

    # ── generate-report ───────────────────────────────────────────────────
    if key == "generate-report":
        rdir = disk.export_root / "reports"
        if rdir.exists():
            try:
                if any(rdir.iterdir()):
                    return StageStatus.DONE
            except Exception:
                pass
        return StageStatus.PENDING

    # ── DB-tracked stages ─────────────────────────────────────────────────
    if stage.scan_run_key:
        live = stage.pgrep_pattern and is_process_live(stage.pgrep_pattern, disk)
        run = disk.latest_run(stage.scan_run_key)

        if live and run and run.status == "running":
            return StageStatus.RUNNING
        if run is None:
            return StageStatus.PENDING
        if run.status == "running":
            # stale row — process died without updating DB
            return StageStatus.PARTIAL
        if run.status == "ok":
            return StageStatus.DONE
        if run.status == "partial":
            return StageStatus.PARTIAL
        if run.status == "failed":
            return StageStatus.FAILED
        return StageStatus.PENDING

    return StageStatus.PENDING


def count_done(disk: DiskInfo) -> int:
    return sum(1 for s in STAGES if get_stage_status(disk, s) == StageStatus.DONE)


def next_pending_stage(disk: DiskInfo) -> Optional[StageDef]:
    for s in STAGES:
        st = get_stage_status(disk, s)
        if st in (StageStatus.PENDING, StageStatus.PARTIAL, StageStatus.FAILED):
            return s
    return None


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n} TB"


def fmt_elapsed(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def get_stage_note(disk: DiskInfo, stage: StageDef, status: StageStatus) -> str:
    """Short reason string shown in the Note column of the stage list."""
    from datetime import datetime, timezone

    key = stage.key

    # ddrescue optional retry passes: explain exactly why they're skipped or needed
    if key in ("ddrescue-retry", "ddrescue-reverse", "ddrescue-retrim"):
        if status == StageStatus.SKIPPED:
            pct = disk.map_coverage_pct
            return f"not needed — {pct:.2f}% rescued" if pct is not None else "not needed"
        if status == StageStatus.PENDING:
            pct = disk.map_coverage_pct
            if pct is not None and pct < 100.0:
                return f"recommended — {100-pct:.2f}% unread"
            return "only if coverage < 100%"

    if status == StageStatus.RUNNING:
        run = disk.latest_run(stage.scan_run_key) if stage.scan_run_key else None
        if run:
            try:
                dt = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
                secs = int((datetime.now(timezone.utc) - dt).total_seconds())
                return f"running {fmt_elapsed(secs)}"
            except Exception:
                pass
        return "running"

    if status == StageStatus.PARTIAL:
        run = disk.latest_run(stage.scan_run_key) if stage.scan_run_key else None
        if run and run.notes:
            note = run.notes.split(";")[0][:32]
            return note
        return "incomplete — consider rerun"

    if status == StageStatus.FAILED:
        run = disk.latest_run(stage.scan_run_key) if stage.scan_run_key else None
        if run and run.notes:
            return run.notes.split(";")[0][:32]
        return "last run failed"

    if status == StageStatus.SKIPPED and stage.is_optional:
        return "optional — not run"

    return ""
