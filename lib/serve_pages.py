"""Page renderers for the hdd-recovery review UI.

Extracted from bin/image-serve.py (#18). This module renders HTML pages and
small page payloads from read-only SQLite queries and already-created recovery
outputs; request dispatch remains separate.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path

from lib.serve_db import db_export_root, find_databases, query_scalar, run_query
from lib.serve_images import discover_images, image_roots
from lib.storage_guard import (
    check_environment, dangerous_roots, overlay_allowed,
)
from lib.serve_mapfile import MAP_STATUS as _MAP_STATUS, map_svg as _map_svg, parse_mapfile
from lib.serve_pipeline import (
    PIPELINE_PRESETS,
    _pipeline_pgrep_lines,
    _queue_log_dir,
    panel_pipeline,
    pipeline_active_for,
    queue_active,
    queue_stop_requested,
)
from lib.serve_queue_log import (
    collapse_queue_noise as _collapse_queue_noise,
    queue_outcome as _queue_outcome,
    queue_progress_cached as _queue_progress_cached,
    queue_progress_html as _queue_progress_html_impl,
)
from lib.serve_ui import badge, h, human_size as _human_size, mem_panel, page, table_html

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"


def storage_warning_banner():
    """Red banner when any data root resolves to the container overlay layer.

    This is the exact misconfiguration that silently filled the FastPool SSD and
    discarded exports on recreate. Returns '' when storage is healthy.
    """
    try:
        danger = dangerous_roots(check_environment())
    except Exception:
        return ""
    if not danger:
        return ""
    rows = "".join(
        f'<li><code>{h(r.name)}</code> &rarr; <code>{h(r.path)}</code></li>'
        for r in danger)
    overridden = (' <b>(enforcement overridden by HDD_ALLOW_OVERLAY=1 — writes '
                  'are still going to the overlay)</b>') if overlay_allowed() else ""
    return (
        '<div class="panel" style="border-left:5px solid #cc2222;background:#2a1414">'
        '<h2 style="color:#ff6b6b;margin-top:0">&#9888; Storage misconfigured — '
        'data is going to the container overlay</h2>'
        f'<p>These roots resolve to the writable overlay layer{overridden}. On '
        'TrueNAS this fills the FastPool SSD and is <b>destroyed on the next '
        'app edit/update</b>:</p>'
        f'<ul style="margin:8px 0 8px 20px">{rows}</ul>'
        '<p class="count">Fix: bind-mount each root to a dataset, or set its env '
        'var under an already-mounted path (see <code>docker/docker-compose.yml</code>). '
        'Run <code>bin/storage-check.sh</code> in the terminal for details.</p>'
        '</div>'
    )


# ── pages ─────────────────────────────────────────────────────────────────────


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


def queue_alert_banner(root):
    """Red home-page banner when the most recent queue ended abnormally — process
    gone, no "queue finished" marker, images left undone (a crash or kill). The
    home page otherwise shows nothing for a dead queue, so a multi-day run that
    died mid-way reads as 'done' (the 2026-06-23 incident). Best-effort."""
    try:
        logs = sorted(glob.glob(os.path.join(_queue_log_dir(root), "queue-*.log")),
                      key=os.path.getmtime, reverse=True)
        if not logs:
            return ""
        latest = logs[0]
        prog = _queue_progress_cached(latest)
        if _queue_outcome(prog, bool(queue_active(root)[0])) != "abnormal":
            return ""
        enc = urllib.parse.quote(latest)
        return (
            '<div class="panel" style="border-left:4px solid #cc2222">'
            '<span class="badge failed">queue ended abnormally</span> &nbsp; '
            f'the last queue stopped after {prog["done"]} of {prog["total"]} '
            'image(s) without finishing (crash or kill). '
            f'<a href="/queue_log?log={enc}">view log</a> &nbsp;&middot;&nbsp; '
            '<a href="/queue">re-launch the queue</a></div>')
    except Exception:
        return ""


def page_home(root):
    cached = _HOME_CACHE.get(root)
    if cached and (time.monotonic() - cached[0]) < _HOME_CACHE_TTL:
        return cached[1]

    storage_banner = storage_warning_banner()
    dbs = find_databases(root)
    if not dbs:
        body = f"""{storage_banner}<div class="panel">
          <p>No *.analysis.sqlite files found under <code>{h(root)}</code>.</p>
          <p style="margin-top:10px">
            <a href="/images/new" style="background:#0f3460;padding:4px 12px;border-radius:3px;color:#7eb8f7"
               title="Find finished .img files, initialise missing DBs, and optionally start the fast scan">
               Scan for new images</a>
            &nbsp; <a href="/help">Add image / Help</a>
          </p>
        </div>"""
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

    queue_alert = queue_alert_banner(root)

    body = f"""{storage_banner}{mem_panel()}{queue_alert}{pipeline_banner}<div class="panel">
      <p class="count" style="display:flex;justify-content:space-between;align-items:center">
        <span>{len(dbs)} image(s) found under <code>{h(root)}</code></span>
        <span>
          <a href="/images/new" style="background:#0f3460;padding:4px 12px;border-radius:3px;color:#7eb8f7;margin-right:6px"
             title="Find finished .img files, initialise missing DBs, and optionally start the fast scan">Scan for new images</a>
          <a href="/help" style="background:#0f3460;padding:4px 12px;border-radius:3px;color:#7eb8f7;margin-right:6px"
             title="How to acquire a new disk image (ddrescue, failing disks, USB) and register it here">&#43; Add image / Help</a>
          <a href="/queue" style="background:#0f3460;padding:4px 12px;border-radius:3px;color:#7eb8f7"
             title="Queue fast/carve/etc. across multiple images">&#9654; Queue work</a>
        </span>
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


