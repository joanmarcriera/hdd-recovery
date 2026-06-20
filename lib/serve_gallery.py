"""File export, thumbnail, and gallery helpers for the review UI.

Extracted from bin/image-serve.py (#18). These helpers serve already-created
recovery outputs and extracted files; they never modify source media.
"""
from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import sqlite3
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

from lib.serve_ui import h, page

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"

# Hard cap for the gallery "View all" mode: emitting every <img> for a DB with
# tens of thousands of carved images builds an enormous DOM and that many /thumb
# requests. Show the first N and tell the user to paginate/filter for the rest.
GALLERY_ALL_CAP = 1000

# ── file serving & image gallery ─────────────────────────────────────────────

def export_file_via_icat(db_path, file_id, root):
    """Idempotently extract a filesystem-aware file via image-export.sh.
    Returns (abs_path, mime) or (None, err). Skips re-extraction if dest already exists."""
    try:
        fid = int(file_id)
    except (TypeError, ValueError):
        return None, "invalid file_id"

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        meta = conn.execute(
            "SELECT f.path, COALESCE(image_info.export_root,'') "
            "FROM files f, image_info "
            "WHERE f.id=? AND image_info.id=1", (fid,)
        ).fetchone()
        conn.close()
    except Exception as e:
        return None, f"db error: {e}"
    if not meta:
        return None, f"file id {fid} not found"
    src_path, export_root = meta
    if not export_root:
        return None, "image_info.export_root not set"

    safe_name = os.path.basename(src_path or f"file-{fid}") or f"file-{fid}"
    dest_dir = os.path.join(export_root, "exports", "files", f"file-{fid}")
    dest_path = os.path.join(dest_dir, safe_name)

    # If already extracted with non-zero size, just serve it
    if not (os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0):
        os.makedirs(dest_dir, exist_ok=True)
        script = BIN_DIR / "image-export.sh"
        try:
            r = subprocess.run(
                [str(script), db_path, "--file-id", str(fid),
                 "--dest-dir", dest_dir],
                capture_output=True, text=True, timeout=60
            )
        except Exception as e:
            return None, f"export spawn failed: {e}"
        if r.returncode != 0:
            return None, f"image-export.sh rc={r.returncode}: {r.stderr.strip()[:200]}"

    abs_path, err = _resolve_under_root(dest_path, root)
    if abs_path is None:
        return None, err
    mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    return abs_path, mime


def _resolve_under_root(path, root):
    """Return (abs_path, None) if path is a regular file inside root, else (None, err)."""
    try:
        abs_path = os.path.realpath(path)
        abs_root = os.path.realpath(root)
        if not abs_path.startswith(abs_root + os.sep):
            return None, "Forbidden: path is outside the recovery root"
        if not os.path.isfile(abs_path):
            return None, "File not found"
        return abs_path, None
    except OSError as e:
        return None, str(e)


def _thumb_cache_dir():
    """Writable directory for cached thumbnails. Override with HDD_THUMB_CACHE."""
    d = os.environ.get("HDD_THUMB_CACHE") or os.path.join(
        tempfile.gettempdir(), "hdd-thumb-cache")
    os.makedirs(d, exist_ok=True)
    return d


def make_thumbnail(abs_path, max_w=320, max_h=240):
    """Return (jpeg_bytes, etag) for a downscaled thumbnail of abs_path, cached on disk.

    Returns (None, None) if Pillow is unavailable or the file is not a decodable image,
    so callers can fall back to serving the original.
    """
    try:
        st = os.stat(abs_path)
    except OSError:
        return None, None
    key = hashlib.sha1(
        f"{abs_path}|{st.st_mtime_ns}|{st.st_size}|{max_w}x{max_h}".encode()
    ).hexdigest()
    cache_path = os.path.join(_thumb_cache_dir(), key + ".jpg")
    etag = f'"{key}"'
    try:
        if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
            with open(cache_path, "rb") as fh:
                return fh.read(), etag
    except OSError:
        pass
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None, None
    try:
        with Image.open(abs_path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((max_w, max_h))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=78, optimize=True)
        data = buf.getvalue()
    except Exception:
        return None, None
    try:  # write-then-rename so concurrent readers never see a partial file
        tmp = cache_path + f".{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, cache_path)
    except OSError:
        pass
    return data, etag


