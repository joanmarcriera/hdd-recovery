"""Queue-log parsing and display helpers for the review UI.

Extracted from bin/image-serve.py (#18). This module is intentionally limited
to pure log parsing/caching/render snippets; route/page code supplies queue
activity and log-directory policy separately.
"""
from __future__ import annotations

import os
import re
import threading


# bulk_extractor prints one "Offset NNNmb (PP.PP%) Done in H:MM:SS at ..." line
# per second; on the --scope recovered finalization spin these flood the tail and
# bury the START/DONE markers. Collapse runs of them for display only; the full
# log on disk is never touched.
QUEUE_NOISE_RE = re.compile(r"Offset\s+\d.*Done in", re.I)


def collapse_queue_noise(text):
    """Collapse consecutive bulk_extractor progress lines.

    Keeps the latest line of each run for context so START/DONE markers remain
    readable in the web tail view.
    """
    out, run = [], []

    def flush():
        if not run:
            return
        if len(run) > 2:
            out.append(f"  ... {len(run)} bulk_extractor progress lines "
                       f"collapsed (latest below) ...")
            out.append(run[-1])
        else:
            out.extend(run)
        run.clear()

    for line in text.splitlines():
        if QUEUE_NOISE_RE.search(line):
            run.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def scan_queue_progress(path):
    """Derive queue progress from START/DONE markers.

    Lines that do not start with '[' are skipped quickly, which avoids parsing
    bulk_extractor's per-second progress spam. Returns counts + current image.
    """
    total = started = done = 0
    current = first_ts = last_ts = None
    finished = stages = ""
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                if not line.startswith("["):
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
    if finished or started <= done:
        current = None
    return {"total": total, "started": started, "done": done, "current": current,
            "finished": finished, "first_ts": first_ts, "last_ts": last_ts,
            "stages": stages}


QPROG_LOCK = threading.Lock()
QPROG_CACHE = {}  # path -> (sig, progress_dict)


def queue_progress_cached(path):
    try:
        st = os.stat(path)
    except OSError:
        return scan_queue_progress(path)
    sig = (st.st_size, st.st_mtime_ns)
    with QPROG_LOCK:
        ent = QPROG_CACHE.get(path)
        if ent and ent[0] == sig:
            return ent[1]
    prog = scan_queue_progress(path)
    with QPROG_LOCK:
        QPROG_CACHE[path] = (sig, prog)
    return prog


def queue_outcome(prog, running):
    """Classify a queue run from its parsed progress + a live-process flag.

    Returns one of: 'finished' (saw the "queue finished" marker), 'running',
    'abnormal' (process gone, no finish marker, images left undone — i.e. a
    crash or kill), or 'idle'. Single source of truth for the queue badges and
    the home-page alert banner.
    """
    if prog.get("finished"):
        return "finished"
    if running:
        return "running"
    if prog.get("total") and prog.get("done", 0) < prog["total"]:
        return "abnormal"
    return "idle"


def queue_progress_html(prog, running, escape):
    """Render queue progress. escape is injected to avoid a UI-module cycle."""
    total, done, cur = prog["total"], prog["done"], prog["current"]
    pct = int(done * 100 / total) if total else 0
    outcome = queue_outcome(prog, running)
    if outcome == "finished":
        head = f'<span class="badge ok">finished</span> {escape(prog["finished"])}'
    elif outcome == "running":
        head = (f'<span class="badge running">running</span> '
                f'image {done + 1} of {total or "?"}')
    elif outcome == "abnormal":
        # No "queue finished" marker, process not alive, work left undone: the
        # queue exited abnormally (crash or kill). Surface it loudly instead of
        # a green "idle" so a dead multi-image run isn't mistaken for complete.
        head = (f'<span class="badge failed">ended abnormally</span> '
                f'stopped after {done} of {total} image(s) without finishing — '
                f're-launch the queue to resume the rest')
    else:
        head = '<span class="badge ok">idle</span>'
    bar = (f'<div style="background:#222;border-radius:4px;height:10px;margin:8px 0;'
           f'overflow:hidden"><div style="width:{pct}%;height:100%;'
           f'background:#22aa44"></div></div>')
    cur_html = (f'<div class="count">current image: <b>{escape(cur)}</b></div>'
                if cur else "")
    return (f'<div>{head} &nbsp; <b>{done} / {total or "?"}</b> images done '
            f'<span class="count">({pct}%)</span></div>{bar}{cur_html}')
