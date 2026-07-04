"""Provenance + resumability for the external-service exporters.

Both ``bin/immich-export.py`` and ``bin/docspell-export.py`` record every file
they successfully push into the per-image ``exports`` table, keyed by a stable
``source_ref`` (the artifact's sha256, or a ``<disk>:<id>`` fallback). Re-running
an exporter then skips anything already recorded — resumable like
``image-tag-photos.py``, and independent of whether the remote service also
dedupes.
"""
from __future__ import annotations

from lib.timestamp import utc_now

# Catalogs created before the `exports` table was added to analysis-schema.sql
# would raise "no such table: exports" on the first record_export, aborting a
# disk mid-export. The exporters pass this to open_writable_db (idempotent) so
# provenance always has somewhere to land.
EXPORTS_DDL = """
CREATE TABLE IF NOT EXISTS exports (
  id INTEGER PRIMARY KEY,
  source_kind TEXT NOT NULL,
  source_ref TEXT,
  relative_path TEXT NOT NULL,
  full_path TEXT NOT NULL,
  sha256 TEXT,
  size_bytes INTEGER,
  created_at TEXT NOT NULL,
  notes TEXT
);
"""


def already_exported(conn, source_kind, source_ref):
    """True if this artifact was already pushed by this exporter."""
    if not source_ref:
        return False
    row = conn.execute(
        "SELECT 1 FROM exports WHERE source_kind=? AND source_ref=? LIMIT 1",
        (source_kind, source_ref),
    ).fetchone()
    return row is not None


def exported_refs(conn, source_kind):
    """Set of source_refs already pushed by this exporter (one query, for speed)."""
    try:
        return {r[0] for r in conn.execute(
            "SELECT source_ref FROM exports WHERE source_kind=? AND source_ref IS NOT NULL",
            (source_kind,)).fetchall()}
    except Exception:
        return set()


def record_export(conn, source_kind, source_ref, relative_path, full_path,
                  sha256=None, size_bytes=None, notes=""):
    """Insert an ``exports`` provenance row and commit."""
    conn.execute(
        "INSERT INTO exports(source_kind, source_ref, relative_path, full_path, "
        "sha256, size_bytes, created_at, notes) VALUES(?,?,?,?,?,?,?,?)",
        (source_kind, source_ref, relative_path or "", full_path or "",
         sha256, size_bytes, utc_now(), notes),
    )
    conn.commit()
