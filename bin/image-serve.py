#!/usr/bin/env python3
"""
Read-only web query interface for hdd-recovery SQLite databases.

Serves a local HTTP UI that lets you browse all recovery databases under
a given root directory.  All SQL executed is restricted to SELECT/WITH.

Usage:
  image-serve.py [--root DIR] [--port PORT] [--host HOST]
"""
import argparse, glob, html, http.server, importlib.util, io, json, mimetypes, os, re
import shlex, sqlite3, subprocess, sys, threading, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Load PRESETS from image-pipeline.py so the form mirrors the CLI runner.
_PIPELINE_PATH = Path(__file__).resolve().parent / "image-pipeline.py"
try:
    _spec = importlib.util.spec_from_file_location("image_pipeline", _PIPELINE_PATH)
    _pipeline_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_pipeline_mod)
    PIPELINE_PRESETS: dict[str, list[str]] = dict(_pipeline_mod.PRESETS)
except Exception:
    PIPELINE_PRESETS = {}

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
.img-card{position:relative;display:inline-block;line-height:0}
.img-card .desc{display:none;position:absolute;bottom:0;left:0;right:0;
  background:rgba(0,0,0,.82);color:#eee;font-size:10px;line-height:1.3;
  padding:4px 5px;border-radius:0 0 4px 4px;white-space:normal}
