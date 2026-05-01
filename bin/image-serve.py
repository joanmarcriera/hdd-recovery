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
        link = f'/db?db={urllib.parse.quote(db_path)}'
        rows_html += f"""<tr>
          <td><a href="{link}">{h(name)}</a></td>
          <td class="mono">{h(Path(image_path).name)}</td>
          <td>{size_mb:.1f} MB</td>
          <td>{total_files:,}</td>
          <td>{total_artifacts:,}</td>
          <td>{wallet_hits:,}</td>
          <td>{h(str(last_run)[:19])}</td>
        </tr>"""

    body = f"""<div class="panel">
      <p class="count">{len(dbs)} database(s) found under <code>{h(root)}</code></p>
      <table style="margin-top:10px">
        <tr><th>Database</th><th>Image</th><th>DB Size</th>
            <th>Files</th><th>Artifacts</th><th>Wallet Hits</th><th>Last Run</th></tr>
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

    # counts — map each to the correct existing route
    COUNTS = [
        ("files",               "SELECT COUNT(*) FROM files",               "/search",   "&#128196; Files"),
        ("wallet candidates",   "SELECT COUNT(*) FROM wallet_candidates",    "/wallets",  "&#128179; Wallets"),
        ("picture candidates",  "SELECT COUNT(*) FROM picture_candidates",   "/pictures", "&#128247; Pictures"),
        ("artifacts",           "SELECT COUNT(*) FROM recovered_artifacts",  "/artifacts","&#128230; Artifacts"),
        ("bulk hits",           "SELECT COUNT(*) FROM bulk_extractor_hits",  "/bulk_hits","&#128202; Bulk Hits"),
        ("findings",            "SELECT COUNT(*) FROM findings",             "/findings", "&#128270; Findings"),
    ]
    enc = urllib.parse.quote(db_path)
    counts_html = ""
    for label, sql, route, icon in COUNTS:
        n = query_scalar(db_path, sql) or 0
        counts_html += (
            f'<a href="{route}?db={enc}" style="margin-right:16px">'
            f'<span class="badge ok">{n:,}</span> {icon}</a>'
        )

    body = f"""
    <div class="panel"><h2>Image Info</h2>{info_html}</div>
    <div class="panel"><h2>Quick Counts</h2><p>{counts_html}</p>
      <p style="margin-top:10px">
        <a href="/timeline?db={enc}">&#128337; Timeline</a> &nbsp;
        <a href="/wallets?db={enc}">&#128179; Wallets</a> &nbsp;
        <a href="/pictures?db={enc}">&#128247; Pictures</a> &nbsp;
        <a href="/findings?db={enc}">&#128270; Findings</a> &nbsp;
        <a href="/search?db={enc}">&#128269; File Search</a> &nbsp;
        <a href="/artifacts?db={enc}">&#128230; Artifacts</a> &nbsp;
        <a href="/bulk_hits?db={enc}">&#128202; Bulk Hits</a> &nbsp;
        <a href="/sql?db={enc}">&#9998; SQL Query</a>
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
    try:
        cols, rows = run_query(db_path, f"""
            SELECT pc.score, pc.reason, pc.camera_model, pc.taken_at,
                   pc.width, pc.height,
                   f.name, f.path, f.size_bytes
            FROM   picture_candidates pc
            LEFT JOIN files f ON f.id = pc.file_id
            ORDER  BY pc.score DESC, f.path
            LIMIT  {limit}""")
        body = f'<div class="panel">{table_html(cols, rows)}</div>'
    except Exception as e:
        body = f'<div class="panel err">{h(str(e))}</div>'
    return page("Picture Candidates", body, db_name=db_path, nav_extra=' &rsaquo; pictures')


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
