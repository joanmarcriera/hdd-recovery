"""Pipeline and queue process helpers for the review UI.

Extracted from bin/image-serve.py (#18). This module owns detached pipeline
launch/cancel logic, durable supervised-run lookups, queue command construction,
queue process detection, and the pipeline-log page.
"""
from __future__ import annotations

import glob
import importlib.util
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from lib.serve_db import db_export_root, find_databases
from lib.serve_ui import h, page
from lib.supervised import (
    active_supervised_runs,
    cancel_supervised_run,
    create_supervised_run,
    encode_env,
    finish_supervised_run,
    request_supervised_stop,
    set_supervised_process,
)

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"

_PIPELINE_PATH = BIN_DIR / "image-pipeline.py"
try:
    _spec = importlib.util.spec_from_file_location("image_pipeline", _PIPELINE_PATH)
    _pipeline_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_pipeline_mod)
    PIPELINE_PRESETS: dict[str, list[str]] = dict(_pipeline_mod.PRESETS)
except Exception:
    PIPELINE_PRESETS = {}


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
    """Return (pid, log_path) of a running image-pipeline.py for this DB."""
    try:
        for run in active_supervised_runs(db_path, run_kind="pipeline"):
            if run.pid:
                return run.pid, run.log_path
    except Exception:
        pass

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

    active = ""
    if pid:
        log_q = urllib.parse.quote(log_path) if log_path else ""
        log_link = (f' &nbsp;<a href="/pipeline_log?db={enc}&log={log_q}">view log</a>'
                    if log_path else "")
        stop_requested = pipeline_stop_requested(db_path)
        stop_banner = (
            '<p class="count">Stop-after-current-stage is already requested.</p>'
            if stop_requested else ""
        )
        stop_disabled = "disabled" if stop_requested else ""
        active = (f'<p><span class="badge running">running</span> '
                  f'pid {pid}{log_link}</p>'
                  f'{stop_banner}'
                  f'<form method="post" action="/stop_pipeline_after_stage" '
                  f'style="margin:6px 0 4px">'
                  f'<input type="hidden" name="db" value="{h(db_path)}">'
                  f'<button type="submit" {stop_disabled}>'
                  f'Stop after current stage</button>'
                  f'</form>'
                  f'<form method="post" action="/cancel_pipeline" '
                  f'style="margin:0 0 10px">'
                  f'<input type="hidden" name="db" value="{h(db_path)}">'
                  f'<button type="submit">Cancel pipeline now</button>'
                  f'</form>')

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

    seen = set()
    stages = []
    for p in presets:
        for s in PIPELINE_PRESETS[p]:
            if s not in seen:
                seen.add(s)
                stages.append(s)

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

    with open(log_path, "a") as fh:
        fh.write(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] "
                 f"spawned: {shlex.join(cmd)}\n")

    run_id = create_supervised_run(db_path, "pipeline", shlex.join(cmd), log_path)
    env = os.environ.copy()
    env["SUPERVISED_RUNS"] = encode_env([(db_path, run_id)])
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, env=env
        )
    except Exception as exc:
        finish_supervised_run(db_path, run_id, "failed", notes=str(exc))
        raise
    set_supervised_process(db_path, run_id, proc.pid)
    return log_path, proc.pid


def cancel_active_pipeline(db_path):
    count = 0
    for run in active_supervised_runs(db_path, run_kind="pipeline", reconcile=False):
        if cancel_supervised_run(db_path, run.run_id):
            count += 1
    return count


_QUEUE_PATH = BIN_DIR / "image-queue.py"


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
    """Build the image-queue.py command line."""
    cmd = [sys.executable, str(_QUEUE_PATH), "--jobs", str(jobs),
           "--stages", ",".join(stages)]
    if skip_done:
        cmd.append("--skip-done")
    if keep_going:
        cmd.append("--keep-going")
    cmd += list(dbs)
    return cmd


