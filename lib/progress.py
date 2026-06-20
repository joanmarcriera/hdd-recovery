"""Useful-progress probes for supervised recovery stages."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


TABLE_COUNT_STAGES = {
    "detect-wallets": "wallet_candidates",
    "detect-pictures": "picture_candidates",
    "enrich-photos": "findings",
    "wallet-inspect": "wallet_keys",
    "text-seed-scan": "findings",
    "ocr-seed-scan": "findings",
    "yara-scan": "findings",
    "pdf-extract": "findings",
    "ntfs-artifact-summary": "recovered_artifacts",
    "regripper": "findings",
    "rifiuti2": "findings",
    "plaso-timeline": "findings",
    "llava-tag-photos": "findings",
}

DIR_COUNTER_STAGES = {
    "index-tsk",
    "ext-recover",
    "ntfs-recover",
    "fat-recover",
    "xfs-recover",
    "btrfs-recover",
    "bulk-extractor-raw",
    "bulk-extractor-recovered",
    "pdf-extract",
    "recoll-index",
    "extract-winmem",
    "volatility3",
    "plaso-timeline",
}

DIR_COUNTER_PREFIXES = (
    "carve-",
    "photorec-",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_dir_counter_stage(stage: str) -> bool:
    return stage in DIR_COUNTER_STAGES or any(
        stage.startswith(prefix) for prefix in DIR_COUNTER_PREFIXES
    )


def ddrescue_rescued_bytes(map_path: str | os.PathLike[str]) -> int | None:
    """Return rescued bytes from a GNU ddrescue mapfile, or None if unavailable."""
    path = Path(map_path)
    if not path.is_file():
        return None
    total = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 3 or parts[2] != "+":
                    continue
                try:
                    total += int(parts[1], 0)
                except ValueError:
                    continue
    except OSError:
        return None
    return total


def directory_work_counter(root: str | os.PathLike[str]) -> int | None:
    """Monotonic-ish work counter: total file bytes plus file count."""
    path = Path(root)
    if not path.exists():
        return None
    total = 0
    count = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for name in filenames:
                full = Path(dirpath) / name
                try:
                    st = full.stat()
                except OSError:
                    continue
                if not full.is_file():
                    continue
                total += st.st_size
                count += 1
    except OSError:
        return None
    return total + count


def max_mtime_counter(root: str | os.PathLike[str]) -> int | None:
    """Return latest mtime_ns under a path, including the root itself."""
    path = Path(root)
    if not path.exists():
        return None
    latest = 0
    try:
        latest = max(latest, path.stat().st_mtime_ns)
        if path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                for name in [*dirnames, *filenames]:
                    try:
                        latest = max(latest, (Path(dirpath) / name).stat().st_mtime_ns)
                    except OSError:
                        continue
    except OSError:
        return None
    return latest or None


def table_row_count(db_path: str, table: str) -> int | None:
    """Return COUNT(*) for a whitelisted table name."""
    allowed = set(TABLE_COUNT_STAGES.values()) | {"files", "recovered_artifacts"}
    if table not in allowed:
        raise ValueError(f"unsupported progress table: {table}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


@dataclass(frozen=True)
class RunningScanRun:
    run_id: int
    output_dir: str
    log_path: str


def latest_running_scan_run(db_path: str, stage: str) -> RunningScanRun | None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT id, COALESCE(output_dir,''), COALESCE(log_path,'') "
                "FROM scan_runs WHERE stage=? AND status='running' "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (stage,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return RunningScanRun(int(row[0]), row[1] or "", row[2] or "")


def touch_scan_run(db_path: str, run_id: int, *, progress: bool = False) -> None:
    now = _utc_now()
    try:
        conn = sqlite3.connect(db_path)
        try:
            if progress:
                conn.execute(
                    "UPDATE scan_runs "
                    "SET heartbeat_at=?, last_progress_at=? "
                    "WHERE id=? AND status='running'",
                    (now, now, run_id),
                )
            else:
                conn.execute(
                    "UPDATE scan_runs SET heartbeat_at=? "
                    "WHERE id=? AND status='running'",
                    (now, run_id),
                )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return


@dataclass
class StageProgressProbe:
    db_path: str
    stage: str
    map_path: str = ""
    min_db_update_interval: float = 30.0

    _run_id: int | None = None
    _last_heartbeat_write: float = 0.0
    _last_progress_write: float = 0.0

    def __call__(self) -> int | None:
        run = latest_running_scan_run(self.db_path, self.stage)
        if run:
            self._run_id = run.run_id
            now = time.monotonic()
            if now - self._last_heartbeat_write >= self.min_db_update_interval:
                touch_scan_run(self.db_path, run.run_id, progress=False)
                self._last_heartbeat_write = now
        elif not self.stage.startswith("ddrescue-"):
            return None

        values: list[int] = []
        if self.stage.startswith("ddrescue-") and self.map_path:
            rescued = ddrescue_rescued_bytes(self.map_path)
            if rescued is not None:
                values.append(rescued)

        if self.stage in TABLE_COUNT_STAGES:
            count = table_row_count(self.db_path, TABLE_COUNT_STAGES[self.stage])
            if count is not None:
                values.append(count)

        if run and run.output_dir:
            if _is_dir_counter_stage(self.stage):
                dir_count = directory_work_counter(run.output_dir)
                if dir_count is not None:
                    values.append(dir_count)
            elif not values:
                mtime = max_mtime_counter(run.output_dir)
                if mtime is not None:
                    values.append(mtime)

        if run and run.log_path and not values and self.stage not in {
            "bulk-extractor-raw",
            "bulk-extractor-recovered",
        }:
            mtime = max_mtime_counter(run.log_path)
            if mtime is not None:
                values.append(mtime)

        if not values:
            return None
        return sum(values)

    def mark_progress(self, _value: float) -> None:
        run_id = self._run_id
        if run_id is None:
            run = latest_running_scan_run(self.db_path, self.stage)
            run_id = run.run_id if run else None
        if run_id is not None:
            now = time.monotonic()
            if now - self._last_progress_write >= self.min_db_update_interval:
                touch_scan_run(self.db_path, run_id, progress=True)
                self._last_progress_write = now


def build_stage_progress_probe(
    db_path: str,
    stage: str | None,
    *,
    map_path: str = "",
) -> StageProgressProbe | None:
    if not db_path or not stage:
        return None
    return StageProgressProbe(db_path=db_path, stage=stage, map_path=map_path)