.img-card:hover .desc{display:block}
table.sortable th{cursor:pointer;user-select:none}
table.sortable th:hover{background:#1a2f4a}
table.sortable th[data-sort-state="asc"]::after{content:" \\25B2";color:#7eb8f7}
table.sortable th[data-sort-state="desc"]::after{content:" \\25BC";color:#7eb8f7}
"""

SORT_JS = r"""
(function(){
  function txt(c){return (c.innerText||c.textContent||'').trim();}
  function isEmpty(v){return v==='' || /^null$/i.test(v);}
  function num(v){var n=Number(v.replace(/[, _]/g,''));return isFinite(n)?n:NaN;}
  function inferNumeric(rows, idx){
    var sawAny=false;
    for (var i=0;i<rows.length;i++){
      var v=txt(rows[i].cells[idx]); if (isEmpty(v)) continue;
      if (!isFinite(Number(v.replace(/[, _]/g,'')))) return false;
      sawAny=true;
    }
    return sawAny;
  }
  function sortBy(t, idx, dir){
    var body=t.tBodies[0]||t;
    var rows=Array.prototype.slice.call(body.rows, 1);  // skip header row
    var numeric=inferNumeric(rows, idx);
    rows.sort(function(a,b){
      var av=txt(a.cells[idx]), bv=txt(b.cells[idx]);
      var ae=isEmpty(av), be=isEmpty(bv);
      if (ae&&be) return 0;
      if (ae) return 1;   // empties always last
      if (be) return -1;
      var cmp = numeric
        ? num(av)-num(bv)
        : av.localeCompare(bv, undefined, {sensitivity:'base', numeric:true});
      return dir==='asc' ? cmp : -cmp;
    });
    rows.forEach(function(r){body.appendChild(r);});
  }
  function init(){
    document.querySelectorAll('table').forEach(function(t){
      var first=t.rows[0]; if (!first || t.rows.length<2) return;
      // Only tables whose first row is all <th> (skip key-value layouts)
      var cells=Array.prototype.slice.call(first.cells);
      if (!cells.length) return;
      if (!cells.every(function(c){return c.tagName==='TH';})) return;
      t.classList.add('sortable');
      cells.forEach(function(th, idx){
        th.addEventListener('click', function(){
          var prev = t.dataset.sortIdx===String(idx) ? t.dataset.sortDir : '';
          var dir = prev==='asc' ? 'desc' : 'asc';
          t.dataset.sortIdx=String(idx); t.dataset.sortDir=dir;
          cells.forEach(function(c){c.removeAttribute('data-sort-state');});
          th.dataset.sortState=dir;
          sortBy(t, idx, dir);
        });
      });
    });
  }
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
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
<style>{CSS}</style></head><body>{nav}<h1>{h(title)}</h1>{body}
<script>{SORT_JS}</script></body></html>"""

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


def pipeline_active_for(db_path):
    """Return (pid, log_path) of a running image-pipeline.py for this DB, or (None, None)."""
    try:
        out = subprocess.run(
            ["pgrep", "-af", "image-pipeline.py"],
            capture_output=True, text=True, timeout=5
        )
    except Exception:
        return None, None
    for line in out.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, argv = parts
        if db_path not in argv:
            continue
        m = re.search(r"--log\s+(\S+)", argv)
        log_path = m.group(1) if m else ""
        return int(pid), log_path
    return None, None


def list_pipeline_logs(export_root, limit=10):
    if not export_root or not os.path.isdir(export_root):
        return []
    pat = os.path.join(export_root, "logs", "pipeline-*.log")
    paths = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
    return paths[:limit]


def panel_pipeline(db_path):
    """Render the 'Run Pipeline' panel for the DB detail page."""
    if not PIPELINE_PRESETS:
        return ('<div class="panel"><h2>Run Pipeline</h2>'
                '<p class="err">image-pipeline.py module failed to load.</p></div>')
    enc = urllib.parse.quote(db_path)
    pid, log_path = pipeline_active_for(db_path)

    # describe each preset
    boxes = []
    for name, stages in PIPELINE_PRESETS.items():
        chain = " &rarr; ".join(stages)
        boxes.append(
            f'<label style="display:block;padding:4px 0;cursor:pointer">'
            f'<input type="checkbox" name="preset" value="{h(name)}" '
            f'style="margin-right:8px"> '
            f'<b>{h(name)}</b> '
            f'<span style="color:var(--sub);font-size:12px">— {chain}</span>'
            f'</label>'
        )
    boxes_html = "".join(boxes)

    # active-run indicator
    active = ""
    if pid:
        log_q = urllib.parse.quote(log_path) if log_path else ""
        log_link = (f' &nbsp;<a href="/pipeline_log?db={enc}&log={log_q}">view log</a>'
                    if log_path else "")
        active = (f'<p><span class="badge running">running</span> '
                  f'pid {pid}{log_link}</p>')

    # recent logs
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        export_root = conn.execute(
            "SELECT export_root FROM image_info WHERE id=1").fetchone()
        conn.close()
        export_root = export_root[0] if export_root else ""
    except Exception:
        export_root = ""
    recent = list_pipeline_logs(export_root, 5)
    recent_html = ""
    if recent:
        items = []
        for p in recent:
            log_q = urllib.parse.quote(p)
            ts = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")
            items.append(f'<li><a href="/pipeline_log?db={enc}&log={log_q}">'
                         f'{h(os.path.basename(p))}</a> <span class="count">— {ts}</span></li>')
        recent_html = ('<p style="margin-top:10px;color:var(--sub);font-size:12px">'
                       'Recent runs:</p><ul style="margin-left:20px">'
                       + "".join(items) + "</ul>")

    disabled = "disabled" if pid else ""
    note = ('<p class="count" style="margin-top:6px">'
            'Pick one or more presets. Stages run sequentially; the page will '
            'refresh while the run is alive.</p>')

    return f"""
    <div class="panel">
      <h2>Run Pipeline</h2>
      {active}
      <form method="post" action="/run_pipeline">
        <input type="hidden" name="db" value="{h(db_path)}">
        <div style="display:flex;flex-direction:column;gap:2px">{boxes_html}</div>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
          <label><input type="checkbox" name="keep_going" value="1"> keep going on failure</label>
          <button type="submit" {disabled}>Run selected presets</button>
        </div>
        {note}
      </form>
      {recent_html}
    </div>
    """


def spawn_pipeline(db_path, presets, keep_going=False):
    """Spawn image-pipeline.py detached. Returns (log_path, pid) or raises."""
    if not all(p in PIPELINE_PRESETS for p in presets):
        raise ValueError("unknown preset(s) in form")

    # merged stage list, dedup preserving order
    seen = set()
    stages = []
    for p in presets:
        for s in PIPELINE_PRESETS[p]:
            if s not in seen:
                seen.add(s)
                stages.append(s)

    # resolve export_root for log path
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    row = conn.execute("SELECT export_root FROM image_info WHERE id=1").fetchone()
    conn.close()
    export_root = row[0] if row else ""
    if not export_root:
        raise ValueError("image_info.export_root missing — initialise the DB first")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = os.path.join(export_root, "logs", f"pipeline-{ts}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    cmd = [str(_PIPELINE_PATH), db_path, "--run", "--log", log_path]
    if keep_going:
        cmd.append("--keep-going")
    cmd += stages

    # write a header so the user sees something even before the script writes its own log
    with open(log_path, "a") as fh:
        fh.write(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] "
                 f"spawned: {shlex.join(cmd)}\n")

    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    return log_path, proc.pid


def page_pipeline_log(db_path, log_path, tail_kb=64):
    enc = urllib.parse.quote(db_path)
    if not log_path or not os.path.isfile(log_path):
        return page("Pipeline Log",
                    f'<div class="panel"><p class="err">Log not found: {h(log_path)}</p>'
                    f'<p><a href="/db?db={enc}">&larr; Back to DB</a></p></div>',
                    db_name=db_path, nav_extra=' &rsaquo; pipeline log')

    # read tail
    size = os.path.getsize(log_path)
    with open(log_path, "rb") as fh:
        if size > tail_kb * 1024:
            fh.seek(-tail_kb * 1024, os.SEEK_END)
            fh.readline()  # discard partial line
        body_text = fh.read().decode("utf-8", errors="replace")

    # alive iff some image-pipeline.py is still writing to this exact log file
    alive = False
    try:
        out = subprocess.run(["pgrep", "-af", "image-pipeline.py"],
                             capture_output=True, text=True, timeout=5)
        alive = log_path in out.stdout
    except Exception:
        alive = False

    refresh = '<meta http-equiv="refresh" content="3">' if alive else ""
    badge = ('<span class="badge running">running</span>' if alive
             else '<span class="badge ok">finished</span>')
    summary = ""
    if "Summary:" in body_text:
        summary = '<p class="count">Final summary at the end of the log.</p>'

    body = f"""
    {refresh}
    <div class="panel">
      <p>{badge} &nbsp; <code>{h(log_path)}</code> &nbsp;
         (<span class="count">{size:,} bytes</span>)
         &nbsp; <a href="/db?db={enc}">&larr; Back to DB</a></p>
      {summary}
      <pre class="mono" style="background:#0d1117;padding:10px;border-radius:4px;
                                max-height:70vh;overflow:auto;font-size:12px">{h(body_text)}</pre>
    </div>
    """
    return page("Pipeline Log", body, db_name=db_path, nav_extra=' &rsaquo; pipeline log')


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
    {panel_pipeline(db_path)}
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
    enc = urllib.parse.quote(db_path)
    # Section 1: filesystem-aware scored candidates (from image-detect-pictures.sh)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cand_rows = conn.execute(f"""
            SELECT f.id AS file_id, pc.score, pc.reason,
                   pc.camera_model, pc.taken_at, pc.width, pc.height,
                   f.name, f.path, f.size_bytes
            FROM   picture_candidates pc
            LEFT JOIN files f ON f.id = pc.file_id
            ORDER  BY pc.score DESC, f.path
            LIMIT  {limit}""").fetchall()
        conn.close()

        if not cand_rows:
            cand_html = ('<p class="count">No filesystem-indexed picture candidates. '
                         'The disk may have no image files in its partition table, '
                         'or the detect-pictures stage found none.</p>')
        else:
            t = ('<p class="count">' + str(len(cand_rows)) + ' row(s)</p>'
                 '<div style="overflow-x:auto"><table>'
                 '<tr><th>Score</th><th>Reason</th><th>Camera</th><th>Taken</th>'
                 '<th>WxH</th><th>Name</th><th>Path</th><th>Size (B)</th><th>View</th></tr>')
            for r in cand_rows:
                fid = r["file_id"]
                view = ""
                if fid is not None:
                    view = (f'<a href="/export_view?db={enc}&file_id={fid}" '
                            f'target="_blank" title="Extract via icat and view">'
                            f'&#128247;</a>')
                wh = ""
                if r["width"] and r["height"]:
                    wh = f"{r['width']}x{r['height']}"
                t += ('<tr>'
                      f'<td style="text-align:right">{h(r["score"])}</td>'
                      f'<td>{h(r["reason"])}</td>'
                      f'<td>{h(r["camera_model"])}</td>'
                      f'<td style="white-space:nowrap">{h(r["taken_at"])}</td>'
                      f'<td>{h(wh)}</td>'
                      f'<td>{h(r["name"])}</td>'
                      f'<td style="word-break:break-all">{h(r["path"])}</td>'
                      f'<td style="text-align:right">{(r["size_bytes"] or 0):,}</td>'
                      f'<td style="text-align:center">{view}</td></tr>')
            t += '</table></div>'
            cand_html = t
    except Exception as e:
        cand_html = f'<p class="err">{h(str(e))}</p>'

    # Section 2: image artifacts with per-row View link (Option 2) + gallery link (Option 1)
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
      <p style="color:var(--sub);font-size:12px;margin-bottom:6px">
        Scored by <code>image-detect-pictures.sh</code> from the filesystem index (TSK/fiwalk).
        Files live inside the disk image; the View column extracts each on demand via icat.
        &nbsp;&nbsp;
        <a href="/gallery?db={enc}&mode=fs"
           style="background:#0f3460;padding:3px 10px;border-radius:3px;color:#7eb8f7"
           title="Browse filesystem picture candidates as a paginated thumbnail grid">&#128443; Gallery View</a>
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
            sql = f"""SELECT f.name, f.path, f.size_bytes, f.mtime,
                             f.allocated, f.deleted, fs.fs_type
                      FROM files f
                      LEFT JOIN filesystems fs ON fs.partition_id = f.partition_id
                      WHERE f.path LIKE ? OR f.name LIKE ?
                      ORDER BY f.size_bytes DESC LIMIT {limit}"""
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (pattern, pattern)).fetchmany(limit)
            cols = ["name", "path", "size_bytes", "mtime",
                    "allocated", "deleted", "fs_type"]
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
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""SELECT method, mime_type, size_bytes, relative_path, full_path,
                       sha256, created_at
                FROM recovered_artifacts {method_filter}
                ORDER BY created_at DESC LIMIT {limit}"""
        ).fetchall()
        methods = [r[0] for r in conn.execute(
            "SELECT DISTINCT method FROM recovered_artifacts ORDER BY method").fetchall()]
        conn.close()

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

        tbl = ('<p class="count">' + f"{len(rows):,}" + ' row(s)</p>'
               '<div style="overflow-x:auto"><table>'
               '<tr><th>Method</th><th>MIME</th><th>Size (B)</th><th>Path</th>'
               '<th>SHA256</th><th>Created</th><th>View</th></tr>')
        for r in rows:
            fp = r["full_path"] or ""
            mime = r["mime_type"] or ""
            sha = r["sha256"] or ""
            sha_short = (sha[:12] + "…") if len(sha) > 12 else sha
            view = ""
            if fp:
                fenc = urllib.parse.quote(fp)
                if mime.startswith("image/"):
                    icon = "&#128247;"   # 📷
                    title = "Open image in new tab"
                elif mime.startswith("text/") or mime in ("application/pdf",
                                                          "application/json",
                                                          "application/xml"):
                    icon = "&#128196;"   # 📄
                    title = "Open file in new tab"
                else:
                    icon = "&#128190;"   # 💾
                    title = "Download / open raw bytes"
                view = (f'<a href="/file?path={fenc}" target="_blank" '
                        f'title="{title}">{icon}</a>')
            tbl += (
                f'<tr><td>{h(r["method"])}</td>'
                f'<td>{h(mime)}</td>'
                f'<td style="text-align:right">{(r["size_bytes"] or 0):,}</td>'
                f'<td style="word-break:break-all">{h(r["relative_path"] or "")}</td>'
                f'<td style="font-family:monospace;font-size:11px" title="{h(sha)}">{h(sha_short)}</td>'
                f'<td style="white-space:nowrap">{h(r["created_at"] or "")}</td>'
                f'<td style="text-align:center">{view}</td></tr>'
            )
        tbl += '</table></div>'

        body = form + f'<div class="panel">{tbl}</div>'
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

def export_file_via_icat(db_path, file_id, root):
    """Idempotently extract a filesystem-aware file via image-export.sh.
    Returns (bytes, mime) or (None, err). Skips re-extraction if dest already exists."""
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
    dest_dir = os.path.join(export_root, "exports", "files")
    dest_path = os.path.join(dest_dir, safe_name)

    # If already extracted with non-zero size, just serve it
    if not (os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0):
        os.makedirs(dest_dir, exist_ok=True)
        script = Path(__file__).parent / "image-export.sh"
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

    return _safe_file_read(dest_path, root)


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


def page_gallery_fs(db_path, pg=0, per_page=48, all_images=False):
    """Gallery for filesystem-aware picture candidates. Each thumbnail is fetched
    via /export_view, which extracts on demand via icat and caches under exports/files/."""
    enc = urllib.parse.quote(db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        total = conn.execute("""
            SELECT COUNT(*) FROM picture_candidates pc
            JOIN files f ON f.id = pc.file_id
            WHERE f.size_bytes IS NOT NULL AND f.size_bytes > 0
        """).fetchone()[0]
        base_sql = """
            SELECT f.id AS file_id, f.name, f.path, f.size_bytes,
                   pc.score, pc.camera_model, pc.taken_at
            FROM picture_candidates pc
            JOIN files f ON f.id = pc.file_id
            WHERE f.size_bytes IS NOT NULL AND f.size_bytes > 0
            ORDER BY pc.score DESC, f.path
        """
        if all_images:
            rows = conn.execute(base_sql).fetchall()
        else:
            offset = pg * per_page
            rows = conn.execute(base_sql + f" LIMIT {per_page} OFFSET {offset}").fetchall()
        conn.close()
    except Exception as e:
        return page("Image Gallery (filesystem)",
                    f'<div class="panel err">{h(str(e))}</div>',
                    db_name=db_path, nav_extra=" &rsaquo; gallery (fs)")

    imgs = ""
    for r in rows:
        fid = r["file_id"]
        sz = r["size_bytes"] or 0
        name = r["name"] or f"file-{fid}"
        cam = r["camera_model"] or ""
        taken = r["taken_at"] or ""
        meta = " — ".join(filter(None, [name, f"{sz:,} B", f"score {r['score']}", cam, taken]))
        url = f"/export_view?db={enc}&file_id={fid}"
        imgs += (
            f'<a href="{url}" target="_blank" class="img-card" title="{h(meta)}">'
            f'<img src="{url}" loading="lazy" '
            f'style="width:150px;height:112px;object-fit:cover;border-radius:4px;'
            f'border:1px solid #333;background:#111"></a>'
        )

    btn = 'style="background:#0f3460;padding:3px 12px;border-radius:3px;color:#7eb8f7"'
    if all_images:
        header = f'<p class="count">{total:,} candidate(s) — all on one page (extraction is on-demand)</p>'
        toggle = f'<a href="/gallery?db={enc}&mode=fs" {btn}>&#9660; Paginated view</a>'
        pager = ""
    else:
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = pg * per_page
        pg_base = f"/gallery?db={enc}&mode=fs"
        prev_link = f'<a href="{pg_base}&page={pg-1}">&larr; Prev</a>' if pg > 0 else ""
        next_link = f'<a href="{pg_base}&page={pg+1}">Next &rarr;</a>' if (offset + per_page) < total else ""
        pager = " &nbsp; ".join(filter(None, [prev_link, next_link]))
        header = f'<p class="count">{total:,} candidate(s) &mdash; page {pg+1} of {total_pages} &nbsp; ({per_page} per page)</p>'
        all_href = f"/gallery?db={enc}&mode=fs&all=1"
        toggle = f'<a href="{all_href}" {btn}>&#9651; View all on one page</a>'

    note = ('<p class="count" style="margin-top:6px">'
            'Each thumbnail is extracted on demand via <code>icat</code> and cached '
            'under <code>exports/files/</code>. First load may take a moment per image.</p>')

    body = f"""
    <div class="panel">
      {header}{note}
      <p style="margin-top:6px">{toggle}</p>
    </div>
    <div class="panel">
      <div style="display:flex;flex-wrap:wrap;gap:6px">{imgs or '<p class="count">No filesystem candidates with size > 0.</p>'}</div>
    </div>
    {"<div class='panel'><p>" + pager + "</p></div>" if pager else ""}
    """
    return page("Image Gallery (filesystem)", body, db_name=db_path,
                nav_extra=" &rsaquo; gallery (fs)")


def page_gallery(db_path, root, pg=0, per_page=48, all_images=False, search=""):
    """Paginated (or all-on-one-page) image gallery with LLM description search."""
    enc = urllib.parse.quote(db_path)
    abs_root = os.path.realpath(root)
    search = search.strip()

    # Base query: LEFT JOIN findings for llava descriptions.
    # When searching, switch to INNER JOIN so only tagged+matching images appear.
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        tagged_total = conn.execute(
            "SELECT COUNT(*) FROM recovered_artifacts ra "
            "JOIN findings f ON f.artifact_id=ra.id AND f.source_tool='llava' AND f.key='description' "
            "WHERE ra.mime_type LIKE 'image/%'"
        ).fetchone()[0]

        if search:
            count_sql = (
                "SELECT COUNT(*) FROM recovered_artifacts ra "
                "JOIN findings f ON f.artifact_id=ra.id AND f.source_tool='llava' AND f.key='description' "
                "WHERE ra.mime_type LIKE 'image/%' AND f.value LIKE ?"
            )
            total = conn.execute(count_sql, (f"%{search}%",)).fetchone()[0]
            base_sql = (
                "SELECT ra.id, ra.full_path, ra.mime_type, ra.size_bytes, ra.method, f.value AS description "
                "FROM recovered_artifacts ra "
                "JOIN findings f ON f.artifact_id=ra.id AND f.source_tool='llava' AND f.key='description' "
                "WHERE ra.mime_type LIKE 'image/%' AND f.value LIKE ? "
                "ORDER BY ra.method, ra.full_path"
            )
            base_params = (f"%{search}%",)
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM recovered_artifacts WHERE mime_type LIKE 'image/%'"
            ).fetchone()[0]
            base_sql = (
                "SELECT ra.id, ra.full_path, ra.mime_type, ra.size_bytes, ra.method, "
                "f.value AS description "
                "FROM recovered_artifacts ra "
                "LEFT JOIN findings f ON f.artifact_id=ra.id AND f.source_tool='llava' AND f.key='description' "
                "WHERE ra.mime_type LIKE 'image/%' "
                "ORDER BY ra.method, ra.full_path"
            )
            base_params = ()

        if all_images:
            rows = conn.execute(base_sql, base_params).fetchall()
        else:
            offset = pg * per_page
            rows = conn.execute(
                base_sql + f" LIMIT {per_page} OFFSET {offset}", base_params
            ).fetchall()
        conn.close()
    except Exception as e:
        body = f'<div class="panel err">{h(str(e))}</div>'
        return page("Image Gallery", body, db_name=db_path)

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
        tip = f"{Path(fp).name} — {method} — {sz:,} B"
        desc_div = (f'<div class="desc">{h(desc[:200])}</div>' if desc else "")
        imgs += (
            f'<a href="/file?path={fenc}" target="_blank" class="img-card" title="{h(tip)}">'
            f'<img src="/file?path={fenc}" loading="lazy" '
            f'style="width:150px;height:112px;object-fit:cover;border-radius:4px;'
            f'border:1px solid #333;background:#111">'
            f'{desc_div}</a>'
        )

    # Controls
    senc = urllib.parse.quote(search)
    btn = 'style="background:#0f3460;padding:3px 12px;border-radius:3px;color:#7eb8f7"'
    search_box = f"""<form method="get" action="/gallery" style="display:inline-flex;gap:6px;align-items:center">
      <input type="hidden" name="db" value="{h(db_path)}">
      <input type="text" name="search" value="{h(search)}" placeholder="Search descriptions…"
             style="width:220px" title="Search llava descriptions — only tagged images are searched">
      <button type="submit">Search</button>
      {"<a href='/gallery?db=" + enc + "' style='color:var(--sub)'>Clear</a>" if search else ""}
    </form>"""

    if all_images:
        header = f'<p class="count">{total:,} image(s) — all on one page</p>'
        toggle = f'<a href="/gallery?db={enc}{"&search=" + senc if search else ""}" {btn}>&#9660; Paginated view</a>'
        pager = ""
    else:
        total_pages = max(1, (total + per_page - 1) // per_page)
        offset = pg * per_page
        pg_base = f"/gallery?db={enc}" + (f"&search={senc}" if search else "")
        prev_link = f'<a href="{pg_base}&page={pg-1}">&larr; Prev</a>' if pg > 0 else ""
        next_link = f'<a href="{pg_base}&page={pg+1}">Next &rarr;</a>' if (offset + per_page) < total else ""
        pager = " &nbsp; ".join(filter(None, [prev_link, next_link]))
        header = f'<p class="count">{total:,} image(s) &mdash; page {pg+1} of {total_pages} &nbsp; ({per_page} per page)</p>'
        all_href = f"/gallery?db={enc}&all=1" + (f"&search={senc}" if search else "")
        toggle = f'<a href="{all_href}" {btn}>&#9651; View all on one page</a>'

    tag_note = (f'<span class="count" style="margin-left:12px">'
                f'&#127381; {tagged_total:,} of {total:,} tagged by llava</span>'
                if not search else
                f'<span class="count" style="margin-left:12px">Showing {total:,} matches in llava descriptions</span>')

    body = f"""
    <div class="panel">
      {header}{tag_note}
      <p style="margin-top:10px;display:flex;align-items:center;flex-wrap:wrap;gap:8px">
        {toggle} &nbsp;&nbsp; {search_box}
      </p>
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
                search = self.qsval("search")
                mode = self.qsval("mode") or "carved"
                try:
                    pg = max(0, int(self.qsval("page", "0") or "0"))
                except ValueError:
                    pg = 0
                if mode == "fs":
                    self.send_html(page_gallery_fs(db, pg, all_images=all_images))
                else:
                    self.send_html(page_gallery(db, self.root, pg,
                                                all_images=all_images, search=search))
            elif p == "/export_view":
                file_id = self.qsval("file_id")
                data, mime_or_err = export_file_via_icat(db, file_id, self.root)
                if data is None:
                    self.send_html(page("Error",
                        f'<p class="err">{h(mime_or_err)}</p>'), 500)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", mime_or_err)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "private, max-age=300")
                    self.end_headers()
                    self.wfile.write(data)
                return
            elif p == "/pipeline_log":
                log_path = self.qsval("log")
                self.send_html(page_pipeline_log(db, log_path))
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
        elif p == "/run_pipeline":
            if not db or not os.path.isfile(db):
                self.send_html(page("Error",
                    '<p class="err">missing or unknown db</p>'), 400)
                return
            presets = params.get("preset", [])
            if not presets:
                self.send_html(page("Error",
                    f'<p class="err">no presets selected</p>'
                    f'<p><a href="/db?db={urllib.parse.quote(db)}">&larr; back</a></p>'), 400)
                return
            existing_pid, existing_log = pipeline_active_for(db)
            if existing_pid:
                # already running — redirect to the in-flight log
                target = (f'/pipeline_log?db={urllib.parse.quote(db)}'
                          f'&log={urllib.parse.quote(existing_log)}'
                          if existing_log else f'/db?db={urllib.parse.quote(db)}')
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return
            keep_going = pval("keep_going") == "1"
            try:
                log_path, _pid = spawn_pipeline(db, presets, keep_going=keep_going)
            except Exception as e:
                self.send_html(page("Error",
                    f'<p class="err">spawn failed: {h(str(e))}</p>'
                    f'<p><a href="/db?db={urllib.parse.quote(db)}">&larr; back</a></p>'), 500)
                return
            self.send_response(302)
            self.send_header("Location",
                f'/pipeline_log?db={urllib.parse.quote(db)}'
                f'&log={urllib.parse.quote(log_path)}')
            self.end_headers()
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
