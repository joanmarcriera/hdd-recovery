#!/usr/bin/env python3
"""
Read-only web query interface for hdd-recovery SQLite databases.

Serves a local HTTP UI that lets you browse all recovery databases under
a given root directory.  All SQL executed is restricted to SELECT/WITH.

Usage:
  image-serve.py [--root DIR] [--port PORT] [--host HOST]
"""
import argparse, glob, html, http.server, io, json, mimetypes, os, re
import sqlite3, sys, threading, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ── helpers ──────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#1a1a2e;--panel:#16213e;--accent:#0f3460;--hi:#e94560;--txt:#eee;--sub:#aaa}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 'Fira Mono',monospace;padding:16px}
a{color:#7eb8f7;text-decoration:none} a:hover{text-decoration:underline}
h1{color:var(--hi);margin-bottom:12px;font-size:1.3em}
h2{color:#7eb8f7;margin:16px 0 8px;font-size:1.1em}
.panel{background:var(--panel);border:1px solid var(--accent);border-radius:6px;padding:14px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;font-size:12px}
th{background:var(--accent);color:var(--txt);padding:6px 8px;text-align:left}
td{padding:5px 8px;border-bottom:1px solid #2a2a4a;vertical-align:top;word-break:break-all}
tr:hover td{background:#1e2a4a}
.badge{display:inline-block;padding:2px 6px;border-radius:3px;font-size:11px;font-weight:bold}
.ok{background:#1e4620;color:#6fcf7f}
.partial{background:#4a3200;color:#f4b942}
.failed,.error{background:#4a1010;color:#e74c3c}
.running{background:#0d3b5e;color:#7eb8f7}
.pending{background:#333;color:#aaa}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start}
input[type=text],select,textarea{background:#0d1117;color:var(--txt);border:1px solid var(--accent);
  border-radius:4px;padding:6px 10px;font:13px monospace;flex:1;min-width:200px}
textarea{width:100%;min-height:80px;resize:vertical}
button{background:var(--hi);color:#fff;border:none;border-radius:4px;padding:7px 18px;cursor:pointer;font-weight:bold}
button:hover{opacity:.85}
.err{color:#e74c3c;padding:8px;background:#2a1010;border-radius:4px}
nav{margin-bottom:16px;font-size:13px}
.mono{font-family:monospace;white-space:pre-wrap;font-size:12px}
.count{color:var(--sub);font-size:12px}
"""

# ddrescue map status characters: (hex_color, display_label)
_MAP_STATUS = {
    '+': ('#22aa44', 'rescued'),
    '-': ('#334466', 'non-tried'),
    '/': ('#cc9900', 'non-trimmed'),
    '*': ('#dd6600', 'non-scraped'),
    '?': ('#cc2222', 'bad-sector'),
}
_MAP_PRIORITY = {'+': 4, '-': 3, '/': 2, '*': 1, '?': 0}  # lower = worse

def h(s):
    return html.escape(str(s)) if s is not None else "<span style='color:#555'>NULL</span>"

def badge(status):
    cls = {"ok": "ok", "partial": "partial", "failed": "failed",
           "running": "running", "error": "error"}.get(str(status).lower(), "pending")
    return f'<span class="badge {cls}">{h(status)}</span>'

def page(title, body, db_name="", nav_extra=""):
    home = '<a href="/">&#8962; home</a>'
    db_link = f' &rsaquo; <a href="/db?db={urllib.parse.quote(db_name)}">{h(Path(db_name).name)}</a>' if db_name else ""
    nav = f'<nav>{home}{db_link}{nav_extra}</nav>'
    return f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>{h(title)} &mdash; hdd-recovery</title>
<style>{CSS}</style></head><body>{nav}<h1>{h(title)}</h1>{body}</body></html>"""

def safe_sql(sql):
    stripped = sql.strip().lstrip(";").strip()
    upper = stripped.upper()
    if not re.match(r"\s*(SELECT|WITH)\b", upper):
        raise ValueError("Only SELECT and WITH queries are allowed.")
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX"):
        if re.search(r"\b" + bad + r"\b", upper):
            raise ValueError(f"Disallowed keyword: {bad}")
    return stripped

def run_query(db_path, sql, limit=5000):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchmany(limit)
        cols = [d[0] for d in conn.execute(sql).description] if rows else \
               [d[0] for d in conn.execute(f"SELECT * FROM ({sql}) LIMIT 0").description]
        return cols, rows
    finally:
        conn.close()

def query_scalar(db_path, sql, default=None):
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(sql).fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default

def table_html(cols, rows, max_cell=300):
    if not rows:
        return '<p class="count">No rows.</p>'
    buf = f'<p class="count">{len(rows)} row(s)</p><div style="overflow-x:auto"><table><tr>'
    for c in cols:
        buf += f"<th>{h(c)}</th>"
    buf += "</tr>"
    for row in rows:
        buf += "<tr>"
        for val in row:
            cell = str(val) if val is not None else ""
            display = (cell[:max_cell] + "…") if len(cell) > max_cell else cell
            buf += f"<td>{h(display)}</td>"
        buf += "</tr>"
    buf += "</table></div>"
    return buf

# ── database discovery ────────────────────────────────────────────────────────

def find_databases(root):
    pattern = os.path.join(root, "**", "*.analysis.sqlite")
    dbs = sorted(glob.glob(pattern, recursive=True))
    # also top-level images dir
    extra = sorted(glob.glob(os.path.join(root, "images", "*.analysis.sqlite")))
    seen = set()
    result = []
    for d in dbs + extra:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result

# ── pages ─────────────────────────────────────────────────────────────────────

def page_home(root):
    dbs = find_databases(root)
    if not dbs:
        body = f'<div class="panel"><p>No *.analysis.sqlite files found under <code>{h(root)}</code>.</p></div>'
        return page("Recovery Dashboard", body)

    rows_html = ""
    for db_path in dbs:
        name = Path(db_path).name
        size_mb = os.path.getsize(db_path) / 1e6
        image_path = query_scalar(db_path, "SELECT image_path FROM image_info WHERE id=1") or "?"
        total_files = query_scalar(db_path, "SELECT COUNT(*) FROM files") or 0
        total_artifacts = query_scalar(db_path, "SELECT COUNT(*) FROM recovered_artifacts") or 0
        wallet_hits = query_scalar(db_path, "SELECT COUNT(*) FROM wallet_candidates") or 0
        last_run = query_scalar(db_path,
            "SELECT MAX(COALESCE(ended_at, started_at)) FROM scan_runs") or "—"
        map_p = query_scalar(db_path, "SELECT ddrescue_map_path FROM image_info WHERE id=1") or ""
        enc = urllib.parse.quote(db_path)
        map_cell = (f'<a href="/mapview?db={enc}">&#128209;</a>'
                    if map_p and os.path.isfile(map_p) else "—")
        link = f'/db?db={enc}'
        rows_html += f"""<tr>
          <td><a href="{link}">{h(name)}</a></td>
          <td class="mono">{h(Path(image_path).name)}</td>
          <td>{size_mb:.1f} MB</td>
          <td>{total_files:,}</td>
          <td>{total_artifacts:,}</td>
          <td>{wallet_hits:,}</td>
          <td>{h(str(last_run)[:19])}</td>
          <td style="text-align:center">{map_cell}</td>
        </tr>"""

    body = f"""<div class="panel">
      <p class="count">{len(dbs)} database(s) found under <code>{h(root)}</code></p>
      <table style="margin-top:10px">
        <tr><th>Database</th><th>Image</th><th>DB Size</th>
            <th>Files</th><th>Artifacts</th><th>Wallet Hits</th><th>Last Run</th><th>Map</th></tr>
        {rows_html}
      </table>
    </div>"""
    return page("Recovery Dashboard", body)


def page_db(db_path):
    if not os.path.isfile(db_path):
        return page("Error", '<p class="err">Database not found.</p>')

    name = Path(db_path).name

    # image_info
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        info = conn.execute("SELECT * FROM image_info WHERE id=1").fetchone()
        conn.close()
    except Exception:
        info = None

    info_html = ""
    if info:
        fields = dict(info)
        info_html = "<table>" + "".join(
            f"<tr><th>{h(k)}</th><td>{h(v)}</td></tr>" for k, v in fields.items()
        ) + "</table>"
    else:
        info_html = "<p>image_info table not found.</p>"

    # scan_runs summary
    try:
        cols, runs = run_query(db_path,
            "SELECT stage, status, started_at, ended_at, log_path, output_dir FROM scan_runs ORDER BY started_at")
        runs_html = table_html(cols, runs)
    except Exception as e:
        runs_html = f'<p class="err">{h(str(e))}</p>'

    # counts with links — Pictures counts both scored candidates and carved image artifacts
    enc = urllib.parse.quote(db_path)
    pic_count = (
        (query_scalar(db_path, "SELECT COUNT(*) FROM picture_candidates") or 0) +
        (query_scalar(db_path,
            "SELECT COUNT(*) FROM recovered_artifacts WHERE mime_type LIKE 'image/%'") or 0)
    )
    COUNTS = [
        ("SELECT COUNT(*) FROM files",              "/search",   "&#128196; Files",
         "All files indexed from the filesystem (TSK/fiwalk), including deleted entries"),
        ("SELECT COUNT(*) FROM wallet_candidates",  "/wallets",  "&#128179; Wallets",
         "Files scored for wallet keywords/extensions by image-detect-wallets.sh"),
        (None,                                      "/pictures", "&#128247; Pictures",
         "Filesystem picture candidates + carved image artifacts (JPEG, PNG, BMP, GIF …)"),
        ("SELECT COUNT(*) FROM recovered_artifacts","/artifacts","&#128230; Artifacts",
         "All files recovered by carving tools: foremost, scalpel, PhotoRec, extundelete, ext4magic"),
        ("SELECT COUNT(*) FROM bulk_extractor_hits","/bulk_hits","&#128202; Bulk Hits",
         "Feature hits from bulk_extractor: URLs, email addresses, Bitcoin addresses, credit cards …"),
        ("SELECT COUNT(*) FROM findings",           "/findings", "&#128270; Findings",
         "Structured findings from exiftool (GPS/EXIF), YARA, regripper, rifiuti2, plaso, pdf-extract"),
    ]
    counts_html = ""
    for sql, route, icon, tip in COUNTS:
        n = pic_count if sql is None else (query_scalar(db_path, sql) or 0)
        counts_html += (
            f'<a href="{route}?db={enc}" style="margin-right:16px" title="{h(tip)}">'
            f'<span class="badge ok">{n:,}</span> {icon}</a>'
        )

    # extra links not covered by count badges
    map_path = (info['ddrescue_map_path'] if info and info['ddrescue_map_path'] else
                query_scalar(db_path, "SELECT ddrescue_map_path FROM image_info WHERE id=1") or "")
    map_link = (f' &nbsp; <a href="/mapview?db={enc}" title="Visual block map of ddrescue imaging coverage">&#128209; ddrescue Map</a>'
                if map_path and os.path.isfile(map_path) else "")

    body = f"""
    <div class="panel"><h2>Image Info</h2>{info_html}</div>
    <div class="panel"><h2>Quick Links</h2>
      <p>{counts_html}</p>
      <p style="margin-top:10px">
        <a href="/timeline?db={enc}" title="Chronological event timeline assembled from all analysis stages">&#128337; Timeline</a> &nbsp;
        <a href="/sql?db={enc}" title="Run read-only SELECT queries directly against the analysis database">&#9998; SQL Query</a>{map_link}
      </p>
    </div>
    <div class="panel"><h2>Stage Run History</h2>{runs_html}</div>
    """
    return page(name, body, db_name=db_path)


def page_wallets(db_path, limit=200):
    try:
        cols, rows = run_query(db_path, f"""
            SELECT wc.score, wc.reason, wc.source_stage,
                   f.name, f.path, f.size_bytes, wc.details, wc.created_at
            FROM   wallet_candidates wc
            LEFT JOIN files f ON f.id = wc.file_id
            ORDER  BY wc.score DESC, f.path
            LIMIT  {limit}""")
        body = f'<div class="panel">{table_html(cols, rows)}</div>'
    except Exception as e:
        body = f'<div class="panel err">{h(str(e))}</div>'
    return page("Wallet Candidates", body, db_name=db_path, nav_extra=' &rsaquo; wallets')


def page_pictures(db_path, limit=500):
    # Section 1: filesystem-aware scored candidates (from image-detect-pictures.sh)
    try:
        cols, rows = run_query(db_path, f"""
            SELECT pc.score, pc.reason, pc.camera_model, pc.taken_at,
                   pc.width, pc.height,
                   f.name, f.path, f.size_bytes
            FROM   picture_candidates pc
            LEFT JOIN files f ON f.id = pc.file_id
            ORDER  BY pc.score DESC, f.path
            LIMIT  {limit}""")
        cand_html = table_html(cols, rows)
        if not rows:
            cand_html = '<p class="count">No filesystem-indexed picture candidates. The disk may have no image files in its partition table, or the detect-pictures stage found none.</p>'
    except Exception as e:
        cand_html = f'<p class="err">{h(str(e))}</p>'

    # Section 2: image artifacts with per-row View link (Option 2) + gallery link (Option 1)
    enc = urllib.parse.quote(db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        img_rows = conn.execute(
            "SELECT method, mime_type, size_bytes, relative_path, full_path FROM recovered_artifacts "
            f"WHERE mime_type LIKE 'image/%' ORDER BY method, mime_type, size_bytes DESC LIMIT {limit}"
        ).fetchall()
        conn.close()

        tbl = ('<p class="count">' + str(len(img_rows)) + ' row(s)</p>'
               '<div style="overflow-x:auto"><table>'
               '<tr><th>Method</th><th>MIME</th><th>Size (B)</th><th>Path</th><th>View</th></tr>')
        for r in img_rows:
            fp = r["full_path"] or ""
            fenc = urllib.parse.quote(fp)
            view = (f'<a href="/file?path={fenc}" target="_blank" '
                    f'title="Open image in new tab">&#128247;</a>' if fp else "")
            tbl += (f'<tr><td>{h(r["method"])}</td><td>{h(r["mime_type"])}</td>'
                    f'<td>{(r["size_bytes"] or 0):,}</td>'
                    f'<td style="word-break:break-all">{h(r["relative_path"])}</td>'
                    f'<td style="text-align:center">{view}</td></tr>')
        tbl += '</table></div>'
        carved_html = tbl
    except Exception as e:
        carved_html = f'<p class="err">{h(str(e))}</p>'

    body = f"""
    <div class="panel">
      <h2>Filesystem Picture Candidates</h2>
      <p style="color:var(--sub);font-size:12px;margin-bottom:8px">
        Scored by <code>image-detect-pictures.sh</code> from the filesystem index (TSK/fiwalk).
      </p>
      {cand_html}
    </div>
    <div class="panel">
      <h2>Carved Image Artifacts</h2>
      <p style="color:var(--sub);font-size:12px;margin-bottom:6px">
        Files with <code>image/*</code> MIME type recovered by foremost, scalpel, PhotoRec, etc.
        &nbsp;&nbsp;
        <a href="/gallery?db={enc}"
           style="background:#0f3460;padding:3px 10px;border-radius:3px;color:#7eb8f7"
           title="Browse all carved images as a paginated thumbnail grid">&#128443; Gallery View</a>
      </p>
      {carved_html}
    </div>
    """
    return page("Pictures", body, db_name=db_path, nav_extra=' &rsaquo; pictures')


def page_files(db_path, pattern="", limit=500):
    enc = urllib.parse.quote(db_path)
    search_form = f"""<div class="panel">
      <form method="get" action="/search">
        <input type="hidden" name="db" value="{h(db_path)}">
        <input type="text" name="q" value="{h(pattern)}" placeholder="filename pattern (SQL LIKE, e.g. %.jpg)">
        <button type="submit">Search</button>
      </form>
    </div>"""
    results_html = ""
    if pattern:
        try:
            sql = f"""SELECT name, path, size, mtime, alloc, fs_type
                      FROM files WHERE path LIKE ? OR name LIKE ?
                      ORDER BY size DESC LIMIT {limit}"""
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (pattern, pattern)).fetchmany(limit)
            cols = ["name", "path", "size", "mtime", "alloc", "fs_type"]
            conn.close()
            results_html = f'<div class="panel">{table_html(cols, rows)}</div>'
        except Exception as e:
            results_html = f'<div class="panel err">{h(str(e))}</div>'
    return page("File Search", search_form + results_html, db_name=db_path,
                nav_extra=' &rsaquo; search')


def page_artifacts(db_path, method="", limit=1000):
    enc = urllib.parse.quote(db_path)
    try:
        method_filter = f"WHERE method = '{method}'" if method else ""
        cols, rows = run_query(db_path,
            f"""SELECT method, mime_type, size_bytes, relative_path, sha256, created_at
                FROM recovered_artifacts {method_filter}
                ORDER BY created_at DESC LIMIT {limit}""")
        # method selector
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            methods = [r[0] for r in conn.execute(
                "SELECT DISTINCT method FROM recovered_artifacts ORDER BY method").fetchall()]
            conn.close()
        except Exception:
            methods = []
        opts = '<option value="">All</option>' + "".join(
            f'<option value="{h(m)}" {"selected" if m==method else ""}>{h(m)}</option>'
            for m in methods)
        form = f"""<div class="panel">
          <form method="get" action="/artifacts">
            <input type="hidden" name="db" value="{h(db_path)}">
            <select name="method">{opts}</select>
            <button type="submit">Filter</button>
          </form>
        </div>"""
        body = form + f'<div class="panel">{table_html(cols, rows)}</div>'
    except Exception as e:
        body = f'<div class="panel err">{h(str(e))}</div>'
    return page("Recovered Artifacts", body, db_name=db_path,
                nav_extra=' &rsaquo; artifacts')


def page_timeline(db_path, fmt="table", limit=2000):
    enc = urllib.parse.quote(db_path)
    # Run image-timeline.sh via subprocess for the table
    import subprocess
    script = Path(__file__).parent / "image-timeline.sh"
    try:
        result = subprocess.run(
            ["bash", str(script), db_path, "--format", "json"],
            capture_output=True, text=True, timeout=30
        )
        events = json.loads(result.stdout) if result.stdout.strip() else []
    except Exception as e:
        events = []
        err = str(e)
    else:
        err = result.stderr[:500] if result.returncode != 0 else ""

    if err:
        body = f'<div class="panel err">{h(err)}</div>'
        return page("Timeline", body, db_name=db_path, nav_extra=' &rsaquo; timeline')

    rows_html = ""
    for ev in events[:limit]:
        ts = h(ev.get("timestamp", ""))
        ev_type = h(ev.get("event_type", ""))
        src = h(ev.get("source", ""))
        label = h(ev.get("label", "")[:150])
        rows_html += f"<tr><td>{ts}</td><td>{ev_type}</td><td>{src}</td><td>{label}</td></tr>"

    body = f"""<div class="panel">
      <p class="count">{len(events)} event(s) &mdash; showing {min(limit, len(events))}</p>
      <p><a href="/timeline?db={enc}&raw=1">Download JSON</a></p>
      <table>
        <tr><th>Timestamp</th><th>Event Type</th><th>Source</th><th>Label</th></tr>
        {rows_html}
      </table>
    </div>"""
    return page("Timeline", body, db_name=db_path, nav_extra=' &rsaquo; timeline')


def page_sql(db_path, sql="", error="", cols=None, rows=None):
    enc = urllib.parse.quote(db_path)
    result_html = ""
    if error:
        result_html = f'<div class="panel err">{h(error)}</div>'
    elif rows is not None:
        result_html = f'<div class="panel">{table_html(cols or [], rows)}</div>'

    body = f"""<div class="panel">
      <p style="color:var(--sub);margin-bottom:8px">Only SELECT and WITH queries are allowed.</p>
      <form method="post" action="/sql">
        <input type="hidden" name="db" value="{h(db_path)}">
        <textarea name="sql" placeholder="SELECT ...">{h(sql)}</textarea>
        <button type="submit">Run</button>
      </form>
    </div>{result_html}"""
    return page("SQL Query", body, db_name=db_path, nav_extra=' &rsaquo; sql')


def page_bulk_hits(db_path, scope="", feature="", limit=2000):
    enc = urllib.parse.quote(db_path)
    # scope selector
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        scopes = [r[0] for r in conn.execute(
            "SELECT DISTINCT source_scope FROM bulk_extractor_hits ORDER BY source_scope").fetchall()]
        features = [r[0] for r in conn.execute(
            "SELECT DISTINCT feature_file FROM bulk_extractor_hits ORDER BY feature_file").fetchall()]
        conn.close()
    except Exception:
        scopes, features = [], []

    scope_opts = '<option value="">All scopes</option>' + "".join(
        f'<option value="{h(s)}" {"selected" if s==scope else ""}>{h(s)}</option>' for s in scopes)
    feat_opts = '<option value="">All features</option>' + "".join(
        f'<option value="{h(f)}" {"selected" if f==feature else ""}>{h(f)}</option>' for f in features)

    form = f"""<div class="panel">
      <form method="get" action="/bulk_hits">
        <input type="hidden" name="db" value="{h(db_path)}">
        <select name="scope">{scope_opts}</select>
        <select name="feature">{feat_opts}</select>
        <button type="submit">Filter</button>
      </form>
    </div>"""

    try:
        where_parts = []
        if scope:
            where_parts.append(f"source_scope = '{scope.replace(chr(39), chr(39)*2)}'")
        if feature:
            where_parts.append(f"feature_file = '{feature.replace(chr(39), chr(39)*2)}'")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cols, rows = run_query(db_path,
            f"SELECT source_scope, feature_file, value, context, offset_ref "
            f"FROM bulk_extractor_hits {where} "
            f"ORDER BY source_scope, feature_file LIMIT {limit}")
        body = form + f'<div class="panel">{table_html(cols, rows)}</div>'
    except Exception as e:
        body = form + f'<div class="panel err">{h(str(e))}</div>'
    return page("Bulk Extractor Hits", body, db_name=db_path, nav_extra=' &rsaquo; bulk_hits')


def page_findings(db_path, tool="", category="", limit=2000):
    enc = urllib.parse.quote(db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        tools = [r[0] for r in conn.execute(
            "SELECT DISTINCT source_tool FROM findings ORDER BY source_tool").fetchall()]
        cats  = [r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM findings ORDER BY category").fetchall()]
        conn.close()
    except Exception:
        tools, cats = [], []

    tool_opts = '<option value="">All tools</option>' + "".join(
        f'<option value="{h(t)}" {"selected" if t==tool else ""}>{h(t)}</option>' for t in tools)
    cat_opts = '<option value="">All categories</option>' + "".join(
        f'<option value="{h(c)}" {"selected" if c==category else ""}>{h(c)}</option>' for c in cats)

    form = f"""<div class="panel">
      <p style="color:var(--sub);margin-bottom:8px">
        Findings from: exiftool (GPS/EXIF), YARA (wallet patterns), regripper (Windows registry),
        rifiuti2 (Recycle Bin), plaso (timeline), pdf-extract (seed phrases).
      </p>
      <form method="get" action="/findings">
        <input type="hidden" name="db" value="{h(db_path)}">
        <select name="tool">{tool_opts}</select>
        <select name="category">{cat_opts}</select>
        <button type="submit">Filter</button>
      </form>
    </div>"""

    try:
        where_parts = []
        if tool:
            where_parts.append(f"source_tool = '{tool.replace(chr(39), chr(39)*2)}'")
        if category:
            where_parts.append(f"category = '{category.replace(chr(39), chr(39)*2)}'")
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cols, rows = run_query(db_path,
            f"SELECT source_tool, category, key, value, score, path, notes, created_at "
            f"FROM findings {where} "
            f"ORDER BY score DESC, source_tool, category LIMIT {limit}")
        body = form + f'<div class="panel">{table_html(cols, rows)}</div>'
    except Exception as e:
        msg = str(e)
        if "no such table" in msg:
            body = form + (
                '<div class="panel"><p>The <code>findings</code> table does not exist in this database yet. '
                'It is created automatically when you run any of the following analysis stages:</p>'
                '<ul style="margin:8px 0 0 20px;color:var(--sub)">'
                '<li>EXIF Photo Enrichment (<code>image-enrich-photos.sh</code>)</li>'
                '<li>YARA Wallet Scan (<code>image-yara-scan.sh</code>)</li>'
                '<li>PDF Seed Extraction (<code>image-pdf-extract.sh</code>)</li>'
                '<li>RegRipper (<code>image-regripper.sh</code>)</li>'
                '<li>rifiuti2 (<code>image-rifiuti.sh</code>)</li>'
                '</ul></div>'
            )
        else:
            body = form + f'<div class="panel err">{h(msg)}</div>'
    return page("Findings", body, db_name=db_path, nav_extra=' &rsaquo; findings')


# ── file serving & image gallery ─────────────────────────────────────────────

def _safe_file_read(path, root):
    """Read a file only if it resolves to a path inside root. Returns (bytes, mime) or (None, err)."""
    try:
        abs_path = os.path.realpath(path)
        abs_root = os.path.realpath(root)
        if not abs_path.startswith(abs_root + os.sep):
            return None, "Forbidden: path is outside the recovery root"
        if not os.path.isfile(abs_path):
            return None, "File not found"
        mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
        with open(abs_path, "rb") as f:
            return f.read(), mime
    except OSError as e:
        return None, str(e)


def page_gallery(db_path, root, pg=0, per_page=48, all_images=False):
    """Paginated (or all-on-one-page) image gallery."""
    enc = urllib.parse.quote(db_path)
    try:
        total = query_scalar(db_path,
            "SELECT COUNT(*) FROM recovered_artifacts WHERE mime_type LIKE 'image/%'") or 0
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if all_images:
            rows = conn.execute(
                "SELECT full_path, mime_type, size_bytes, method FROM recovered_artifacts "
                "WHERE mime_type LIKE 'image/%' ORDER BY method, full_path"
            ).fetchall()
        else:
            offset = pg * per_page
            rows = conn.execute(
                "SELECT full_path, mime_type, size_bytes, method FROM recovered_artifacts "
                "WHERE mime_type LIKE 'image/%' ORDER BY method, full_path "
                f"LIMIT {per_page} OFFSET {offset}"
            ).fetchall()
        conn.close()
    except Exception as e:
        body = f'<div class="panel err">{h(str(e))}</div>'
        return page("Image Gallery", body, db_name=db_path)

    abs_root = os.path.realpath(root)

    imgs = ""
    for row in rows:
        fp = row["full_path"] or ""
        sz = row["size_bytes"] or 0
        method = row["method"] or ""
        if not fp or not os.path.realpath(fp).startswith(abs_root + os.sep) or not os.path.isfile(fp):
            continue
        fenc = urllib.parse.quote(fp)
        tip = f"{Path(fp).name} — {method} — {sz:,} B"
        imgs += (
            f'<a href="/file?path={fenc}" target="_blank" title="{h(tip)}">'
            f'<img src="/file?path={fenc}" loading="lazy" '
            f'style="width:150px;height:112px;object-fit:cover;border-radius:4px;'
            f'border:1px solid #333;background:#111">'
            f'</a>'
        )

    btn_style = 'style="background:#0f3460;padding:3px 12px;border-radius:3px;color:#7eb8f7"'
    if all_images:
        header = f'<p class="count">{total:,} images — all on one page</p>'
        toggle = f'<a href="/gallery?db={enc}" {btn_style}>&#9660; Paginated view</a>'
        pager = ""
    else:
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = pg * per_page
        prev_link = (f'<a href="/gallery?db={enc}&page={pg-1}">&larr; Prev</a>' if pg > 0 else "")
        next_link = (f'<a href="/gallery?db={enc}&page={pg+1}">Next &rarr;</a>'
                     if (offset + per_page) < total else "")
        pager = " &nbsp; ".join(filter(None, [prev_link, next_link]))
        header = f'<p class="count">{total:,} images &mdash; page {pg+1} of {total_pages} &nbsp; ({per_page} per page)</p>'
        toggle = f'<a href="/gallery?db={enc}&all=1" {btn_style}>&#9651; View all on one page</a>'

    body = f"""
    <div class="panel">
      {header}
      <p style="margin-top:8px">{toggle}{(" &nbsp;&nbsp; " + pager) if pager else ""}</p>
    </div>
    <div class="panel">
      <div style="display:flex;flex-wrap:wrap;gap:6px">{imgs or '<p class="count">No images found.</p>'}</div>
    </div>
    {"<div class='panel'><p>" + pager + "</p></div>" if pager else ""}
    """
    return page("Image Gallery", body, db_name=db_path, nav_extra=" &rsaquo; gallery")


# ── ddrescue map visualization ───────────────────────────────────────────────

def parse_mapfile(path):
    """Parse a GNU ddrescue mapfile. Returns (meta, blocks).

    meta  — dict: current_pos, current_status, current_pass, start_time,
                  current_time, finished, command_line
    blocks — list of (pos_bytes: int, size_bytes: int, status_char: str)
    """
    meta = {k: None for k in ('current_pos', 'current_status', 'current_pass',
                               'start_time', 'current_time', 'command_line')}
    meta['finished'] = False
    blocks = []
    section = None  # 'header' | 'data'

    try:
        with open(path, 'r', errors='replace') as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith('#'):
                    c = line[1:].strip()
                    if c.startswith('Command line:'):
                        meta['command_line'] = c[len('Command line:'):].strip()
                    elif c.startswith('Start time:'):
                        meta['start_time'] = c[len('Start time:'):].strip()
                    elif c.startswith('Current time:'):
                        meta['current_time'] = c[len('Current time:'):].strip()
                    elif 'Finished' in c:
                        meta['finished'] = True
                    elif 'current_pos' in c:
                        section = 'header'
                    elif 'pos' in c and 'size' in c and 'status' in c:
                        section = 'data'
                    continue
                parts = line.split()
                if section == 'header' and len(parts) >= 2:
                    try:
                        meta['current_pos'] = int(parts[0], 16)
                        meta['current_status'] = parts[1]
                        if len(parts) >= 3:
                            meta['current_pass'] = int(parts[2])
                    except ValueError:
                        pass
                    section = 'data'
                elif section == 'data' and len(parts) >= 3:
                    try:
                        blocks.append((int(parts[0], 16), int(parts[1], 16), parts[2]))
                    except ValueError:
                        pass
    except OSError:
        pass

    return meta, blocks


def _map_svg(blocks, cols=200, cell_w=5, cell_h=5):
    """Rasterize ddrescue blocks as a colored SVG grid.

    Returns (svg_html, stats_dict).  Uses worst-status-wins per cell so a
    single bad block in a region isn't hidden by surrounding good data.
    """
    if not blocks:
        return '<p class="count">No blocks in map file.</p>', {}

    total_size = max(pos + sz for pos, sz, _ in blocks)
    if total_size == 0:
        return '<p class="count">Map covers zero bytes.</p>', {}

    target = 5000
    bpc = max(512, (total_size + target - 1) // target)  # bytes per cell
    num_cells = (total_size + bpc - 1) // bpc
    rows = (num_cells + cols - 1) // cols
    num_pad = rows * cols  # padded so last row is complete

    # None = no block seen yet for this cell; filled in below
    cell_st = [None] * num_pad

    for pos, sz, st in blocks:
        c0 = pos // bpc
        c1 = min(num_pad - 1, (pos + sz - 1) // bpc)
        prio = _MAP_PRIORITY.get(st, 5)
        for c in range(c0, c1 + 1):
            existing = cell_st[c]
            if existing is None or prio < _MAP_PRIORITY.get(existing, 5):
                cell_st[c] = st

    pad = 2
    svg_w = cols * cell_w + 2 * pad
    svg_h = rows * cell_h + 2 * pad

    rects = []
    for idx in range(num_pad):
        st = cell_st[idx] or '-'   # uncovered cells → non-tried
        color = _MAP_STATUS.get(st, ('#888', 'unknown'))[0]
        cx = pad + (idx % cols) * cell_w
        cy = pad + (idx // cols) * cell_h
        rects.append(f'<rect x="{cx}" y="{cy}" width="{cell_w}" height="{cell_h}" fill="{color}"/>')

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" '
        f'style="display:block;background:#111;border-radius:4px">'
        + ''.join(rects) + '</svg>'
    )

    byte_stats = {}
    for _, sz, st in blocks:
        byte_stats[st] = byte_stats.get(st, 0) + sz

    return svg, {'total_size': total_size, 'bpc': bpc, 'bytes': byte_stats}


def page_mapview(map_path, db_path=""):
    if not map_path:
        body = ('<div class="panel err"><p>No map file specified. '
                'Use <code>?map=/path/to/file.map</code> or <code>?db=/path/to/db</code>.</p></div>')
        return page("ddrescue Map View", body, db_name=db_path)
    if not os.path.isfile(map_path):
        body = f'<div class="panel err"><p>Map file not found: <code>{h(map_path)}</code></p></div>'
        return page("ddrescue Map View", body, db_name=db_path)

    meta, blocks = parse_mapfile(map_path)
    svg, stats = _map_svg(blocks)

    # metadata table
    rows_m = []
    for label, key in [("Start time", "start_time"), ("Last updated", "current_time")]:
        if meta.get(key):
            rows_m.append(f"<tr><th>{label}</th><td>{h(meta[key])}</td></tr>")
    if meta.get('current_pos') is not None:
        cp = meta['current_pos']
        rows_m.append(f"<tr><th>Current position</th><td>{hex(cp)} &nbsp;({cp:,} B)</td></tr>")
    if meta.get('current_status'):
        sname = _MAP_STATUS.get(meta['current_status'], ('#888', meta['current_status']))[1]
        rows_m.append(f"<tr><th>Current status</th><td>{h(sname)}</td></tr>")
    if meta.get('current_pass') is not None:
        rows_m.append(f"<tr><th>Current pass</th><td>{meta['current_pass']}</td></tr>")
    rows_m.append(f"<tr><th>Finished</th><td>{'&#9989; Yes' if meta['finished'] else '&#9203; In progress'}</td></tr>")
    rows_m.append(f"<tr><th>Data blocks</th><td>{len(blocks):,}</td></tr>")
    total = stats.get('total_size', 0)
    if total:
        rows_m.append(f"<tr><th>Total size</th><td>{total:,} B &nbsp;({total/1e9:.2f} GB)</td></tr>")
    if stats.get('bpc'):
        rows_m.append(f"<tr><th>Map resolution</th><td>{stats['bpc']:,} B per display cell</td></tr>")
    meta_html = "<table>" + "".join(rows_m) + "</table>"

    # coverage stats
    byte_stats = stats.get('bytes', {})
    total_b = stats.get('total_size', 1) or 1
    stat_rows = []
    for st, (color, label) in _MAP_STATUS.items():
        amount = byte_stats.get(st, 0)
        if amount == 0:
            continue
        pct = amount / total_b * 100
        bar = f'<div style="background:{color};width:{min(100, pct):.1f}%;height:12px;border-radius:2px"></div>'
        swatch = (f'<span style="display:inline-block;width:12px;height:12px;'
                  f'background:{color};border-radius:2px;vertical-align:middle"></span>')
        stat_rows.append(
            f"<tr><td>{swatch} {h(label)}</td>"
            f"<td>{amount/1e9:.3f} GB</td>"
            f"<td>{pct:.2f}%</td>"
            f"<td style='width:200px'>{bar}</td></tr>"
        )
    stats_html = (
        "<table><tr><th>Status</th><th>Size</th><th>%</th><th></th></tr>"
        + "".join(stat_rows) + "</table>"
    ) if stat_rows else "<p class='count'>No blocks to summarise.</p>"

    # legend
    legend_parts = []
    for st, (color, label) in _MAP_STATUS.items():
        sw = (f'<span style="display:inline-block;width:12px;height:12px;background:{color};'
              f'border-radius:2px;vertical-align:middle;margin-right:3px"></span>')
        legend_parts.append(f'{sw}{h(label)} <code style="color:#888">({st})</code>')
    legend = " &nbsp;&nbsp; ".join(legend_parts)

    body = f"""
    <div class="panel">
      <h2>{h(Path(map_path).name)}</h2>
      <p style="color:var(--sub);font-size:11px;margin-bottom:10px;word-break:break-all">{h(map_path)}</p>
      {meta_html}
    </div>
    <div class="panel">
      <h2>Coverage Map</h2>
      <p style="color:var(--sub);font-size:12px;margin-bottom:8px">{legend}</p>
      {svg}
    </div>
    <div class="panel"><h2>Coverage Statistics</h2>{stats_html}</div>
    """
    return page("ddrescue Map View", body, db_name=db_path, nav_extra=" &rsaquo; mapview")


# ── request handler ───────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    root = "/mnt/recovery16tb/recovery"

    def log_message(self, fmt, *args):
        pass  # suppress default access log; errors still shown

    def send_html(self, content, status=200):
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def qs(self):
        parsed = urllib.parse.urlparse(self.path)
        return urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def qsval(self, key, default=""):
        return self.qs().get(key, [default])[0]

    def path_only(self):
        return urllib.parse.urlparse(self.path).path

    def do_GET(self):
        p = self.path_only()
        db = self.qsval("db")

        try:
            if p == "/":
                self.send_html(page_home(self.root))
            elif p == "/db":
                self.send_html(page_db(db))
            elif p == "/wallets":
                self.send_html(page_wallets(db))
            elif p == "/pictures":
                self.send_html(page_pictures(db))
            elif p == "/search":
                self.send_html(page_files(db, self.qsval("q")))
            elif p == "/artifacts":
                self.send_html(page_artifacts(db, self.qsval("method")))
            elif p == "/timeline":
                if self.qsval("raw") == "1":
                    import subprocess
                    script = Path(__file__).parent / "image-timeline.sh"
                    result = subprocess.run(
                        ["bash", str(script), db, "--format", "json"],
                        capture_output=True, text=True, timeout=60)
                    data = result.stdout.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Disposition",
                        f'attachment; filename="{Path(db).stem}-timeline.json"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_html(page_timeline(db))
            elif p == "/bulk_hits":
                self.send_html(page_bulk_hits(db, self.qsval("scope"), self.qsval("feature")))
            elif p == "/findings":
                self.send_html(page_findings(db, self.qsval("tool"), self.qsval("category")))
            elif p == "/files":
                # /files was linked from old page_db counts; redirect to /search
                import urllib.request
                location = f"/search?db={urllib.parse.quote(db)}"
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()
            elif p == "/sql":
                self.send_html(page_sql(db))
            elif p == "/mapview":
                map_path = self.qsval("map")
                if not map_path and db and os.path.isfile(db):
                    map_path = query_scalar(db, "SELECT ddrescue_map_path FROM image_info WHERE id=1") or ""
                self.send_html(page_mapview(map_path, db_path=db))
            elif p == "/file":
                file_path = self.qsval("path")
                data, mime_or_err = _safe_file_read(file_path, self.root)
                if data is None:
                    self.send_html(page("Error", f'<p class="err">{h(mime_or_err)}</p>'), 403)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", mime_or_err)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                return
            elif p == "/gallery":
                all_images = self.qsval("all") == "1"
                try:
                    pg = max(0, int(self.qsval("page", "0") or "0"))
                except ValueError:
                    pg = 0
                self.send_html(page_gallery(db, self.root, pg, all_images=all_images))
            else:
                self.send_html(page("404", "<p>Not found.</p>"), 404)
        except Exception as e:
            self.send_html(page("Error", f'<p class="err">{h(str(e))}</p>'), 500)

    def do_POST(self):
        p = self.path_only()
        length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(length).decode("utf-8", errors="replace")
        params = urllib.parse.parse_qs(body_raw, keep_blank_values=True)

        def pval(k, default=""):
            return params.get(k, [default])[0]

        db = pval("db")

        if p == "/sql":
            sql = pval("sql", "").strip()
            if not sql:
                self.send_html(page_sql(db))
                return
            try:
                safe = safe_sql(sql)
                cols, rows = run_query(db, safe)
                self.send_html(page_sql(db, sql=sql, cols=cols, rows=rows))
            except Exception as e:
                self.send_html(page_sql(db, sql=sql, error=str(e)))
        else:
            self.send_html(page("405", "<p>Method not allowed.</p>"), 405)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="/mnt/recovery16tb/recovery",
                    help="Recovery root directory to search for *.analysis.sqlite files")
    ap.add_argument("--port", type=int, default=7788, help="TCP port (default: 7788)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="Bind address (default: 127.0.0.1, localhost only)")
    args = ap.parse_args()

    Handler.root = args.root

    server = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"hdd-recovery web UI  →  http://{args.host}:{args.port}/")
    print(f"Root: {args.root}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
