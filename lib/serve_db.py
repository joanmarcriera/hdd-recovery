"""Read-only SQLite and database-discovery helpers for the review UI.

Extracted from bin/image-serve.py (#18). These helpers deliberately do not
write to recovery databases: UI SQL is restricted to SELECT/WITH and the DB
connections use SQLite read-only URI mode.
"""
from __future__ import annotations

import os
import re
import sqlite3


def safe_sql(sql):
    """Return a read-only SQL statement or raise ValueError."""
    stripped = sql.strip().lstrip(";").strip()
    upper = stripped.upper()
    if not re.match(r"\s*(SELECT|WITH)\b", upper):
        raise ValueError("Only SELECT and WITH queries are allowed.")
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX"):
        if re.search(r"\b" + bad + r"\b", upper):
            raise ValueError(f"Disallowed keyword: {bad}")
    return stripped


def run_query(db_path, sql, params=(), limit=5000):
    """Run a read-only query and return (column_names, rows)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchmany(limit)
        cols = [d[0] for d in cur.description or []]
        return cols, rows
    finally:
        conn.close()


def query_scalar(db_path, sql, default=None):
    """Run a read-only scalar query, returning default on missing rows/errors."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(sql).fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def db_export_root(db_path):
    return query_scalar(db_path, "SELECT export_root FROM image_info WHERE id=1", "") or ""


# Analysis DBs live beside images (e.g. <root>/images/*.analysis.sqlite). These
# subdirectories never contain DBs but can hold millions of carved/feature files
# on real recovery trees, so discovery prunes them and applies a depth cap.
_DISCOVERY_PRUNE = {
    "exports", "recovered", "indexes", "winmem", "photorec", "carved",
    "hits", "state", "reports", "logs", "manifests", "structure",
}
# Stale backup copies of DBs live under directories like
# ``_rsync_conflict_backups_20260507-150519/`` (created by rsync --backup-dir
# during the bind-mount/ENOSPC incidents). A copied DB still points its
# image_info at the *real* image/export root, so if it were discovered it would
# show as a phantom duplicate in the UI and — worse — get enqueued and re-run
# the pipeline into the genuine export tree. Prune these dirs by name prefix.
_DISCOVERY_PRUNE_PREFIXES = (
    "_rsync_conflict_backups",
    ".rsync_conflict_backups",
)
_DISCOVERY_MAX_DEPTH = 4


def _is_pruned_dir(name):
    lname = name.lower()
    if lname in _DISCOVERY_PRUNE or name.startswith("."):
        return True
    return any(lname.startswith(p) for p in _DISCOVERY_PRUNE_PREFIXES)


def find_databases(root, max_depth=_DISCOVERY_MAX_DEPTH):
    root = os.path.abspath(root)
    base = root.rstrip(os.sep).count(os.sep)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.count(os.sep) - base
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if not _is_pruned_dir(d)]
        for fn in filenames:
            if fn.endswith(".analysis.sqlite"):
                found.append(os.path.join(dirpath, fn))
    return sorted(set(found))