def page_new_images(root, notice="", error=""):
    """GET /images/new — discover finished .img files and register missing DBs."""
    candidates = discover_images(root)
    roots = image_roots(root)
    roots_html = "".join(f"<li><code>{h(r)}</code></li>" for r in roots)
    if not roots_html:
        roots_html = "<li>No readable image roots found.</li>"

    notice_html = (f'<div class="panel" style="border-left:4px solid #1e4620">'
                   f'{h(notice)}</div>') if notice else ""
    error_html = f'<div class="panel err">{h(error)}</div>' if error else ""

    rows = ""
    for c in candidates:
        status = ('<span class="badge ok">registered</span>' if c.registered
                  else '<span class="badge pending">new</span>')
        checked = " checked" if not c.registered else ""
        map_cell = (f'<code>{h(c.map_path)}</code>' if c.map_path
                    else '<span class="count">not found</span>')
        db_note = "registered" if c.registered else "will create"
        rows += f"""<tr>
          <td style="text-align:center">
            <input type="checkbox" name="image" value="{h(c.image_path)}"{checked}>
          </td>
          <td>{status}</td>
          <td><code>{h(c.image_path)}</code></td>
          <td style="white-space:nowrap">{h(_human_size(c.size_bytes))}</td>
          <td>{map_cell}</td>
          <td><span class="count">{h(db_note)}</span><br><code>{h(c.db_path)}</code></td>
        </tr>"""

    if rows:
        table = f"""
        <form method="post" action="/images/new" style="display:block">
          <div style="overflow-x:auto"><table>
            <tr><th>Use</th><th>Status</th><th>Image</th><th>Size</th><th>Map</th><th>Database</th></tr>
            {rows}
          </table></div>
          <p class="count" style="margin-top:8px">
            Missing DBs are checked by default. Existing DBs can also be selected
            if you want to queue the fast scan with skip-done enabled.
          </p>
          <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
            <button type="submit" name="action" value="init">Initialize selected</button>
            <button type="submit" name="action" value="init_fast">Initialize + start fast scan</button>
          </div>
        </form>
        """
    else:
        table = '<p class="count">No <code>*.img</code> files were found in the image roots above.</p>'

    body = f"""
    {notice_html}{error_html}
    <div class="panel">
      <h2>Scan for New Images</h2>
      <p class="count">
        This scans finished raw images only. It never mounts or writes to source
        disks. The fast scan is queued sequentially and runs
        <code>structure-scan</code>, <code>index-tsk</code>,
        <code>detect-wallets</code>, and <code>detect-pictures</code>.
      </p>
      <p class="count" style="margin-top:8px">
        Image hashing is not run from this page so the HTTP request does not
        block on a full-disk read; run <code>image-analysis-init.sh --hash</code>
        later if you need a SHA256 recorded.
      </p>
    </div>
    <div class="panel">
      <h2>Image roots</h2>
      <ul style="margin-left:20px">{roots_html}</ul>
    </div>
    <div class="panel">
      <h2>Discovered images ({len(candidates)})</h2>
      {table}
    </div>
    """
    return page("Scan for New Images", body, nav_extra=" &rsaquo; scan images")


