"""Durable run-supervision helpers shared by the pipeline, queue, and web UI.

Reconciliation: a `scan_runs` row left `status='running'` by a kill, crash, or
container restart is corrected to `interrupted` once its owning process is
provably gone. Rows without a recorded `pid` (pre-supervision) are left
untouched for human review — consistent with the project's "leave the DB for
human review" rule; we only correct rows we can prove are dead.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

# cmdline fingerprints of our stage processes; used to reject PID reuse by an
# unrelated process that happens to inherit a recycled PID.
STAGE_MARKERS = ("image-", "hdd-recovery")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pid_alive(pid, markers=STAGE_MARKERS) -> bool:
    """True if `pid` exists and — where /proc is available — still looks like one
    of our stage processes. Returns True on platforms without /proc (can't
    refute liveness there, so stay conservative and don't reconcile)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # exists, owned by another user
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except FileNotFoundError:
        return False           # /proc present but the pid vanished
    except OSError:
        return True            # no /proc (non-Linux) — can't refute; assume alive
    return any(m in cmd for m in markers)


def reconcile_running(db_path: str, markers=STAGE_MARKERS) -> int:
    """Mark dead 'running' rows (recorded pid is gone) as 'interrupted'.
    Returns the number of rows reconciled. No-ops on a DB without the supervision
    columns (older schema)."""
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_runs)")}
        if "pid" not in cols:
            return 0
        rows = conn.execute(
            "SELECT id, pid FROM scan_runs "
            "WHERE status='running' AND pid IS NOT NULL"
        ).fetchall()
        now = _now()
        n = 0
        for run_id, pid in rows:
            if pid_alive(pid, markers):
                continue
            conn.execute(
                "UPDATE scan_runs SET status='interrupted', ended_at=?, "
                "notes=TRIM(COALESCE(notes,'') || "
                "' [reconciled: pid ' || ? || ' not alive at ' || ? || ']') "
                "WHERE id=? AND status='running'",
                (now, pid, now, run_id))
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()
