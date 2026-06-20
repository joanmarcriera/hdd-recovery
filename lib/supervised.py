"""Durable records for detached web/queue supervisor processes."""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import socket
from dataclasses import dataclass
from datetime import datetime, timezone

from lib.runs import pid_alive

SUPERVISED_MARKERS = ("image-pipeline.py", "image-queue.py", "hdd-recovery")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _host() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


SCHEMA = """
CREATE TABLE IF NOT EXISTS supervised_runs (
  id INTEGER PRIMARY KEY,
  run_kind TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  command_line TEXT,
  log_path TEXT,
  pid INTEGER,
  pgid INTEGER,
  host TEXT,
  heartbeat_at TEXT,
  last_progress_at TEXT,
  cancel_requested INTEGER DEFAULT 0,
  exit_code INTEGER,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_supervised_status
  ON supervised_runs(status, run_kind, started_at);
"""


@dataclass(frozen=True)
class SupervisedRun:
    db_path: str
    run_id: int
    run_kind: str
    status: str
    pid: int | None
    log_path: str
    command_line: str
    started_at: str
    heartbeat_at: str
    last_progress_at: str
    cancel_requested: bool


def ensure_supervised_schema(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def create_supervised_run(
    db_path: str,
    run_kind: str,
    command_line: str,
    log_path: str,
    *,
    notes: str = "",
) -> int:
    ensure_supervised_schema(db_path)
    now = _now()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO supervised_runs(
              run_kind,status,started_at,command_line,log_path,host,
              heartbeat_at,last_progress_at,notes
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (run_kind, "starting", now, command_line, log_path, _host(), now, now, notes),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def set_supervised_process(db_path: str, run_id: int, pid: int) -> None:
    ensure_supervised_schema(db_path)
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    now = _now()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE supervised_runs
            SET status='running', pid=?, pgid=?, heartbeat_at=?
            WHERE id=? AND status IN ('starting','running')
            """,
            (pid, pgid, now, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_supervised_heartbeat(
    db_path: str,
    run_id: int,
    *,
    progress: bool = False,
) -> None:
    ensure_supervised_schema(db_path)
    now = _now()
    conn = sqlite3.connect(db_path)
    try:
        if progress:
            conn.execute(
                """
                UPDATE supervised_runs
                SET heartbeat_at=?, last_progress_at=?
                WHERE id=? AND status IN ('starting','running')
                """,
                (now, now, run_id),
            )
        else:
            conn.execute(
                """
                UPDATE supervised_runs
                SET heartbeat_at=?
                WHERE id=? AND status IN ('starting','running')
                """,
                (now, run_id),
            )
        conn.commit()
    finally:
        conn.close()


def finish_supervised_run(
    db_path: str,
    run_id: int,
    status: str,
    *,
    exit_code: int | None = None,
    notes: str = "",
) -> None:
    ensure_supervised_schema(db_path)
    now = _now()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE supervised_runs
            SET status=?, ended_at=?, heartbeat_at=?, exit_code=?,
                notes=TRIM(COALESCE(notes,'') || ?)
            WHERE id=?
            """,
            (status, now, now, exit_code, f" {notes}" if notes else "", run_id),
        )
        conn.commit()
    finally:
        conn.close()


def cancel_supervised_run(db_path: str, run_id: int) -> bool:
    """Request cancellation and SIGTERM the recorded process group if present."""
    ensure_supervised_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT pid,pgid FROM supervised_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        now = _now()
        conn.execute(
            """
            UPDATE supervised_runs
            SET cancel_requested=1, heartbeat_at=?,
                notes=TRIM(COALESCE(notes,'') || ?)
            WHERE id=?
            """,
            (now, f" [cancel requested at {now}]", run_id),
        )
        conn.commit()
    finally:
        conn.close()
    if not row:
        return False
    pid, pgid = row
    target = pgid or pid
    if not target:
        return False
    try:
        if pgid:
            os.killpg(int(pgid), signal.SIGTERM)
        else:
            os.kill(int(pid), signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def request_supervised_stop(
    db_path: str,
    run_id: int,
    *,
    reason: str = "stop after current stage",
) -> bool:
    """Request a graceful stop without terminating the running process group."""
    ensure_supervised_schema(db_path)
    now = _now()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE supervised_runs
            SET cancel_requested=1, heartbeat_at=?,
                notes=TRIM(COALESCE(notes,'') || ?)
            WHERE id=? AND status IN ('starting','running')
            """,
            (now, f" [{reason} requested at {now}]", run_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _row_to_run(db_path: str, row) -> SupervisedRun:
    return SupervisedRun(
        db_path=db_path,
        run_id=int(row["id"]),
        run_kind=row["run_kind"],
        status=row["status"],
        pid=int(row["pid"]) if row["pid"] is not None else None,
        log_path=row["log_path"] or "",
        command_line=row["command_line"] or "",
        started_at=row["started_at"] or "",
        heartbeat_at=row["heartbeat_at"] or "",
        last_progress_at=row["last_progress_at"] or "",
        cancel_requested=bool(row["cancel_requested"]),
    )


def reconcile_supervised_runs(
    db_path: str,
    *,
    markers=SUPERVISED_MARKERS,
) -> int:
    ensure_supervised_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id,pid FROM supervised_runs "
            "WHERE status IN ('starting','running') AND pid IS NOT NULL"
        ).fetchall()
        now = _now()
        n = 0
        for run_id, pid in rows:
            if pid_alive(pid, markers):
                continue
            conn.execute(
                """
                UPDATE supervised_runs
                SET status='interrupted', ended_at=?, heartbeat_at=?,
                    notes=TRIM(COALESCE(notes,'') || ?)
                WHERE id=? AND status IN ('starting','running')
                """,
                (now, now, f" [reconciled: pid {pid} not alive at {now}]", run_id),
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def active_supervised_runs(
    db_path: str,
    *,
    run_kind: str | None = None,
    reconcile: bool = True,
) -> list[SupervisedRun]:
    if reconcile:
        reconcile_supervised_runs(db_path)
    ensure_supervised_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT * FROM supervised_runs "
            "WHERE status IN ('starting','running') "
        )
        params: list[str] = []
        if run_kind:
            sql += "AND run_kind=? "
            params.append(run_kind)
        sql += "ORDER BY started_at DESC, id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_run(db_path, row) for row in rows]
    finally:
        conn.close()


def encode_env(runs: list[tuple[str, int]]) -> str:
    return json.dumps([[db, run_id] for db, run_id in runs])


def decode_env(value: str | None = None) -> list[tuple[str, int]]:
    raw = value if value is not None else os.environ.get("SUPERVISED_RUNS", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[tuple[str, int]] = []
    for item in data:
        if not isinstance(item, list) or len(item) != 2:
            continue
        db, run_id = item
        try:
            out.append((str(db), int(run_id)))
        except (TypeError, ValueError):
            continue
    return out


def env_stop_requested(value: str | None = None) -> bool:
    """Return True if any supervised run from SUPERVISED_RUNS requested stop."""
    for db_path, run_id in decode_env(value):
        ensure_supervised_schema(db_path)
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT cancel_requested FROM supervised_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        if row and int(row[0] or 0):
            return True
    return False


def heartbeat_env_runs(*, progress: bool = False) -> None:
    for db_path, run_id in decode_env():
        update_supervised_heartbeat(db_path, run_id, progress=progress)


def finish_env_runs(status: str, *, exit_code: int | None = None, notes: str = "") -> None:
    if os.environ.get("SUPERVISED_FINISH_DISABLED") == "1":
        return
    for db_path, run_id in decode_env():
        finish_supervised_run(db_path, run_id, status, exit_code=exit_code, notes=notes)