def page_queue(root):
    """GET /queue — pick images + presets and launch a multi-image batch."""
    if not PIPELINE_PRESETS:
        return page("Queue", '<div class="panel err">image-pipeline.py failed to load.</div>')
    dbs = find_databases(root)
    qpid, _ = queue_active(root)

    active = ""
    if qpid:
        stop_requested = queue_stop_requested(root)
        stop_banner = (
            '<p class="count">Stop-after-current-stage is already requested.</p>'
            if stop_requested else ""
        )
        stop_disabled = "disabled" if stop_requested else ""
        active = (f'<div class="panel" style="border-left:4px solid #22aa44">'
                  f'<span class="badge running">running</span> a queue is active '
                  f'(pid {qpid}). <a href="/queue_log">view queue log</a>'
                  f'{stop_banner}'
                  f'<form method="post" action="/stop_queue_after_stage" '
                  f'style="margin-top:8px"><button type="submit" {stop_disabled}>'
                  f'Stop after current stage</button></form>'
                  f'<form method="post" action="/cancel_queue" '
                  f'style="margin-top:8px"><button type="submit">'
                  f'Cancel queue now</button></form></div>')

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


def _queue_progress_html(prog, running):
    """Compatibility wrapper around the extracted queue-log renderer."""
    return _queue_progress_html_impl(prog, running, h)


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
            "running": bool(queue_active(root)[0]),
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
    {panel_reset(db_path)}
    """
    return page(name, body, db_name=db_path, head_extra=head_extra)


def panel_reset(db_path):
    """Render the per-image 'Danger zone — reset' panel for the DB page."""
    enc = h(db_path)
    img_name = (query_scalar(db_path, "SELECT image_name FROM image_info WHERE id=1", "")
                or Path(db_path).name)
    export_root = db_export_root(db_path)
    return f"""
    <div class="panel" style="border-left:5px solid #cc2222;margin-top:18px">
      <h2 style="color:#ff6b6b">Danger zone &mdash; reset image</h2>
      <p class="count">Permanently deletes this image's analysis database and its
        export tree (carving / PhotoRec / recovered output). The raw
        <code>.img</code> and the ddrescue map are <b>kept</b>. Use this to
        re-analyse from scratch.</p>
      <p class="count">Deletes <code>{h(Path(db_path).name)}</code> and
        <code>{h(export_root or '(export tree)')}</code></p>
      <form method="post" action="/reset_image"
            onsubmit="return confirm('Delete the DB and all exports for this image? This cannot be undone.')">
        <input type="hidden" name="db" value="{enc}">
        <label>Type the image name <b>{h(img_name)}</b> to confirm:
          <input type="text" name="confirm" autocomplete="off"
                 style="margin:0 8px;padding:3px 6px"></label>
        <button type="submit" style="background:#cc2222;color:#fff;border:none;
                padding:5px 12px;border-radius:3px;cursor:pointer">Reset image</button>
      </form>
    </div>"""


# One row per candidate FILE, merging the separate rows that detect-wallets,
# yara-scan, and pdf-extract each create for the same file. Non-destructive: a
# pure SELECT/GROUP BY that keeps every underlying row (and its provenance)
# intact — evidence is never deleted (#8). NULL-file_id rows (no indexed file)
# stay individual via the -wc.id grouping key.
_WALLET_DEDUP_SQL = """
    SELECT MAX(wc.score)                      AS score,
           COUNT(*)                           AS hits,
           COUNT(DISTINCT wc.source_stage)    AS methods,
           GROUP_CONCAT(DISTINCT wc.source_stage) AS method_list,
           GROUP_CONCAT(DISTINCT wc.reason)   AS reasons,
           f.name                             AS name,
           COALESCE(f.path, '(no indexed file)') AS path,
           f.size_bytes                       AS size_bytes,
           MIN(wc.created_at)                 AS first_seen
    FROM   wallet_candidates wc
    LEFT JOIN files f ON f.id = wc.file_id
    GROUP  BY COALESCE(wc.file_id, -wc.id)
    ORDER  BY score DESC, path
    LIMIT  ?"""


def wallet_dedup_counts(db_path):
    """Return (raw_rows, distinct_files): the raw wallet_candidates count and
    the deduplicated count after merging by file."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        raw = conn.execute("SELECT COUNT(*) FROM wallet_candidates").fetchone()[0]
        deduped = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM wallet_candidates "
            "GROUP BY COALESCE(file_id, -id))").fetchone()[0]
    finally:
        conn.close()
    return raw, deduped


