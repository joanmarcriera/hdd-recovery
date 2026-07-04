"""Shared cross-image harvest predicates for the consolidation/export tools.

The cross-image inventory (``bin/inventory-summary.py`` + ``/inventory``), the
Immich photo exporter (``bin/immich-export.py``), and the Docspell document
exporter (``bin/docspell-export.py``) must all agree on *what counts* as a
"curated real photo" versus a "document". That single definition lives here so
the counts a report shows can never drift from the files an exporter actually
ships.

All access is read-only (``mode=ro`` via :func:`lib.db.ro_db`); nothing here
writes to a recovery DB or touches source media. Physical files are read from
``recovered_artifacts.full_path`` — the carved/PhotoRec outputs that already
exist on disk.

Curated photos mirror the ``/gallery`` "Groups" query in ``lib/serve_gallery.py``
(dedup primaries only) and add a size floor + optional quality gate, restricted
to real camera/photo formats (jpeg/png/heic/heif/tiff/webp) — gif/bmp/ico are
treated as web-cache noise and excluded.
"""
from __future__ import annotations

import os

from lib.db import ro_db
from lib.serve_db import find_databases, query_scalar

# ── curated-set definitions (the single source of truth) ─────────────────────

# Real photo/camera formats. Deliberately excludes gif/bmp/ico (web-cache noise).
PHOTO_MIMES = (
    "image/jpeg", "image/png", "image/heic", "image/heif",
    "image/tiff", "image/webp",
)
PHOTO_EXTS = ("jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "webp")
PHOTO_MIN_SIZE = 20_000        # bytes; below this is almost always a thumbnail/icon
PHOTO_MIN_QUALITY = 30.0       # recovered_artifacts.quality_score gate (NULL passes)

# Document formats. text/plain is opt-in (carving produces huge volumes of noisy
# text fragments) — see ``include_text`` below.
DOC_MIMES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/rtf",
    "text/rtf",
    "application/epub+zip",
)
DOC_EXTS = ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
            "odt", "ods", "odp", "rtf", "epub")
DOC_MIN_SIZE = 4_096           # bytes; drop trivial fragments


def _in_list(col, values):
    """Return (sql_fragment, params) for a case-insensitive ``lower(col) IN (...)``."""
    placeholders = ",".join("?" for _ in values)
    return f"lower(COALESCE({col},'')) IN ({placeholders})", list(values)


def iter_databases(root):
    """All ``*.analysis.sqlite`` catalogs under ``root`` (reuses the UI's pruning)."""
    return find_databases(root)


def db_disk_label(db_path):
    """Human/album/tag label for a disk: the image basename without ``.img``.

    Falls back to the DB filename stem so a catalog with no ``image_info`` row
    still yields a stable, non-empty label.
    """
    base = query_scalar(db_path, "SELECT image_basename FROM image_info WHERE id=1", "") or ""
    if not base:
        name = os.path.basename(db_path)
        base = name[:-len(".analysis.sqlite")] if name.endswith(".analysis.sqlite") else name
    for suffix in (".img", ".dd", ".raw"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base or "disk"


# ── curated photos ───────────────────────────────────────────────────────────

def _photo_where(min_size, min_quality):
    mime_sql, params = _in_list("ra.mime_type", PHOTO_MIMES)
    ext_sql, ext_params = _in_list("ra.trid_top_ext", PHOTO_EXTS)
    params += ext_params
    where = (
        f"({mime_sql} OR {ext_sql}) "
        "AND COALESCE(ra.size_bytes,0) >= ? "
        "AND (ra.dedup_cluster_id IS NULL OR ra.is_cluster_primary = 1) "
        "AND (ra.quality_score IS NULL OR ra.quality_score >= ?)"
    )
    params += [min_size, min_quality]
    return where, params


def count_photos(db_path, min_size=PHOTO_MIN_SIZE, min_quality=PHOTO_MIN_QUALITY):
    """Count curated photos in one catalog (0 if the table is absent)."""
    where, params = _photo_where(min_size, min_quality)
    try:
        with ro_db(db_path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM recovered_artifacts ra WHERE {where}", params
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def curated_photos(db_path, min_size=PHOTO_MIN_SIZE, min_quality=PHOTO_MIN_QUALITY):
    """Curated real photos as a list of dict rows (physical files on disk).

    Each row carries ``id, full_path, relative_path, size_bytes, sha256,
    mime_type, method, quality_score`` plus ``taken_at`` (from the exiftool
    finding, when present) and ``description`` (llava), so an exporter can set a
    creation date / caption without re-reading the file.
    """
    where, params = _photo_where(min_size, min_quality)
    sql = f"""
        SELECT ra.id, ra.full_path, ra.relative_path, ra.size_bytes, ra.sha256,
               ra.mime_type, ra.method, ra.quality_score,
               ft.value AS taken_at,
               fd.value AS description
        FROM recovered_artifacts ra
        LEFT JOIN findings ft ON ft.artifact_id = ra.id
             AND ft.source_tool = 'exiftool' AND ft.key = 'taken_at'
        LEFT JOIN findings fd ON fd.artifact_id = ra.id
             AND fd.source_tool = 'llava' AND fd.key = 'description'
        WHERE {where}
        ORDER BY ra.size_bytes DESC
    """
    # A catalog predating the findings table would make the JOINs fail; fall
    # back to a join-free query so older DBs still yield their photos.
    try:
        with ro_db(db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return _photos_no_findings(db_path, where, params)


def _photos_no_findings(db_path, where, params):
    sql = f"""
        SELECT ra.id, ra.full_path, ra.relative_path, ra.size_bytes, ra.sha256,
               ra.mime_type, ra.method, ra.quality_score,
               NULL AS taken_at, NULL AS description
        FROM recovered_artifacts ra
        WHERE {where}
        ORDER BY ra.size_bytes DESC
    """
    try:
        with ro_db(db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []


# ── curated documents ────────────────────────────────────────────────────────

def _doc_where(min_size, include_text):
    mimes = list(DOC_MIMES) + (["text/plain"] if include_text else [])
    exts = list(DOC_EXTS) + (["txt"] if include_text else [])
    mime_sql, params = _in_list("ra.mime_type", mimes)
    ext_sql, ext_params = _in_list("ra.trid_top_ext", exts)
    params += ext_params
    where = (
        f"({mime_sql} OR {ext_sql}) "
        "AND COALESCE(ra.size_bytes,0) >= ?"
    )
    params.append(min_size)
    return where, params


def count_docs(db_path, min_size=DOC_MIN_SIZE, include_text=False):
    """Count curated documents in one catalog (0 if the table is absent)."""
    where, params = _doc_where(min_size, include_text)
    try:
        with ro_db(db_path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM recovered_artifacts ra WHERE {where}", params
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def curated_docs(db_path, min_size=DOC_MIN_SIZE, include_text=False):
    """Curated documents as a list of dict rows (physical files on disk).

    Each row carries ``id, full_path, relative_path, size_bytes, sha256,
    mime_type, trid_top_ext, method, file_output``.
    """
    where, params = _doc_where(min_size, include_text)
    sql = f"""
        SELECT ra.id, ra.full_path, ra.relative_path, ra.size_bytes, ra.sha256,
               ra.mime_type, ra.trid_top_ext, ra.method, ra.file_output
        FROM recovered_artifacts ra
        WHERE {where}
        ORDER BY ra.size_bytes DESC
    """
    try:
        with ro_db(db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []
