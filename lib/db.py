"""Shared SQLite helpers for the recovery toolkit.

Consolidates the read-only connection boilerplate, the writable-DB pragma/DDL
bootstrap, the ``image_info`` row read, and the ``scan_runs`` start/end writes
that were independently reimplemented across the Python CLIs and lib modules.

``start_scan_run``/``end_scan_run`` write the same ``scan_runs`` columns as the
bash twins ``record_scan_start``/``record_scan_end`` in ``lib/common.sh`` (minus
the supervision columns the bash layer also sets); keep the two compatible.
``fetch_image_info`` deliberately reads the DB (the source of truth) rather than
re-deriving paths from the environment, so it cannot drift from the bash
``default_export_root``/``default_db_path`` fallbacks.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from lib.timestamp import utc_now

DEFAULT_PRAGMAS = ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON")


@contextmanager
def ro_db(db_path):
    """Yield a read-only connection (``mode=ro``, ``row_factory=Row``), auto-closed."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def open_writable_db(db_path, *, pragmas=DEFAULT_PRAGMAS, ddl=None):
    """Open a writable connection with the standard pragmas and optional DDL.

    Returns the connection; the caller owns its lifetime and must close it.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for pragma in pragmas:
        conn.execute(f"PRAGMA {pragma}")
    if ddl:
        conn.executescript(ddl)
    conn.commit()
    return conn


@dataclass(frozen=True)
class ImageInfo:
    image_path: str
    export_root: str
    ddrescue_map_path: str


def fetch_image_info(db_path) -> ImageInfo:
    """Return the single ``image_info`` row (id=1).

    Raises ``LookupError`` if the row is absent (DB not initialised).
    """
    with ro_db(db_path) as conn:
        row = conn.execute(
            "SELECT image_path, export_root, ddrescue_map_path "
            "FROM image_info WHERE id=1"
        ).fetchone()
    if row is None:
        raise LookupError(f"image_info row (id=1) missing in {db_path}")
    return ImageInfo(
        image_path=row["image_path"] or "",
        export_root=row["export_root"] or "",
        ddrescue_map_path=row["ddrescue_map_path"] or "",
    )


def start_scan_run(conn, stage, command_line, log_path="", output_dir="") -> int:
    """Insert a ``scan_runs`` row in 'running' state; return its id."""
    cur = conn.execute(
        "INSERT INTO scan_runs(stage,status,started_at,command_line,log_path,output_dir) "
        "VALUES(?,?,?,?,?,?)",
        (stage, "running", utc_now(), command_line, log_path, output_dir),
    )
    conn.commit()
    return cur.lastrowid


def end_scan_run(conn, run_id, status, notes="") -> None:
    """Mark a ``scan_runs`` row terminal with ended_at and notes."""
    conn.execute(
        "UPDATE scan_runs SET status=?, ended_at=?, notes=? WHERE id=?",
        (status, utc_now(), notes, run_id),
    )
    conn.commit()