_FS_SORT_COLS = {
    "score":  ("pc.score",       "DESC"),
    "size":   ("f.size_bytes",   "DESC"),
    "date":   ("pc.taken_at",    "ASC"),
    "name":   ("f.name",         "ASC"),
    "camera": ("pc.camera_model","ASC"),
}


def page_gallery_fs(db_path, pg=0, per_page=48, all_images=False,
                    sort="score", order="", camera="", min_score=0, year=""):
    """Gallery for filesystem-aware picture candidates with sort + filter."""
    enc = urllib.parse.quote(db_path)

    # Validate sort / order
    sort = sort if sort in _FS_SORT_COLS else "score"
    default_order = _FS_SORT_COLS[sort][1]
    order = order.upper() if order.upper() in ("ASC", "DESC") else default_order
    order_sql = f"{_FS_SORT_COLS[sort][0]} {order}"

    # Build WHERE extras from filters
    where_extra = ["f.size_bytes IS NOT NULL", "f.size_bytes > 0"]
    params: list = []
    if camera:
        where_extra.append("pc.camera_model = ?")
        params.append(camera)
    if year:
        where_extra.append("pc.taken_at LIKE ?")
        params.append(f"{year}%")
    if min_score:
        where_extra.append("pc.score >= ?")
        params.append(min_score)
    where_clause = " AND ".join(where_extra)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # Populate filter dropdowns
        cameras = [r[0] for r in conn.execute(
            "SELECT DISTINCT camera_model FROM picture_candidates "
            "WHERE camera_model IS NOT NULL AND camera_model != '' ORDER BY camera_model"
        ).fetchall()]
        years = [r[0][:4] for r in conn.execute(
            "SELECT DISTINCT taken_at FROM picture_candidates "
            "WHERE taken_at IS NOT NULL AND taken_at != '' ORDER BY taken_at"
        ).fetchall() if r[0] and len(r[0]) >= 4]
        years = sorted(set(years), reverse=True)

        count_sql = (f"SELECT COUNT(*) FROM picture_candidates pc "
                     f"JOIN files f ON f.id = pc.file_id WHERE {where_clause}")
        total = conn.execute(count_sql, params).fetchone()[0]

        base_sql = (f"SELECT f.id AS file_id, f.name, f.path, f.size_bytes, "
                    f"pc.score, pc.camera_model, pc.taken_at "
                    f"FROM picture_candidates pc "
                    f"JOIN files f ON f.id = pc.file_id "
                    f"WHERE {where_clause} "
                    f"ORDER BY {order_sql}, f.path")

        if all_images:
            rows = conn.execute(base_sql + " LIMIT ?",
                                [*params, GALLERY_ALL_CAP]).fetchall()
        else:
            offset = pg * per_page
            rows = conn.execute(base_sql + " LIMIT ? OFFSET ?",
                                [*params, per_page, offset]).fetchall()
        conn.close()
    except Exception as e:
        return page("Image Gallery (filesystem)",
                    f'<div class="panel err">{h(str(e))}</div>',
                    db_name=db_path, nav_extra=" &rsaquo; gallery (fs)")

    # Query strings preserve active filters while letting links override values.
    def qs_filter(**overrides):
        params = {
            "db": db_path,
            "mode": "fs",
            "sort": sort,
            "order": order,
        }
        if camera:
            params["camera"] = camera
        if year:
            params["year"] = year
        if min_score:
            params["min_score"] = str(min_score)
        for key, value in overrides.items():
            if value is None or value == "" or value is False:
                params.pop(key, None)
            else:
                params[key] = str(value)
        return urllib.parse.urlencode(params)

    imgs = ""
    for r in rows:
        fid  = r["file_id"]
        sz   = r["size_bytes"] or 0
        name = r["name"] or f"file-{fid}"
        cam  = r["camera_model"] or ""
        taken = r["taken_at"] or ""
        score = r["score"] or 0
        meta = " — ".join(filter(None, [name, f"{sz:,} B", f"score {score}", cam, taken]))
        url  = f"/export_view?db={enc}&file_id={fid}"
        caption = " — ".join(filter(None, [cam, taken[:10] if taken else "", f"{sz//1024:,} KB"]))
        imgs += (
            f'<div style="display:inline-block;text-align:center;vertical-align:top">'
            f'<a href="{url}" target="_blank" class="img-card" title="{h(meta)}">'
            f'<img src="{url}&thumb=1" loading="lazy" decoding="async" '
            f'style="width:150px;height:112px;object-fit:cover;border-radius:4px 4px 0 0;'
            f'border:1px solid #333;border-bottom:none;background:#111"></a>'
            f'<div style="width:150px;font-size:10px;color:var(--sub);background:#0d1117;'
            f'border:1px solid #333;border-top:none;border-radius:0 0 4px 4px;'
            f'padding:2px 3px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis"'
            f' title="{h(caption)}">{h(caption) or "&nbsp;"}</div>'
            f'</div>'
        )

    # Sort bar
    def sort_link(col, label):
        if sort == col:
            new_order = "ASC" if order == "DESC" else "DESC"
        else:
            new_order = _FS_SORT_COLS[col][1]
        indicator = (" &#9650;" if order == "ASC" else " &#9660;") if sort == col else ""
        href = f"/gallery?{qs_filter(sort=col, order=new_order, page=None, all=None)}"
        style = "color:#7eb8f7;font-weight:bold" if sort == col else "color:var(--sub)"
        return f'<a href="{href}" style="{style}">{label}{indicator}</a>'

    sort_bar = (" &nbsp;|&nbsp; ".join([
        sort_link("score",  "Score"),
        sort_link("size",   "Size"),
        sort_link("date",   "Date"),
        sort_link("name",   "Name"),
        sort_link("camera", "Camera"),
    ]))

    # Filter form
    cam_opts = '<option value="">All cameras</option>' + "".join(
        f'<option value="{h(c)}"{"selected" if c == camera else ""}>{h(c)}</option>'
        for c in cameras)
    yr_opts = '<option value="">All years</option>' + "".join(
        f'<option value="{y}"{"selected" if y == year else ""}>{y}</option>'
        for y in years)
    filter_form = f"""<form method="get" action="/gallery"
        style="display:inline-flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px">
      <input type="hidden" name="db" value="{h(db_path)}">
      <input type="hidden" name="mode" value="fs">
      {"".join(f'<input type="hidden" name="{k}" value="{h(v)}">' for k,v in [("sort",sort),("order",order)] if v)}
      <select name="camera" style="font-size:12px">{cam_opts}</select>
      <select name="year"   style="font-size:12px">{yr_opts}</select>
      <input type="number" name="min_score" value="{min_score or ''}" placeholder="Min score"
             style="width:90px;font-size:12px">
      <button type="submit" style="font-size:12px">Filter</button>
      {"<a href='/gallery?" + qs_filter(camera=None, year=None, min_score=None, page=None, all=None) + "' style='color:var(--sub);font-size:12px'>Clear</a>" if (camera or year or min_score) else ""}
    </form>"""

    btn = 'style="background:#0f3460;padding:3px 12px;border-radius:3px;color:#7eb8f7"'
    if all_images:
        shown = min(total, GALLERY_ALL_CAP)
        capped = (f' — showing first {GALLERY_ALL_CAP:,}; paginate or filter for the rest'
                  if total > GALLERY_ALL_CAP else ' — all on one page')
        header = f'<p class="count">{total:,} candidate(s){capped}</p>'
        toggle = f'<a href="/gallery?{qs_filter(all=None, page=None)}" {btn}>&#9660; Paginated view</a>'
        pager = ""
    else:
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = pg * per_page
        prev_link = (f'<a href="/gallery?{qs_filter(page=pg-1)}">&larr; Prev</a>'
                     if pg > 0 else "")
        next_link = (f'<a href="/gallery?{qs_filter(page=pg+1)}">Next &rarr;</a>'
                     if (offset + per_page) < total else "")
        pager = " &nbsp; ".join(filter(None, [prev_link, next_link]))
        header = (f'<p class="count">{total:,} candidate(s) &mdash; '
                  f'page {pg+1} of {total_pages} &nbsp; ({per_page} per page)</p>')
        toggle = f'<a href="/gallery?{qs_filter(all=1, page=None)}" {btn}>&#9651; View all</a>'

    note = ('<p class="count" style="margin-top:4px">Thumbnails extracted on demand via '
            '<code>icat</code>; cached under <code>exports/files/</code>.</p>')

    active_filters = ", ".join(filter(None, [
        f"camera: {camera}" if camera else "",
        f"year: {year}" if year else "",
        f"score ≥ {min_score}" if min_score else "",
    ]))
    filter_badge = (f' &nbsp; <span class="badge partial">{h(active_filters)}</span>'
                    if active_filters else "")

    body = f"""
    <div class="panel">
      {header}{filter_badge}{note}
      <p style="margin-top:6px;display:flex;flex-wrap:wrap;align-items:center;gap:10px">
        {toggle}
        <span style="font-size:12px;color:var(--sub)">Sort:&nbsp;{sort_bar}</span>
      </p>
      {filter_form}
    </div>
    <div class="panel">
      <div style="display:flex;flex-wrap:wrap;gap:6px">{imgs or '<p class="count">No candidates match.</p>'}</div>
    </div>
    {"<div class='panel'><p>" + pager + "</p></div>" if pager else ""}
    """
    return page("Image Gallery (filesystem)", body, db_name=db_path,
                nav_extra=" &rsaquo; gallery (fs)")