def page_wallets(db_path, limit=200):
    try:
        raw, deduped = wallet_dedup_counts(db_path)
        cols, rows = run_query(db_path, _WALLET_DEDUP_SQL, (limit,))
        merged = raw - deduped
        summary = (f'<p class="count">{deduped} candidate file(s)'
                   + (f' &mdash; deduplicated from {raw} raw hits across all '
                      f'discovery methods ({merged} duplicate row(s) merged)'
                      if merged > 0 else f' ({raw} raw hit(s), no duplicates)')
                   + '. Every source method is preserved in the Methods column; '
                   'no evidence is deleted.</p>')
        body = f'<div class="panel">{summary}{table_html(cols, rows)}</div>'
    except Exception as e:
        body = f'<div class="panel err">{h(str(e))}</div>'
    return page("Wallet Candidates", body, db_name=db_path, nav_extra=' &rsaquo; wallets')


def page_pictures(db_path, limit=500):
    enc = urllib.parse.quote(db_path)
    # Section 1: filesystem-aware scored candidates (from image-detect-pictures.sh)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cand_rows = conn.execute("""
            SELECT f.id AS file_id, pc.score, pc.reason,
                   pc.camera_model, pc.taken_at, pc.width, pc.height,
                   f.name, f.path, f.size_bytes
            FROM   picture_candidates pc
            LEFT JOIN files f ON f.id = pc.file_id
            ORDER  BY pc.score DESC, f.path
            LIMIT  ?""", (limit,)).fetchall()
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
            "SELECT method, mime_type, size_bytes, relative_path, full_path, "
            "dedup_cluster_id, is_cluster_primary FROM recovered_artifacts "
            "WHERE mime_type LIKE 'image/%' ORDER BY method, mime_type, size_bytes DESC LIMIT ?",
            (limit,)
        ).fetchall()
        # Cluster sizes so the table can show how many near-duplicates each
        # cluster holds (populated by the dedup-photos stage; #15).
        sizes = dict(conn.execute(
            "SELECT dedup_cluster_id, COUNT(*) FROM recovered_artifacts "
            "WHERE dedup_cluster_id IS NOT NULL GROUP BY dedup_cluster_id").fetchall())
        conn.close()

        tbl = ('<p class="count">' + str(len(img_rows)) + ' row(s)</p>'
               '<div style="overflow-x:auto"><table>'
               '<tr><th>Method</th><th>MIME</th><th>Size (B)</th><th>Cluster</th>'
               '<th>Path</th><th>View</th></tr>')
        for r in img_rows:
            fp = r["full_path"] or ""
            fenc = urllib.parse.quote(fp)
            view = (f'<a href="/file?path={fenc}" target="_blank" '
                    f'title="Open image in new tab">&#128247;</a>' if fp else "")
            cid = r["dedup_cluster_id"]
            if cid is None:
                cluster = '<span style="color:#888">—</span>'
            else:
                star = ' ★' if r["is_cluster_primary"] else ''
                n = sizes.get(cid, 1)
                cluster = (f'#{h(cid)}{star} '
                           f'<span style="color:#888">({n})</span>')
            tbl += (f'<tr><td>{h(r["method"])}</td><td>{h(r["mime_type"])}</td>'
                    f'<td>{(r["size_bytes"] or 0):,}</td>'
                    f'<td style="white-space:nowrap">{cluster}</td>'
                    f'<td style="word-break:break-all">{h(r["relative_path"])}</td>'
                    f'<td style="text-align:center">{view}</td></tr>')
        tbl += ('</table></div>'
                '<p class="count" style="color:#888">★ = cluster primary '
                '(best representative); (n) = images in that near-duplicate '
                'cluster. Run the dedup-photos stage to populate clusters.</p>')
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
            sql = """SELECT f.name, f.path, f.size_bytes, f.mtime,
                             f.allocated, f.deleted, fs.fs_type
                      FROM files f
                      LEFT JOIN filesystems fs ON fs.partition_id = f.partition_id
                      WHERE f.path LIKE ? OR f.name LIKE ?
                      ORDER BY f.size_bytes DESC LIMIT ?"""
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (pattern, pattern, limit)).fetchmany(limit)
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
    script = BIN_DIR / "image-timeline.sh"
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

# ── ddrescue map visualization ───────────────────────────────────────────────

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


_HELP_HEAD = """<style>
.help h2{border-bottom:1px solid var(--accent);padding-bottom:4px;margin-top:22px}
.help pre{background:#0d1117;border:1px solid var(--accent);border-radius:6px;
  padding:10px 12px;overflow:auto;font-size:12px;line-height:1.5;white-space:pre;margin:8px 0}
