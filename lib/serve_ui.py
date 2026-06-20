"""Generic HTML/UI helpers for the hdd-recovery review UI.

Extracted from bin/image-serve.py (#18). This module contains no route or
database policy; it only formats escaped HTML fragments and shared page chrome.
"""
from __future__ import annotations

import html
import os
import urllib.parse
from pathlib import Path


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

APP_VERSION = os.environ.get("APP_VERSION", "dev")


def h(s):
    return html.escape(str(s)) if s is not None else "<span style='color:#555'>NULL</span>"


def badge(status):
    cls = {"ok": "ok", "partial": "partial", "failed": "failed",
           "running": "running", "error": "error",
           # a run reconciled after a kill/crash/restart (see lib/runs.py)
           "interrupted": "partial", "timeout": "failed"}.get(
               str(status).lower(), "pending")
    return f'<span class="badge {cls}">{h(status)}</span>'


def page(title, body, db_name="", nav_extra="", head_extra=""):
    home = '<a href="/">&#8962; home</a>'
    db_link = (f' &rsaquo; <a href="/db?db={urllib.parse.quote(db_name)}">'
               f'{h(Path(db_name).name)}</a>' if db_name else "")
    nav = f'<nav>{home}{db_link}{nav_extra}</nav>'
    footer = (f'<footer style="margin-top:24px;color:var(--sub);font-size:11px">'
              f'hdd-forensics &middot; build {h(APP_VERSION)}</footer>')
    return f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>{h(title)} &mdash; hdd-recovery</title>
<style>{CSS}</style>{head_extra}</head><body>{nav}<h1>{h(title)}</h1>{body}
{footer}<script>{SORT_JS}</script></body></html>"""


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


def mem_status():
    """Read /proc/meminfo. Returns memory/swap values in MB, or None."""
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


def human_size(n):
    """Bytes to compact human string, e.g. 160041885696 -> '149 GB'."""
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