_CARVED_SORT_COLS = {
    "size":   ("ra.size_bytes", "DESC"),
    "method": ("ra.method",     "ASC"),
    "path":   ("ra.full_path",  "ASC"),
}


def page_gallery(db_path, root, pg=0, per_page=48, all_images=False,
                 search="", sort="size", order="", method_filter="", groups=False):
    """Paginated image gallery for carved artifacts with sort, method filter,
    description search, and an optional near-duplicate "Groups" collapse (#15)."""
    enc = urllib.parse.quote(db_path)
    abs_root = os.path.realpath(root)
    search = search.strip()

    # Validate sort / order
    sort = sort if sort in _CARVED_SORT_COLS else "size"
    default_order = _CARVED_SORT_COLS[sort][1]
    order = order.upper() if order.upper() in ("ASC", "DESC") else default_order
    order_sql = f"{_CARVED_SORT_COLS[sort][0]} {order}"

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        has_findings = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='findings'"
        ).fetchone() is not None

        if has_findings:
            tagged_total = conn.execute(
                "SELECT COUNT(*) FROM recovered_artifacts ra "
                "JOIN findings f ON f.artifact_id=ra.id "
                "AND f.source_tool='llava' AND f.key='description' "
                "WHERE ra.mime_type LIKE 'image/%'"
            ).fetchone()[0]
        else:
            tagged_total = 0

        # Populate method dropdown
        methods = [r[0] for r in conn.execute(
            "SELECT DISTINCT method FROM recovered_artifacts "
            "WHERE mime_type LIKE 'image/%' AND method IS NOT NULL ORDER BY method"
        ).fetchall()]

        # Build WHERE + params
        where_parts = ["ra.mime_type LIKE 'image/%'"]
        params: list = []
        findings_join = "LEFT JOIN"
        join_sql = ""
        desc_select = "'' AS description"
        if has_findings:
            join_sql = (
                "{join_type} findings f ON f.artifact_id=ra.id "
                "AND f.source_tool='llava' AND f.key='description' "
            )
            desc_select = "f.value AS description"
        if search:
            if has_findings:
                findings_join = "JOIN"
                where_parts.append("f.value LIKE ?")
                params.append(f"%{search}%")
            else:
                where_parts.append("0")
        if method_filter:
            where_parts.append("ra.method = ?")
            params.append(method_filter)
        if groups:
            # Collapse each near-duplicate cluster to its primary; keep images
            # that were never clustered (dedup-photos not run, or unique).
            where_parts.append(
                "(ra.dedup_cluster_id IS NULL OR ra.is_cluster_primary = 1)")
        # Cluster sizes for the per-thumbnail "(n)" badge.
        cluster_sizes = dict(conn.execute(
            "SELECT dedup_cluster_id, COUNT(*) FROM recovered_artifacts "
            "WHERE dedup_cluster_id IS NOT NULL GROUP BY dedup_cluster_id").fetchall())
        where_clause = " AND ".join(where_parts)
        if join_sql:
            join_sql = join_sql.format(join_type=findings_join)

        count_sql = (
            f"SELECT COUNT(*) FROM recovered_artifacts ra "
            f"{join_sql}"
            f"WHERE {where_clause}"
        )
        total = conn.execute(count_sql, params).fetchone()[0]

        base_sql = (
            f"SELECT ra.id, ra.full_path, ra.mime_type, ra.size_bytes, ra.method, "
            f"ra.dedup_cluster_id, "
            f"{desc_select} "
            f"FROM recovered_artifacts ra "
            f"{join_sql}"
            f"WHERE {where_clause} "
            f"ORDER BY {order_sql}"
        )

        if all_images:
            rows = conn.execute(base_sql + " LIMIT ?",
                                [*params, GALLERY_ALL_CAP]).fetchall()
        else:
            offset = pg * per_page
            rows = conn.execute(base_sql + " LIMIT ? OFFSET ?",
                                [*params, per_page, offset]).fetchall()
        conn.close()
    except Exception as e:
        return page("Image Gallery", f'<div class="panel err">{h(str(e))}</div>',
                    db_name=db_path)

    def qs_filter(**overrides):
        params = {
            "db": db_path,
            "sort": sort,
            "order": order,
        }
        if search:
            params["search"] = search
        if method_filter:
            params["method"] = method_filter
        if groups:
            params["groups"] = "1"
        for key, value in overrides.items():
            if value is None or value == "" or value is False:
                params.pop(key, None)
            else:
                params[key] = str(value)
        return urllib.parse.urlencode(params)

    # Build image grid
    imgs = ""
    for row in rows:
        fp = row["full_path"] or ""
        sz = row["size_bytes"] or 0
        method = row["method"] or ""
        desc = row["description"] or ""
        if not fp or not os.path.realpath(fp).startswith(abs_root + os.sep) or not os.path.isfile(fp):
            continue
        fenc = urllib.parse.quote(fp)
        cid = row["dedup_cluster_id"]
        csize = cluster_sizes.get(cid, 0) if cid is not None else 0
        # In Groups mode each tile is a cluster primary — the caption shows how
        # many near-duplicates it represents.
        tip = f"{Path(fp).name} — {method} — {sz:,} B"
        desc_div = (f'<div class="desc">{h(desc[:200])}</div>' if desc else "")
        caption = " — ".join(filter(None, [method,
                                           f"{sz//1024:,} KB" if sz else "",
                                           f"cluster {cid} (×{csize})" if (groups and csize > 1) else ""]))
        imgs += (
            f'<div style="display:inline-block;text-align:center;vertical-align:top">'
            f'<a href="/file?path={fenc}" target="_blank" class="img-card" title="{h(tip)}">'
            f'<img src="/thumb?path={fenc}" loading="lazy" decoding="async" '
            f'style="width:150px;height:112px;object-fit:cover;border-radius:4px 4px 0 0;'
            f'border:1px solid #333;border-bottom:none;background:#111">'
            f'{desc_div}</a>'
            f'<div style="width:150px;font-size:10px;color:var(--sub);background:#0d1117;'
            f'border:1px solid #333;border-top:none;border-radius:0 0 4px 4px;'
            f'padding:2px 3px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis"'
            f' title="{h(caption)}">{h(caption) or "&nbsp;"}</div>'
            f'</div>'
        )

    # Sort bar
    def sort_link(col, label):
        if sort == col:
            new_order = "ASC" if order == "DESC" else "DESC"
        else:
            new_order = _CARVED_SORT_COLS[col][1]
        indicator = (" &#9650;" if order == "ASC" else " &#9660;") if sort == col else ""
        href = f"/gallery?{qs_filter(sort=col, order=new_order, page=None, all=None)}"
        style = "color:#7eb8f7;font-weight:bold" if sort == col else "color:var(--sub)"
        return f'<a href="{href}" style="{style}">{label}{indicator}</a>'

    sort_bar = " &nbsp;|&nbsp; ".join([
        sort_link("size",   "Size"),
        sort_link("method", "Method"),
        sort_link("path",   "Path"),
    ])

    # Method filter + description search in one form
    meth_opts = '<option value="">All methods</option>' + "".join(
        f'<option value="{h(m)}"{"selected" if m == method_filter else ""}>{h(m)}</option>'
        for m in methods)
    filter_form = f"""<form method="get" action="/gallery"
        style="display:inline-flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px">
      <input type="hidden" name="db" value="{h(db_path)}">
      {"".join(f'<input type="hidden" name="{k}" value="{h(v)}">' for k,v in [("sort",sort),("order",order)] if v)}
      <select name="method" style="font-size:12px">{meth_opts}</select>
      <input type="text" name="search" value="{h(search)}" placeholder="Search llava descriptions…"
             style="width:200px;font-size:12px"
             title="Search llava descriptions — only tagged images are searched">
      <button type="submit" style="font-size:12px">Filter</button>
      {"<a href='/gallery?" + qs_filter(search=None, method=None, page=None, all=None) + "' style='color:var(--sub);font-size:12px'>Clear</a>"
       if (search or method_filter) else ""}
    </form>"""

    btn = 'style="background:#0f3460;padding:3px 12px;border-radius:3px;color:#7eb8f7"'
    if all_images:
        capped = (f' — showing first {GALLERY_ALL_CAP:,}; paginate or filter for the rest'
                  if total > GALLERY_ALL_CAP else ' — all on one page')
        header = f'<p class="count">{total:,} image(s){capped}</p>'
        toggle = f'<a href="/gallery?{qs_filter(all=None, page=None)}" {btn}>&#9660; Paginated view</a>'
        pager = ""
    else:
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = pg * per_page
        prev_link = (f'<a href="/gallery?{qs_filter(page=pg-1)}">&larr; Prev</a>'
                     if pg > 0 else "")
        next_link = (f'<a href="/gallery?{qs_filter(page=pg+1)}">Next &rarr;</a>'
                     if (offset + per_page) < total else "")
        pager = " &nbsp; ".join(filter(None, [prev_link, next_link]))
        header = (f'<p class="count">{total:,} image(s) &mdash; '
                  f'page {pg+1} of {total_pages} &nbsp; ({per_page} per page)</p>')
        toggle = f'<a href="/gallery?{qs_filter(all=1, page=None)}" {btn}>&#9651; View all</a>'

    tag_note = (
        f'<span class="count" style="margin-left:12px">Showing {total:,} llava-matched</span>'
        if search else
        f'<span class="count" style="margin-left:12px">&#127381; {tagged_total:,} of {total:,} tagged by llava</span>'
    )

    active_filters = ", ".join(filter(None, [
        f"method: {method_filter}" if method_filter else "",
        f"desc: \"{search}\"" if search else "",
    ]))
    filter_badge = (f' &nbsp; <span class="badge partial">{h(active_filters)}</span>'
                    if active_filters else "")

    if groups:
        groups_toggle = (f'<a href="/gallery?{qs_filter(groups=None, page=None, all=None)}" '
                         f'{btn}>&#9635; Show all duplicates</a>')
    else:
        groups_toggle = (f'<a href="/gallery?{qs_filter(groups=1, page=None, all=None)}" '
                         f'{btn} title="Collapse near-duplicate clusters to one '
                         f'representative each">&#9636; Group duplicates</a>')

    body = f"""
    <div class="panel">
      {header}{tag_note}{filter_badge}
      <p style="margin-top:8px;display:flex;align-items:center;flex-wrap:wrap;gap:10px">
        {toggle}
        {groups_toggle}
        <span style="font-size:12px;color:var(--sub)">Sort:&nbsp;{sort_bar}</span>
      </p>
      {filter_form}
    </div>
    <div class="panel">
      <div style="display:flex;flex-wrap:wrap;gap:6px">{imgs or '<p class="count">No images found.</p>'}</div>
    </div>
    {"<div class='panel'><p>" + pager + "</p></div>" if pager else ""}
    """
    return page("Image Gallery", body, db_name=db_path, nav_extra=" &rsaquo; gallery")