.help code{background:#0d1117;border:1px solid #2a2a4a;border-radius:3px;padding:1px 5px}
.help li{margin:5px 0 5px 22px}
.help .warn{border-left:4px solid var(--hi);padding:8px 12px;background:#2a1622;
  border-radius:0 4px 4px 0;margin:10px 0}
.help .ok-note{border-left:4px solid #1e4620;padding:8px 12px;background:#10210f;
  border-radius:0 4px 4px 0;margin:10px 0}
.help table td{vertical-align:top}
</style>"""


def page_help(root=""):
    """Operator guide: how to acquire a new disk image and register it here."""
    body = """
<div class="help">

<div class="panel">
  <h2 style="margin-top:0;border:0">Two machines, one destination disk</h2>
  <p>Imaging and analysis run in <b>different places</b>:</p>
  <ul>
    <li><b>optiplex990 (imaging host)</b> &mdash; has the recovery tools
        (<code>ddrescue</code>, <code>hddsuperclone</code>, <code>safecopy</code>&hellip;)
        and the source disks physically attached. <b>You run all the commands on
        this page here.</b></li>
    <li><b>This container (analysis / web UI)</b> &mdash; never touches a source
        disk. It only reads finished <code>.img</code> files and their analysis
        databases under <code>__ROOT__</code>.</li>
  </ul>
  <p>The bridge is the <b>16&nbsp;TB destination</b>
     (<code>/mnt/recovery16tb/recovery/</code>): imaging writes the
     <code>.img</code> + <code>.map</code> there, and this UI reads them back. An
     image only appears on the home page once its
     <code>&lt;image&gt;.analysis.sqlite</code> exists beside it (see step 4).</p>
</div>

<div class="warn">
  <b>Safety (non-negotiable).</b>
  <ul style="margin-top:4px">
    <li>Never write to a source disk; never mount a source <b>read-write</b>.</li>
    <li>Identify the disk by <b>model + serial + by-path</b> before touching it &mdash;
        not by <code>/dev/sdX</code> alone (it changes between boots).</li>
    <li>Do not start a SMART self-test while imaging.</li>
    <li>Write images only to the 16&nbsp;TB destination, never back onto a source.</li>
  </ul>
</div>

<div class="panel">
  <h2 style="margin-top:0;border:0">On optiplex990 (this imaging host)</h2>
  <ul>
    <li>The repo lives at <code>~/hdd-recovery</code>, not <code>/root/hdd-recovery</code>.
        Export <code>HDD_RECOVERY_ROOT=$HOME/hdd-recovery</code> before running any
        <code>bin/</code> script, or they fail sourcing <code>lib/common.sh</code>.</li>
    <li>Images land on the <b>DATA1</b> disk (<code>/dev/sdc1</code>, ext4) mounted at
        <code>/data</code>: <code>/data/images</code> (img), <code>/data/logs</code>
        (mapfiles), <code>/data/db</code> (analysis DBs), <code>/data/exports</code>.
        If it is not mounted: <code>sudo mount /dev/sdc1 /data</code>.</li>
    <li><b>Never touch <code>sdb</code> or <code>sdd</code></b> &mdash; 14.6&nbsp;TB XFS
        <code>mfschunks*</code> MooseFS cluster disks. Only the USB/SATA <i>source</i>
        disks you attach are imaging targets.</li>
    <li><code>hddsuperclone</code> and <code>safecopy</code> are not installed here;
        <code>sudo apt install</code> them only if a drive is badly failing.</li>
  </ul>
</div>

<h2>1. Identify the source disk</h2>
<pre>lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MODEL,SERIAL,TRAN,ROTA,STATE
ls -l /dev/disk/by-path           # stable path that survives reboots
sudo smartctl -a /dev/sdX         # health (read-only)  -- do NOT run a self-test
sudo hdparm -I /dev/sdX           # model/serial/sector size</pre>
<p>If the stick/disk auto-mounted, unmount it first &mdash; do not remount writable:</p>
<pre>sudo umount /dev/sdX1   # repeat per partition; leave the source untouched after</pre>
<p class="count"><b>Reality checks.</b> USB flash sticks usually do <b>not</b> expose
   SMART through the bridge (<code>smartctl</code> reports &ldquo;Unknown USB
   bridge&rdquo;) &mdash; that is normal, skip the health check for them. And if
   you plugged in N drives but <code>lsblk</code> shows fewer, the missing one did
   not enumerate: reseat it, try another port/cable, then check
   <code>dmesg | tail</code> for whether the kernel saw it at all.</p>

<h2>2. Failing disk &mdash; ddrescue, graduated intensity</h2>
<p>This is the important one. ddrescue copies in <b>passes that get more
   aggressive</b>: grab everything that reads easily and cheaply first (so a dying
   disk spends its remaining life on data, not on retrying bad sectors), then
   close in on the damaged zones. The <b>mapfile</b> records progress, so every
   pass resumes where the last stopped &mdash; never restart from zero.</p>

<h3>Option A &mdash; using the repo job-config workflow (recommended)</h3>
<pre>cd ~/hdd-recovery                                   # the repo on optiplex990
cp bin/ddrescue-job-template.conf jobs/mydisk.conf  # then fill SOURCE_DEV,
                                                    # SOURCE_MODEL, SERIAL, BASENAME

bin/ddrescue-run.sh jobs/mydisk.conf first          # PREVIEW (prints the command)
bin/ddrescue-run.sh jobs/mydisk.conf first  --run   # execute first pass
bin/ddrescue-status.sh /mnt/recovery16tb/recovery/logs/&lt;basename&gt;.map

# only if unread areas remain, escalate one pass at a time:
bin/ddrescue-run.sh jobs/mydisk.conf retry   --run
bin/ddrescue-run.sh jobs/mydisk.conf reverse --run
bin/ddrescue-run.sh jobs/mydisk.conf retrim  --run</pre>

<h3>Option B &mdash; raw ddrescue (same phases, by hand)</h3>
<pre>SRC=/dev/sdX
IMG=/mnt/recovery16tb/recovery/images/mydisk.img
MAP=/mnt/recovery16tb/recovery/logs/mydisk.map

# 1) First pass: fast + gentle. -n skips the slow scrape phase, -p preallocates,
#    so only the easy, healthy blocks are read this time.
sudo ddrescue -n -p -v --log-events=mydisk.events.log --log-rates=mydisk.rates.log "$SRC" "$IMG" "$MAP"

# 2) See what is left (rescued / non-tried / bad).
sudo ddrescuelog -t "$MAP"

# 3) Retry pass: now attack the hard areas. -d direct I/O, -r3 three retries,
#    -O reopen the device on error, brief pause so the drive can recover.
sudo ddrescue -d -r3 -O --pause-on-error=1s -v "$SRC" "$IMG" "$MAP"

# 4) Reverse pass: approach bad zones from the other end (-R).
sudo ddrescue -d -r1 -O -R -v "$SRC" "$IMG" "$MAP"

# 5) Retrim: re-attempt previously trimmed bad areas (-M), last and most aggressive.
sudo ddrescue -d -r1 -O -M -v "$SRC" "$IMG" "$MAP"</pre>
<p class="count">Add <code>-b 4096</code> for 4K-native drives. Keep the same
   <code>$MAP</code> across every pass &mdash; that is what makes them cumulative.</p>

<h2>3. Tougher cases &mdash; other tools</h2>
<p>When ddrescue stalls, the disk keeps dropping off the bus, or you want a second
   opinion, switch tools but keep the same gentle-first strategy. All are on
   optiplex990.</p>
<table>
  <tr><th>Tool</th><th>When / how</th></tr>
  <tr><td><b>HDDSuperClone</b></td>
      <td>Very sick drives: talks over direct ATA/USB, handles drives that
          reset or vanish mid-read, and can resume into a ddrescue-compatible
          map. Reach for it when ddrescue can't keep the disk alive.
          <pre>sudo hddsuperclone   # GUI/curses; point it at /dev/sdX -&gt; the .img</pre></td></tr>
  <tr><td><b>safecopy</b></td>
      <td>Staged, increasingly aggressive low-level retries &mdash; good as an
          independent second pass over what ddrescue left behind.
          <pre>sudo safecopy --stage1 /dev/sdX mydisk.img   # fast, skip bad
sudo safecopy --stage2 /dev/sdX mydisk.img   # closer
sudo safecopy --stage3 /dev/sdX mydisk.img   # most aggressive</pre></td></tr>
  <tr><td><b>dd_rescue + dd_rhelp</b></td>
      <td>Older alternative; <code>dd_rhelp</code> wraps <code>dd_rescue</code> to
          copy good areas first, then close in on the bad ones.
          <pre>sudo dd_rhelp /dev/sdX mydisk.img mydisk.log</pre></td></tr>
  <tr><td><b>myrescue</b></td>
      <td>Same good-first / bad-later idea, resumable via its own block log.
          <pre>sudo myrescue -b 4096 /dev/sdX mydisk.img</pre></td></tr>
  <tr><td><b>ddrescuelog</b></td>
      <td>Inspect/convert the mapfile: <code>-t</code> totals,
          <code>-l</code> list block ranges by status, mark/clear regions.</td></tr>
</table>

<h2>4. USB stick / external drive</h2>
<pre># Identify it first -- match the size + model, don't grab the wrong /dev/sdX.
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINT

# Healthy stick -- a straight image is fine:
sudo ddrescue -f -n /dev/sdX /mnt/recovery16tb/recovery/images/usbkey.img \
  /mnt/recovery16tb/recovery/logs/usbkey.map

# Plain dd is OK for HEALTHY media only (no error tolerance, no resume):
sudo dd if=/dev/sdX of=/mnt/recovery16tb/recovery/images/usbkey.img \
  bs=4M conv=noerror,sync status=progress

# Flaky stick? Use the full graduated ddrescue passes from section 2 instead.</pre>

<h2>5. Make the image appear in this UI</h2>
<p>Once the <code>.img</code> and optional <code>.map</code> are visible to this UI,
   use <a href="/images/new">Scan for new images</a>. It creates any missing
   analysis DBs and can queue the fast metadata pass for you.</p>
<p>If you need to do it by hand, run the script directly (not <code>ls</code>):</p>
<pre>DB="$(bin/image-analysis-init.sh /mnt/recovery16tb/recovery/images/mydisk.img \
  --map /mnt/recovery16tb/recovery/logs/mydisk.map --print-db-path)"
bin/image-pipeline.py "$DB" --run --skip-done --preset fast</pre>
<div class="ok-note">
  Creating an <code>*.analysis.sqlite</code> catalog is what makes the image show
  up on the <a href="/">home page</a>. The scan page infers the right DB
  location for either the legacy beside-image layout or the split
  <code>/data/images</code> + <code>/data/db</code> Docker layout.
</div>

</div>
"""
    body = body.replace("__ROOT__", h(root or "(IMAGE_ROOT)"))
    return page("Add Images / Help", body, nav_extra=" &rsaquo; help", head_extra=_HELP_HEAD)