def queue_active(root=None):
    """Return (pid, argv) of a running image-queue.py, or (None, None)."""
    if root is not None:
        try:
            for db_path in find_databases(root):
                for run in active_supervised_runs(db_path, run_kind="queue"):
                    if run.pid:
                        return run.pid, run.command_line
        except Exception:
            pass

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
    supervised = []
    command_line = shlex.join(cmd)
    for db in dbs:
        run_id = create_supervised_run(
            db,
            "queue",
            command_line,
            log_path,
            notes=f"presets={','.join(presets)} jobs={jobs}",
        )
        supervised.append((db, run_id))
    env = os.environ.copy()
    env["SUPERVISED_RUNS"] = encode_env(supervised)
    try:
        proc = subprocess.Popen(cmd, stdout=logfh, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env)
    except Exception as exc:
        for db, run_id in supervised:
            finish_supervised_run(db, run_id, "failed", notes=str(exc))
        raise
    for db, run_id in supervised:
        set_supervised_process(db, run_id, proc.pid)
    return log_path, proc.pid


def cancel_active_queue(root):
    seen = set()
    count = 0
    for db_path in find_databases(root):
        for run in active_supervised_runs(db_path, run_kind="queue", reconcile=False):
            key = (run.db_path, run.run_id)
            if key in seen:
                continue
            seen.add(key)
            if cancel_supervised_run(run.db_path, run.run_id):
                count += 1
    return count


def request_stop_active_queue(root):
    seen = set()
    count = 0
    for db_path in find_databases(root):
        for run in active_supervised_runs(db_path, run_kind="queue", reconcile=False):
            key = (run.db_path, run.run_id)
            if key in seen:
                continue
            seen.add(key)
            if request_supervised_stop(run.db_path, run.run_id):
                count += 1
    return count


def queue_stop_requested(root):
    for db_path in find_databases(root):
        for run in active_supervised_runs(db_path, run_kind="queue", reconcile=False):
            if run.cancel_requested:
                return True
    return False


def request_stop_active_pipeline(db_path):
    count = 0
    for run in active_supervised_runs(db_path, run_kind="pipeline", reconcile=False):
        if request_supervised_stop(db_path, run.run_id):
            count += 1
    return count


def pipeline_stop_requested(db_path):
    for run in active_supervised_runs(db_path, run_kind="pipeline", reconcile=False):
        if run.cancel_requested:
            return True
    return False


def page_pipeline_log(db_path, log_path, tail_kb=64):
    enc = urllib.parse.quote(db_path)
    safe_log_path, err = _db_log_path(db_path, log_path)
    if err:
        return page("Pipeline Log",
                    f'<div class="panel"><p class="err">{h(err)}</p>'
                    f'<p><a href="/db?db={enc}">&larr; Back to DB</a></p></div>',
                    db_name=db_path, nav_extra=' &rsaquo; pipeline log')
    log_path = safe_log_path

    size = os.path.getsize(log_path)
    with open(log_path, "rb") as fh:
        if size > tail_kb * 1024:
            fh.seek(-tail_kb * 1024, os.SEEK_END)
            fh.readline()
        body_text = fh.read().decode("utf-8", errors="replace")

    alive = False
    try:
        out = subprocess.run(["pgrep", "-af", "image-pipeline.py"],
                             capture_output=True, text=True, timeout=5)
        alive = log_path in out.stdout or os.path.realpath(log_path) in out.stdout
    except Exception:
        alive = False

    refresh = '<meta http-equiv="refresh" content="3">' if alive else ""
    status_badge = ('<span class="badge running">running</span>' if alive
                    else '<span class="badge ok">finished</span>')
    summary = ""
    if "Summary:" in body_text:
        summary = '<p class="count">Final summary at the end of the log.</p>'

    body = f"""
    <div class="panel">
      <p>{status_badge} &nbsp; <code>{h(log_path)}</code> &nbsp;
         (<span class="count">{size:,} bytes</span>)
         &nbsp; <a href="/db?db={enc}">&larr; Back to DB</a></p>
      {summary}
      <pre class="mono" style="background:#0d1117;padding:10px;border-radius:4px;
                                max-height:70vh;overflow:auto;font-size:12px">{h(body_text)}</pre>
    </div>
    """
    return page("Pipeline Log", body, db_name=db_path,
                nav_extra=' &rsaquo; pipeline log', head_extra=refresh)
