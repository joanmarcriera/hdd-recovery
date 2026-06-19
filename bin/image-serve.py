#!/usr/bin/env python3
"""
Read-only web query interface for hdd-recovery SQLite databases.

Serves a local HTTP UI that lets you browse all recovery databases under
a given root directory.  All SQL executed is restricted to SELECT/WITH.

Usage:
  image-serve.py [--root DIR] [--port PORT] [--host HOST]
"""
import argparse, glob, gzip, hashlib, html, http.server, importlib.util, io, json, mimetypes, os, re
import shlex, sqlite3, subprocess, sys, tempfile, threading, time, urllib.parse
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
.chip{display:inline-block;padding:1px 5px;margin:1px;border-radius:3px;font-size:10px;font-weight:bold}
.chip.pending{background:transparent;color:#556;border:1px solid #2a2a4a}
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

APP_VERSION = os.environ.get("APP_VERSION", "dev")

# Hard cap for the gallery "View all" mode: emitting every <img> for a DB with
# tens of thousands of carved images builds an enormous DOM and that many /thumb
# requests. Show the first N and tell the user to paginate/filter for the rest.
GALLERY_ALL_CAP = 1000


def page(title, body, db_name="", nav_extra="", head_extra=""):
    home = '<a href="/">&#8962; home</a>'
    db_link = f' &rsaquo; <a href="/db?db={urllib.parse.quote(db_name)}">{h(Path(db_name).name)}</a>' if db_name else ""
    nav = f'<nav>{home}{db_link}{nav_extra}</nav>'
    footer = (f'<footer style="margin-top:24px;color:var(--sub);font-size:11px">'
              f'hdd-forensics &middot; build {h(APP_VERSION)}</footer>')
    return f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>{h(title)} &mdash; hdd-recovery</title>
<style>{CSS}</style>{head_extra}</head><body>{nav}<h1>{h(title)}</h1>{body}
{footer}<script>{SORT_JS}</script></body></html>"""

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

def run_query(db_path, sql, params=(), limit=5000):
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
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(sql).fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def db_export_root(db_path):
    return query_scalar(db_path, "SELECT export_root FROM image_info WHERE id=1", "") or ""

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

# Analysis DBs live beside images (e.g. <root>/images/*.analysis.sqlite). These
# subdirectories never contain DBs but can hold millions of carved/feature files
# — a recursive '**' glob over them took 20-40 s on a real export tree. Pruning
# them (plus a depth cap) makes discovery effectively instant.
_DISCOVERY_PRUNE = {
    "exports", "recovered", "indexes", "winmem", "photorec", "carved",
    "hits", "state", "reports", "logs", "manifests", "structure",
}
_DISCOVERY_MAX_DEPTH = 4


def find_databases(root, max_depth=_DISCOVERY_MAX_DEPTH):
    root = os.path.abspath(root)
    base = root.rstrip(os.sep).count(os.sep)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.count(os.sep) - base
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in _DISCOVERY_PRUNE and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".analysis.sqlite"):
                found.append(os.path.join(dirpath, fn))
    return sorted(set(found))

# ── memory / swap status ─────────────────────────────────────────────────────

def mem_status():
    """Read /proc/meminfo. Returns dict (ram_total, ram_avail, swap_total, swap_free) in MB."""
    try:
        info = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                info[k.strip()] = int(v.split()[0])  # kB
        return {
            "ram_total":  info.get("MemTotal",    0) // 1024,
            "ram_avail":  info.get("MemAvailable",0) // 1024,
            "swap_total": info.get("SwapTotal",   0) // 1024,
            "swap_free":  info.get("SwapFree",    0) // 1024,
        }
    except Exception:
        return None


def _bar(pct, color):
    return (f'<div style="background:#1a2f4a;border-radius:3px;height:8px;'
            f'width:180px;display:inline-block;vertical-align:middle">'
            f'<div style="background:{color};width:{min(pct,100)}%;height:100%;'
            f'border-radius:3px"></div></div>')


def mem_panel():
    m = mem_status()
    if not m:
        return ""
    ram_used  = m["ram_total"]  - m["ram_avail"]
    swap_used = m["swap_total"] - m["swap_free"]
    ram_pct   = int(100 * ram_used  / m["ram_total"])  if m["ram_total"]  else 0
    swap_pct  = int(100 * swap_used / m["swap_total"]) if m["swap_total"] else 0

    ram_col  = "#cc2222" if ram_pct  > 90 else "#cc9900" if ram_pct  > 75 else "#22aa44"
    swap_col = "#cc2222" if swap_pct > 90 else "#cc9900" if swap_pct > 75 else "#22aa44"

    swap_label = (f'{m["swap_free"]:,} MB free / {m["swap_total"]:,} MB total'
                  if m["swap_total"] else "none configured")

    warn = ""
    if m["swap_total"] == 0:
        warn = ('<p style="color:#cc9900;margin-top:6px">&#9888; No swap configured. '
                'Long carving and bulk_extractor stages may OOM on large images. '
                'Add swap: <code>fallocate -l 32G /root/swapfile2 &amp;&amp; '
                'mkswap /root/swapfile2 &amp;&amp; swapon /root/swapfile2</code></p>')
    elif m["ram_avail"] < 2048 and m["swap_free"] < 8192:
        warn = ('<p style="color:#cc2222;margin-top:6px">&#9888; RAM critically low and swap headroom '
                'is under 8 GB — active carving stages risk OOM. '
                'Add more swap or wait for current stage to finish.</p>')
    elif m["ram_avail"] < 4096 and m["swap_free"] < 16384:
        warn = ('<p style="color:#cc9900;margin-top:6px">&#9888; RAM is below 4 GB available and '
                'swap headroom is limited. Consider adding swap before starting heavy stages: '
                '<code>swapon -s</code> to check, '
                '<code>swapon /path/to/existing/swapfile</code> to activate.</p>')

    return f"""<div class="panel" style="padding:10px 14px;font-size:13px">
      <span style="margin-right:24px">
        <b>RAM</b>&nbsp; {m["ram_avail"]:,}&thinsp;MB free / {m["ram_total"]:,}&thinsp;MB total
        &nbsp; {_bar(ram_pct, ram_col)}
      </span>
      <span>
        <b>Swap</b>&nbsp; {swap_label}
        {"&nbsp; " + _bar(swap_pct, swap_col) if m["swap_total"] else ""}
      </span>
      {warn}
    </div>"""


# ── pages ─────────────────────────────────────────────────────────────────────

def _human_size(n):
    """Bytes → compact human string (e.g. 160041885696 → '149 GB')."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            s = f"{n:.1f}".rstrip("0").rstrip(".")
            return f"{s} {unit}"
        n /= 1024


# Home dashboard stage groups: which recorded scan_runs.stage names roll up into
# each at-a-glance indicator. Mirrors the pipeline presets but tolerant of the
# extra/renamed stage keys real DBs contain (e.g. llava-tag-photos).
_HOME_STAGE_GROUPS = [
    ("fast",    ["structure-scan", "index-tsk", "detect-wallets", "detect-pictures"]),
    ("carve",   ["carve-foremost", "carve-scalpel", "carve-recoverjpeg",
                 "carve-magicrescue", "photorec-broad"]),
    ("recover", ["ext-recover", "ntfs-recover", "fat-recover", "xfs-recover",
                 "btrfs-recover", "extract-winmem"]),
    ("index",   ["bulk-extractor-raw", "bulk-extractor-recovered", "yara-scan",
                 "recoll-index", "plaso-timeline", "enrich-trid"]),
    ("photos",  ["enrich-photos", "dedup-photos", "tag-photos", "llava-tag-photos"]),
    ("wallet",  ["wallet-inspect", "text-seed-scan", "crack-wallet", "btcrecover"]),
    ("win",     ["ntfs-artifact-summary", "regripper", "rifiuti2"]),
]


def _stage_group_chips(stage_status):
    """Compact per-group status chips from a {stage: latest_status} dict."""
    chips = []
    for label, members in _HOME_STAGE_GROUPS:
        present = {s: stage_status[s] for s in members if s in stage_status}
        low = [str(v).lower() for v in present.values()]
        if not present:
            cls = "pending"
        elif "running" in low:
            cls = "running"
        elif any(v in ("failed", "error") for v in low):
            cls = "failed"
        elif any(v == "partial" for v in low):
            cls = "partial"
        elif all(v == "ok" for v in low):
            cls = "ok"
        else:
            cls = "partial"
        if present:
            tip = f"{label}: " + ", ".join(f"{s}={v}" for s, v in present.items())
        else:
            tip = f"{label}: not run"
        chips.append(f'<span class="chip {cls}" title="{h(tip)}">{label}</span>')
    return "".join(chips)


def _home_db_stats(db_path):
    """Fetch all homepage row fields for one DB over a single connection."""
    stats = {"image_path": "?", "image_size": 0, "total_files": 0,
             "total_artifacts": 0, "wallet_hits": 0, "last_run": "—",
             "map_path": "", "stage_status": {}}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        return stats
    try:
        def scalar(sql, default):
            try:
                row = conn.execute(sql).fetchone()
                return row[0] if row and row[0] is not None else default
            except Exception:
                return default
        try:
            info = conn.execute(
                "SELECT image_path, ddrescue_map_path, image_size_bytes "
                "FROM image_info WHERE id=1"
            ).fetchone()
        except Exception:
            info = None
        if info:
            stats["image_path"] = info[0] or "?"
            stats["map_path"] = info[1] or ""
            stats["image_size"] = info[2] or 0
        # fall back to the on-disk image size if the column is unset
        if not stats["image_size"] and stats["image_path"] not in ("", "?"):
            try:
                stats["image_size"] = os.path.getsize(stats["image_path"])
            except OSError:
                pass
        stats["total_files"] = scalar("SELECT COUNT(*) FROM files", 0)
        stats["total_artifacts"] = scalar("SELECT COUNT(*) FROM recovered_artifacts", 0)
        stats["wallet_hits"] = scalar("SELECT COUNT(*) FROM wallet_candidates", 0)
        stats["last_run"] = scalar(
            "SELECT MAX(COALESCE(ended_at, started_at)) FROM scan_runs", "—")
        # latest status per stage (highest id wins)
        try:
            latest = {}
            for stage, status in conn.execute(
                "SELECT stage, status FROM scan_runs ORDER BY id"
            ):
                if stage:
                    latest[stage] = status
            stats["stage_status"] = latest
        except Exception:
            pass
    finally:
        conn.close()
    return stats


# Short-lived cache so rapid reloads / the 20 s pipeline meta-refresh do not
# re-scan every DB and re-run pgrep each time.
_HOME_CACHE: dict[str, tuple[float, str]] = {}
_HOME_CACHE_TTL = 5.0


def page_home(root):
    cached = _HOME_CACHE.get(root)
    if cached and (time.monotonic() - cached[0]) < _HOME_CACHE_TTL:
        return cached[1]

    dbs = find_databases(root)
    if not dbs:
        body = f'<div class="panel"><p>No *.analysis.sqlite files found under <code>{h(root)}</code>.</p></div>'
        return page("Recovery Dashboard", body)

    rows_html = ""
    for db_path in dbs:
        st = _home_db_stats(db_path)
        image_path = st["image_path"]
        # primary label is the image name; fall back to the db stem if unknown
        if image_path and image_path != "?":
            img_name = Path(image_path).name
        else:
            dbn = Path(db_path).name
            img_name = dbn[:-len(".analysis.sqlite")] if dbn.endswith(".analysis.sqlite") else dbn
        map_p = st["map_path"]
        enc = urllib.parse.quote(db_path)
        map_cell = (f'<a href="/mapview?db={enc}" title="ddrescue map">&#128209;</a>'
                    if map_p and os.path.isfile(map_p) else "—")
        link = f'/db?db={enc}'
        rows_html += f"""<tr>
          <td><a href="{link}">{h(img_name)}</a></td>
          <td style="white-space:nowrap">{h(_human_size(st["image_size"]))}</td>
          <td>{st["total_files"]:,}</td>
          <td>{st["total_artifacts"]:,}</td>
          <td>{st["wallet_hits"]:,}</td>
          <td style="line-height:1.7">{_stage_group_chips(st["stage_status"])}</td>
          <td style="white-space:nowrap">{h(str(st["last_run"])[:19])}</td>
          <td style="text-align:center">{map_cell}</td>
        </tr>"""

    # Active pipeline banner — one pgrep shared across all DBs.
    pipeline_banner = ""
    pgrep_lines = _pipeline_pgrep_lines()
    for db_path in dbs:
        pid, log_path = pipeline_active_for(db_path, lines=pgrep_lines)
        if pid:
            enc2 = urllib.parse.quote(db_path)
            log_q = urllib.parse.quote(log_path) if log_path else ""
            log_link = (f' &nbsp; <a href="/pipeline_log?db={enc2}&log={log_q}">view log</a>'
                        if log_path else "")
            pipeline_banner += (
                f'<div class="panel" style="border-left:4px solid #22aa44;padding:8px 14px">'
                f'<span class="badge running">running</span> &nbsp; '
                f'<b>{h(Path(db_path).stem)}</b> &nbsp; pid {pid}{log_link}'
                f'</div>'
            )

    body = f"""{mem_panel()}{pipeline_banner}<div class="panel">
      <p class="count" style="display:flex;justify-content:space-between;align-items:center">
        <span>{len(dbs)} image(s) found under <code>{h(root)}</code></span>
        <a href="/queue" style="background:#0f3460;padding:4px 12px;border-radius:3px;color:#7eb8f7"
           title="Queue fast/carve/etc. across multiple images">&#9654; Queue work</a>
      </p>
      <table style="margin-top:10px">
        <tr><th>Image</th><th>Size</th>
            <th>Files</th><th>Artifacts</th><th>Wallets</th>
            <th title="Pipeline stage groups that have run (hover a chip for detail)">Stages</th>
            <th>Last Run</th><th>Map</th></tr>
        {rows_html}
      </table>
    </div>"""
    head_extra = '<meta http-equiv="refresh" content="20">' if pipeline_banner else ""
    html_out = page("Recovery Dashboard", body, head_extra=head_extra)
    _HOME_CACHE[root] = (time.monotonic(), html_out)
    return html_out


def _pipeline_pgrep_lines():
    """Run a single `pgrep -af image-pipeline.py` and return its output lines."""
    try:
        out = subprocess.run(
            ["pgrep", "-af", "image-pipeline.py"],
            capture_output=True, text=True, timeout=5
        )
        return out.stdout.splitlines()
    except Exception:
        return []


def pipeline_active_for(db_path, lines=None):
    """Return (pid, log_path) of a running image-pipeline.py for this DB, or (None, None).

    Pass `lines` (from _pipeline_pgrep_lines) to avoid re-running pgrep per DB.
    """
    if lines is None:
        lines = _pipeline_pgrep_lines()
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, argv = parts
        if db_path not in argv:
            continue
        m = re.search(r"--log\s+(\S+)", argv)
        if m:
            log_path = m.group(1)
        else:
            recent_logs = list_pipeline_logs(db_export_root(db_path), 1)
            log_path = recent_logs[0] if recent_logs else ""
        return int(pid), log_path
    return None, None


def list_pipeline_logs(export_root, limit=10):
    if not export_root or not os.path.isdir(export_root):
        return []
    pat = os.path.join(export_root, "logs", "pipeline-*.log")
    paths = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
    return paths[:limit]


def _db_log_path(db_path, log_path):
    """Resolve a log path only if it belongs to this DB's export_root/logs."""
    if not log_path:
        return "", "Log path is missing."
    export_root = db_export_root(db_path)
    if not export_root:
        return "", "image_info.export_root missing for this database."
    logs_root = os.path.realpath(os.path.join(export_root, "logs"))
    abs_path = os.path.realpath(log_path)
    if not abs_path.startswith(logs_root + os.sep):
        return "", "Forbidden: log path is outside this image's logs directory."
    if not os.path.isfile(abs_path):
        return "", f"Log not found: {log_path}"
    return abs_path, ""


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
    export_root = db_export_root(db_path)
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
          <label><input type="checkbox" name="skip_done" value="1" checked> skip stages already ok</label>
          <label><input type="checkbox" name="keep_going" value="1"> keep going on failure</label>
          <button type="submit" {disabled}>Run selected presets</button>
        </div>
        {note}
      </form>
      {recent_html}
    </div>
    """


def spawn_pipeline(db_path, presets, keep_going=False, skip_done=False):
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
    export_root = db_export_root(db_path)
    if not export_root:
        raise ValueError("image_info.export_root missing — initialise the DB first")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = os.path.join(export_root, "logs", f"pipeline-{ts}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    cmd = [str(_PIPELINE_PATH), db_path, "--run", "--log", log_path]
    if keep_going:
        cmd.append("--keep-going")
    if skip_done:
        cmd.append("--skip-done")
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


# ── multi-image queue ─────────────────────────────────────────────────────────

_QUEUE_PATH = Path(__file__).resolve().parent / "image-queue.py"


def _merge_preset_stages(presets):
    """Resolve a list of preset names to a deduplicated, ordered stage list."""
    if not all(p in PIPELINE_PRESETS for p in presets):
        raise ValueError("unknown preset(s)")
    seen, stages = set(), []
    for p in presets:
        for s in PIPELINE_PRESETS[p]:
            if s not in seen:
                seen.add(s)
                stages.append(s)
    return stages


def _queue_log_dir(root):
    """First writable dir for queue logs: <root>/queue-logs, else a temp dir."""
    for cand in (os.path.join(root, "queue-logs"),
                 os.path.join(tempfile.gettempdir(), "hdd-queue-logs")):
        try:
            os.makedirs(cand, exist_ok=True)
            if os.access(cand, os.W_OK):
                return cand
        except OSError:
            continue
    return tempfile.gettempdir()


def build_queue_cmd(dbs, stages, jobs, skip_done, keep_going):
    """Build the image-queue.py command line (pure/testable)."""
    cmd = [sys.executable, str(_QUEUE_PATH), "--jobs", str(jobs),
           "--stages", ",".join(stages)]
    if skip_done:
        cmd.append("--skip-done")
    if keep_going:
        cmd.append("--keep-going")
    cmd += list(dbs)
    return cmd


def queue_active():
    """Return (pid, argv) of a running image-queue.py, or (None, None)."""
    try:
        out = subprocess.run(["pgrep", "-af", "image-queue.py"],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None, None
    for line in out.stdout.splitlines():
        pid, _, argv = line.partition(" ")
        if "image-queue.py" in argv and pid.strip().isdigit():
            return int(pid), argv
    return None, None


def spawn_queue(root, dbs, presets, jobs=1, skip_done=True, keep_going=True):
    """Spawn image-queue.py detached over several DBs. Returns (log_path, pid)."""
    dbs = [d for d in dbs if d and os.path.isfile(d)]
    if not dbs:
        raise ValueError("no valid databases selected")
    stages = _merge_preset_stages(presets)
    if not stages:
        raise ValueError("no stages resolved from presets")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = os.path.join(_queue_log_dir(root), f"queue-{ts}.log")
    with open(log_path, "a") as fh:
        fh.write(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] "
                 f"queue spawned: {len(dbs)} image(s), jobs={jobs}, "
                 f"presets={','.join(presets)}\n")

    cmd = build_queue_cmd(dbs, stages, jobs, skip_done, keep_going)
    logfh = open(log_path, "a")
    proc = subprocess.Popen(cmd, stdout=logfh, stderr=subprocess.STDOUT,
                            start_new_session=True)
    return log_path, proc.pid


def page_queue(root):
    """GET /queue — pick images + presets and launch a multi-image batch."""
    if not PIPELINE_PRESETS:
        return page("Queue", '<div class="panel err">image-pipeline.py failed to load.</div>')
    dbs = find_databases(root)
    qpid, _ = queue_active()

    active = ""
    if qpid:
        active = (f'<div class="panel" style="border-left:4px solid #22aa44">'
                  f'<span class="badge running">running</span> a queue is active '
                  f'(pid {qpid}). <a href="/queue_log">view queue log</a></div>')

    # image checkboxes (default all checked)
    img_rows = ""
    for db_path in dbs:
        st = _home_db_stats(db_path)
        img_name = (Path(st["image_path"]).name
                    if st["image_path"] not in ("", "?") else Path(db_path).name)
        enc = h(db_path)
        img_rows += (
            f'<label style="display:block;padding:3px 0;cursor:pointer">'
            f'<input type="checkbox" name="db" value="{enc}" checked style="margin-right:8px">'
            f'{h(img_name)} '
            f'<span style="color:var(--sub);font-size:11px">{h(_human_size(st["image_size"]))}</span> '
            f'<span style="margin-left:6px">{_stage_group_chips(st["stage_status"])}</span>'
            f'</label>'
        )

    preset_boxes = "".join(
        f'<label style="display:inline-block;margin-right:14px;cursor:pointer">'
        f'<input type="checkbox" name="preset" value="{h(name)}"'
        f'{" checked" if name == "fast" else ""}> <b>{h(name)}</b> '
        f'<span style="color:var(--sub);font-size:11px">{" &rarr; ".join(stages)}</span>'
        f'</label>'
        for name, stages in PIPELINE_PRESETS.items()
    )

    recent = sorted(glob.glob(os.path.join(_queue_log_dir(root), "queue-*.log")),
                    key=os.path.getmtime, reverse=True)[:5]
    recent_html = ""
    if recent:
        items = "".join(
            f'<li><a href="/queue_log?log={urllib.parse.quote(p)}">{h(os.path.basename(p))}</a></li>'
            for p in recent)
        recent_html = (f'<div class="panel"><h2>Recent queue runs</h2>'
                       f'<ul style="margin-left:20px">{items}</ul></div>')

    body = f"""
    {active}
    <div class="panel">
      <h2>Queue work across images</h2>
      <form method="post" action="/queue">
        <p class="count">Pick presets to run on each selected image. Images on a
          spinning pool are processed fastest <b>sequentially</b>; parallel only
          helps light, metadata stages.</p>
        <div style="margin:8px 0">{preset_boxes}</div>
        <div style="margin:10px 0;display:flex;gap:16px;align-items:center;flex-wrap:wrap">
          <label>Mode:
            <select name="jobs">
              <option value="1">Sequential (1 at a time)</option>
              <option value="2">Parallel — 2</option>
              <option value="3">Parallel — 3</option>
              <option value="4">Parallel — 4</option>
            </select>
          </label>
          <label><input type="checkbox" name="skip_done" value="1" checked> skip stages already ok</label>
          <label><input type="checkbox" name="keep_going" value="1" checked> keep going on failure</label>
        </div>
        <fieldset style="border:1px solid var(--accent);border-radius:4px;padding:8px">
          <legend style="padding:0 6px">Images ({len(dbs)})
            <a href="#" onclick="for(var e of document.querySelectorAll('[name=db]'))e.checked=true;return false">all</a> /
            <a href="#" onclick="for(var e of document.querySelectorAll('[name=db]'))e.checked=false;return false">none</a>
          </legend>
          {img_rows or '<p class="count">No images found.</p>'}
        </fieldset>
        <button type="submit" style="margin-top:12px" {"disabled" if qpid else ""}>
          {"Queue is already running" if qpid else "Start queue"}
        </button>
      </form>
    </div>
    {recent_html}
    """
    return page("Queue", body, nav_extra=" &rsaquo; queue")


# bulk_extractor prints one "Offset NNNmb (PP.PP%) Done in H:MM:SS at ..." line
# per second; on the --scope recovered finalization spin these flood the tail and
# bury the START/DONE markers. Collapse runs of them for display only — the full
# log on disk is never touched.
_QUEUE_NOISE_RE = re.compile(r"Offset\s+\d.*Done in", re.I)


def _collapse_queue_noise(text):
    """Collapse consecutive bulk_extractor progress lines so the queue markers
    stay readable. Keeps the latest line of each run for context."""
    out, run = [], []

    def flush():
        if not run:
            return
        if len(run) > 2:
            out.append(f"  … {len(run)} bulk_extractor progress lines "
                       f"collapsed (latest below) …")
            out.append(run[-1])
        else:
            out.extend(run)
        run.clear()

    for line in text.splitlines():
        if _QUEUE_NOISE_RE.search(line):
            run.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


# cache the last marker scan per log so an unchanged file isn't re-read on every
# poll (idle / finished logs being browsed); an actively growing file still
# rescans, which is cheap from the page cache for a single-user homelab tool.
_QPROG_LOCK = threading.Lock()
_QPROG_CACHE = {}  # path -> (sig, progress_dict)


def _scan_queue_progress(path):
    """Derive queue progress from START/DONE markers (lines starting with '['),
    skipping the per-second progress spam. Returns counts + current image."""
    total = started = done = 0
    current = first_ts = last_ts = None
    finished = stages = ""
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                if not line.startswith("["):          # skip progress spam fast
                    continue
                ts = line[1:line.find("]")] if "]" in line else ""
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
                if "queue:" in line and "image(s)" in line:
                    m = re.search(r"queue:\s*(\d+)\s*image", line)
                    if m:
                        total = int(m.group(1))
                    ms = re.search(r"stages=(\S+)", line)
                    if ms:
                        stages = ms.group(1)
                elif "] START " in line:
                    started += 1
                    current = line.split("] START ", 1)[1].strip()
                elif "] DONE " in line:
                    done += 1
                elif "queue finished:" in line:
                    finished = line.split("]", 1)[1].strip()
    except OSError:
        pass
    if finished or started <= done:               # nothing currently running
        current = None
    return {"total": total, "started": started, "done": done, "current": current,
            "finished": finished, "first_ts": first_ts, "last_ts": last_ts,
            "stages": stages}


def _queue_progress_cached(path):
    try:
        st = os.stat(path)
    except OSError:
        return _scan_queue_progress(path)
    sig = (st.st_size, st.st_mtime_ns)
    with _QPROG_LOCK:
        ent = _QPROG_CACHE.get(path)
        if ent and ent[0] == sig:
            return ent[1]
    prog = _scan_queue_progress(path)
    with _QPROG_LOCK:
        _QPROG_CACHE[path] = (sig, prog)
    return prog


def _queue_progress_html(prog, running):
    """Render the queue progress header (image counts + current image + bar)."""
    total, done, cur = prog["total"], prog["done"], prog["current"]
    pct = int(done * 100 / total) if total else 0
    if prog["finished"]:
        head = f'<span class="badge ok">finished</span> {h(prog["finished"])}'
    elif running:
        head = (f'<span class="badge running">running</span> '
                f'image {done + 1} of {total or "?"}')
    else:
        head = '<span class="badge ok">idle</span>'
    bar = (f'<div style="background:#222;border-radius:4px;height:10px;margin:8px 0;'
           f'overflow:hidden"><div style="width:{pct}%;height:100%;'
           f'background:#22aa44"></div></div>')
    cur_html = (f'<div class="count">current image: <b>{h(cur)}</b></div>'
                if cur else "")
    return (f'<div>{head} &nbsp; <b>{done} / {total or "?"}</b> images done '
            f'<span class="count">({pct}%)</span></div>{bar}{cur_html}')


def queue_log_payload(root, log_path, tail_kb=64):
    """Shared data for the queue-log HTML page and its raw=1 JSON poll endpoint.
    Returns None (no logs), {'error': ...}, or the full payload dict."""
    qdir = os.path.realpath(_queue_log_dir(root))
    if not log_path:
        recent = sorted(glob.glob(os.path.join(qdir, "queue-*.log")),
                        key=os.path.getmtime, reverse=True)
        if not recent:
            return None
        log_path = recent[0]
    real = os.path.realpath(log_path)
    if not (real.startswith(qdir + os.sep) and os.path.isfile(real)):
        return {"error": "log path not allowed"}
    size = os.path.getsize(real)
    with open(real, "rb") as fh:
        if size > tail_kb * 1024:
            fh.seek(-tail_kb * 1024, os.SEEK_END)
            fh.readline()
        tail = fh.read().decode("utf-8", errors="replace")
    return {"real": real, "size": size, "tail": _collapse_queue_noise(tail),
            "running": bool(queue_active()[0]),
            "progress": _queue_progress_cached(real)}


def page_queue_log(root, log_path):
    """Queue-log viewer: progress header + collapsed tail, with a JS poller that
    refreshes in place (preserves scroll, can be paused) instead of reloading."""
    payload = queue_log_payload(root, log_path)
    if payload is None:
        return page("Queue Log", '<div class="panel"><p>No queue logs yet. '
                    '<a href="/queue">Start one</a>.</p></div>',
                    nav_extra=" &rsaquo; queue log")
    if "error" in payload:
        return page("Queue Log", f'<div class="panel err">{h(payload["error"])}</div>',
                    nav_extra=" &rsaquo; queue log")
    real, size, running = payload["real"], payload["size"], payload["running"]
    prog_html = _queue_progress_html(payload["progress"], running)
    enc = urllib.parse.quote(real)
    state = ('<span class="badge running">running</span>' if running
             else '<span class="badge ok">idle</span>')
    poll_js = """<script>
(function(){
  var url=%s, paused=false, timer=null;
  var pre=document.getElementById('qlog'), prog=document.getElementById('qprog');
  var btn=document.getElementById('qpause');
  function atBottom(){return pre.scrollHeight-pre.scrollTop-pre.clientHeight<40;}
  async function tick(){
    if(paused) return;
    try{
      var r=await fetch(url,{cache:'no-store'}); var d=await r.json();
      var stick=atBottom();
      pre.textContent=d.tail; prog.innerHTML=d.progress_html;
      document.getElementById('qsize').textContent=d.size_h;
      if(stick) pre.scrollTop=pre.scrollHeight;
      if(!d.running){ if(timer) clearInterval(timer);
        document.getElementById('qstate').innerHTML='<span class="badge ok">idle</span>'; }
    }catch(e){}
  }
  btn.onclick=function(){paused=!paused; btn.textContent=paused?'\\u25b6 Resume':'\\u23f8 Pause';};
  pre.scrollTop=pre.scrollHeight;
  if(%s) timer=setInterval(tick,5000);
})();
</script>""" % (json.dumps("/queue_log?log=" + enc + "&raw=1"),
                "true" if running else "false")
    # <noscript> keeps a plain meta-refresh fallback when JS is unavailable.
    noscript = ('<noscript><meta http-equiv="refresh" content="6"></noscript>'
                if running else "")
    body = f"""<div class="panel"><div id="qprog">{prog_html}</div></div>
    <div class="panel">
      <p><span id="qstate">{state}</span> &nbsp; <code>{h(real)}</code> &nbsp;
         (<span id="qsize">{_human_size(size)}</span>) &nbsp;
         <button id="qpause" type="button">⏸ Pause</button> &nbsp;
         <a href="/queue">&larr; Queue</a></p>
      <pre id="qlog" class="mono" style="background:#0d1117;padding:10px;
            border-radius:4px;max-height:70vh;overflow:auto;font-size:12px">{h(payload["tail"])}</pre>
    </div>{poll_js}"""
    return page("Queue Log", body, nav_extra=" &rsaquo; queue log", head_extra=noscript)


def page_pipeline_log(db_path, log_path, tail_kb=64):
    enc = urllib.parse.quote(db_path)
    safe_log_path, err = _db_log_path(db_path, log_path)
    if err:
        return page("Pipeline Log",
                    f'<div class="panel"><p class="err">{h(err)}</p>'
                    f'<p><a href="/db?db={enc}">&larr; Back to DB</a></p></div>',
                    db_name=db_path, nav_extra=' &rsaquo; pipeline log')
    log_path = safe_log_path

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
        alive = log_path in out.stdout or os.path.realpath(log_path) in out.stdout
    except Exception:
        alive = False

    refresh = '<meta http-equiv="refresh" content="3">' if alive else ""
    badge = ('<span class="badge running">running</span>' if alive
             else '<span class="badge ok">finished</span>')
    summary = ""
    if "Summary:" in body_text:
        summary = '<p class="count">Final summary at the end of the log.</p>'

    body = f"""
    <div class="panel">
      <p>{badge} &nbsp; <code>{h(log_path)}</code> &nbsp;
         (<span class="count">{size:,} bytes</span>)
         &nbsp; <a href="/db?db={enc}">&larr; Back to DB</a></p>
      {summary}
      <pre class="mono" style="background:#0d1117;padding:10px;border-radius:4px;
                                max-height:70vh;overflow:auto;font-size:12px">{h(body_text)}</pre>
    </div>
    """
    return page("Pipeline Log", body, db_name=db_path,
                nav_extra=' &rsaquo; pipeline log', head_extra=refresh)


def _runs_table_html(db_path, runs):
    """Scan-runs table with colour-coded status badges and log links."""
    if not runs:
        return '<p class="count">No stages run yet.</p>'
    enc = urllib.parse.quote(db_path)
    buf = (f'<p class="count">{len(runs)} run(s)</p>'
           '<div style="overflow-x:auto"><table>'
           '<tr><th>Stage</th><th>Status</th><th>Started</th>'
           '<th>Ended</th><th>Log</th><th>Output</th></tr>')
    for r in runs:
        stage_name = h(r["stage"] or "")
        status_cell = badge(r["status"] or "")
        started = h((r["started_at"] or "")[:19])
        ended = h((r["ended_at"] or "—")[:19])
        log_cell = ""
        if r["log_path"]:
            lq = urllib.parse.quote(r["log_path"])
            log_cell = (f'<a href="/pipeline_log?db={enc}&log={lq}" '
                        f'title="{h(r["log_path"])}">&#128196;</a>')
        out_cell = ""
        if r["output_dir"]:
            out_cell = (f'<span title="{h(r["output_dir"])}" '
                        f'style="font-size:11px;color:var(--sub)">'
                        f'{h(Path(r["output_dir"]).name)}</span>')
        buf += (f'<tr><td>{stage_name}</td><td>{status_cell}</td>'
                f'<td style="white-space:nowrap">{started}</td>'
                f'<td style="white-space:nowrap">{ended}</td>'
                f'<td style="text-align:center">{log_cell}</td>'
                f'<td>{out_cell}</td></tr>')
    buf += '</table></div>'
    return buf


def page_db(db_path):
    if not os.path.isfile(db_path):
        return page("Error", '<p class="err">Database not found.</p>')

    name = Path(db_path).name

    # DB file metadata
    try:
        db_stat = os.stat(db_path)
        db_size_mb = db_stat.st_size / 1_048_576
        import time as _time
        db_age_s = _time.time() - db_stat.st_mtime
        if db_age_s < 60:
            db_age = f"{int(db_age_s)}s ago"
        elif db_age_s < 3600:
            db_age = f"{int(db_age_s/60)}m ago"
        else:
            db_age = f"{int(db_age_s/3600)}h {int((db_age_s%3600)/60)}m ago"
        db_meta = (f'<p class="count" style="margin-bottom:6px">'
                   f'SQLite {db_size_mb:.1f} MB &nbsp;·&nbsp; last modified {db_age}</p>')
    except Exception:
        db_meta = ""

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

    # scan_runs — custom colour-coded table
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        runs = conn.execute(
            "SELECT stage, status, started_at, ended_at, log_path, output_dir "
            "FROM scan_runs ORDER BY id"
        ).fetchall()
        conn.close()
        runs_html = _runs_table_html(db_path, runs)
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

    # Auto-refresh if a pipeline is active for this DB
    pid, _ = pipeline_active_for(db_path)
    head_extra = '<meta http-equiv="refresh" content="15">' if pid else ""

    body = f"""
    <div class="panel"><h2>Image Info</h2>{db_meta}{info_html}</div>
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
    return page(name, body, db_name=db_path, head_extra=head_extra)


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
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if method:
            rows = conn.execute(
                "SELECT method, mime_type, size_bytes, relative_path, full_path, "
                "sha256, created_at FROM recovered_artifacts WHERE method = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (method, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT method, mime_type, size_bytes, relative_path, full_path, "
                "sha256, created_at FROM recovered_artifacts "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
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
        params = []
        if scope:
            where_parts.append("source_scope = ?")
            params.append(scope)
        if feature:
            where_parts.append("feature_file = ?")
            params.append(feature)
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cols, rows = run_query(db_path,
            f"SELECT source_scope, feature_file, value, context, offset_ref "
            f"FROM bulk_extractor_hits {where} "
            f"ORDER BY source_scope, feature_file LIMIT ?",
            (*params, limit))
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
        params = []
        if tool:
            where_parts.append("source_tool = ?")
            params.append(tool)
        if category:
            where_parts.append("category = ?")
            params.append(category)
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cols, rows = run_query(db_path,
            f"SELECT source_tool, category, key, value, score, path, notes, created_at "
            f"FROM findings {where} "
            f"ORDER BY score DESC, source_tool, category LIMIT ?",
            (*params, limit))
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
            rows = conn.execute(base_sql + f" LIMIT {GALLERY_ALL_CAP}", params).fetchall()
        else:
            offset = pg * per_page
            rows = conn.execute(base_sql + f" LIMIT {per_page} OFFSET {offset}",
                                params).fetchall()
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
                 search="", sort="size", order="", method_filter=""):
    """Paginated image gallery for carved artifacts with sort, method filter, and description search."""
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
            f"{desc_select} "
            f"FROM recovered_artifacts ra "
            f"{join_sql}"
            f"WHERE {where_clause} "
            f"ORDER BY {order_sql}"
        )

        if all_images:
            rows = conn.execute(base_sql + f" LIMIT {GALLERY_ALL_CAP}", params).fetchall()
        else:
            offset = pg * per_page
            rows = conn.execute(base_sql + f" LIMIT {per_page} OFFSET {offset}",
                                params).fetchall()
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
        tip = f"{Path(fp).name} — {method} — {sz:,} B"
        desc_div = (f'<div class="desc">{h(desc[:200])}</div>' if desc else "")
        caption = " — ".join(filter(None, [method, f"{sz//1024:,} KB" if sz else ""]))
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

    body = f"""
    <div class="panel">
      {header}{tag_note}{filter_badge}
      <p style="margin-top:8px;display:flex;align-items:center;flex-wrap:wrap;gap:10px">
        {toggle}
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
        gzipped = False
        if "gzip" in self.headers.get("Accept-Encoding", "") and len(encoded) > 512:
            encoded = gzip.compress(encoded, 5)
            gzipped = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Vary", "Accept-Encoding")
            if gzipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            # client closed the connection mid-response (refresh / navigate away)
            self.close_connection = True

    def send_bytes_cached(self, data, mime, etag, max_age=86400, extra_headers=None):
        """Send an in-memory blob with ETag/Cache-Control, honouring If-None-Match (304)."""
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", f"private, max-age={max_age}")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", f"private, max-age={max_age}")
        if etag:
            self.send_header("ETag", etag)
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_file_cached(self, abs_path, mime=None, max_age=86400):
        """Stream a file from disk with ETag/Cache-Control, honouring If-None-Match (304)."""
        try:
            st = os.stat(abs_path)
        except OSError as e:
            self.send_html(page("Error", f'<p class="err">{h(str(e))}</p>'), 404)
            return
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", f"private, max-age={max_age}")
            self.end_headers()
            return
        if mime is None:
            mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(st.st_size))
        self.send_header("Cache-Control", f"private, max-age={max_age}")
        self.send_header("ETag", etag)
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(abs_path, "rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

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
                abs_path, err = _resolve_under_root(self.qsval("path"), self.root)
                if abs_path is None:
                    self.send_html(page("Error", f'<p class="err">{h(err)}</p>'), 403)
                else:
                    self.send_file_cached(abs_path)
                return
            elif p == "/thumb":
                abs_path, err = _resolve_under_root(self.qsval("path"), self.root)
                if abs_path is None:
                    self.send_html(page("Error", f'<p class="err">{h(err)}</p>'), 403)
                    return
                data, etag = make_thumbnail(abs_path)
                if data is None:
                    # Not a decodable image (or Pillow missing) — serve the original.
                    self.send_file_cached(abs_path)
                else:
                    self.send_bytes_cached(data, "image/jpeg", etag)
                return
            elif p == "/gallery":
                all_images = self.qsval("all") == "1"
                search = self.qsval("search")
                sort  = self.qsval("sort")
                order = self.qsval("order")
                mode  = self.qsval("mode") or "carved"
                try:
                    pg = max(0, int(self.qsval("page", "0") or "0"))
                except ValueError:
                    pg = 0
                if mode == "fs":
                    camera = self.qsval("camera")
                    year   = self.qsval("year")
                    try:
                        min_score = int(self.qsval("min_score") or "0")
                    except ValueError:
                        min_score = 0
                    self.send_html(page_gallery_fs(db, pg, all_images=all_images,
                                                   sort=sort, order=order,
                                                   camera=camera, min_score=min_score,
                                                   year=year))
                else:
                    self.send_html(page_gallery(db, self.root, pg,
                                                all_images=all_images, search=search,
                                                sort=sort, order=order,
                                                method_filter=self.qsval("method")))
            elif p == "/export_view":
                file_id = self.qsval("file_id")
                abs_path, mime_or_err = export_file_via_icat(db, file_id, self.root)
                if abs_path is None:
                    self.send_html(page("Error",
                        f'<p class="err">{h(mime_or_err)}</p>'), 500)
                elif self.qsval("thumb") == "1":
                    data, etag = make_thumbnail(abs_path)
                    if data is None:
                        self.send_file_cached(abs_path, mime_or_err)
                    else:
                        self.send_bytes_cached(data, "image/jpeg", etag)
                else:
                    self.send_file_cached(abs_path, mime_or_err)
                return
            elif p == "/pipeline_log":
                log_path = self.qsval("log")
                self.send_html(page_pipeline_log(db, log_path))
            elif p == "/queue":
                self.send_html(page_queue(self.root))
            elif p == "/queue_log":
                if self.qsval("raw") == "1":
                    payload = queue_log_payload(self.root, self.qsval("log"))
                    if not payload or "error" in payload:
                        body = b'{"error":"unavailable"}'
                    else:
                        body = json.dumps({
                            "tail": payload["tail"],
                            "running": payload["running"],
                            "size_h": _human_size(payload["size"]),
                            "progress_html": _queue_progress_html(
                                payload["progress"], payload["running"]),
                        }).encode("utf-8")
                    self.send_bytes_cached(body, "application/json", "", max_age=0)
                else:
                    self.send_html(page_queue_log(self.root, self.qsval("log")))
            else:
                self.send_html(page("404", "<p>Not found.</p>"), 404)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True  # client disconnected; nothing to send
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
            skip_done = pval("skip_done") == "1"
            try:
                log_path, _pid = spawn_pipeline(
                    db, presets, keep_going=keep_going, skip_done=skip_done
                )
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
        elif p == "/queue":
            sel_dbs = params.get("db", [])
            presets = params.get("preset", [])
            try:
                jobs = max(1, min(4, int(pval("jobs", "1") or "1")))
            except ValueError:
                jobs = 1
            if not sel_dbs or not presets:
                self.send_html(page("Queue",
                    '<p class="err">Select at least one image and one preset.</p>'
                    '<p><a href="/queue">&larr; back</a></p>'), 400)
                return
            if queue_active()[0]:
                self.send_response(302)
                self.send_header("Location", "/queue_log")
                self.end_headers()
                return
            try:
                log_path, _pid = spawn_queue(
                    self.root, sel_dbs, presets, jobs=jobs,
                    skip_done=(pval("skip_done") == "1"),
                    keep_going=(pval("keep_going") == "1"),
                )
            except Exception as e:
                self.send_html(page("Queue",
                    f'<p class="err">queue failed: {h(str(e))}</p>'
                    f'<p><a href="/queue">&larr; back</a></p>'), 500)
                return
            self.send_response(302)
            self.send_header("Location", f'/queue_log?log={urllib.parse.quote(log_path)}')
            self.end_headers()
        else:
            self.send_html(page("405", "<p>Method not allowed.</p>"), 405)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.environ.get("DB_ROOT", "/data/db"),
                    help="Directory tree to search for *.analysis.sqlite files (default: $DB_ROOT or /data/db)")
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
