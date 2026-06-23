#!/usr/bin/env python3
"""
Tag recovered photos using a local vision LLM (llava via Ollama).

Sends each qualifying image to the llava model and stores the description
in the findings table (source_tool='llava', category='photo-description').
The job is fully resumable — already-tagged images are skipped by default.

Usage:
  image-tag-photos.py <db> [options]

Scopes:
  --scope real   JPEG only, >= --min-size (default; ~real photos)
  --scope all    All image/* types, >= --min-size (includes web cache)

The findings table is created automatically if absent (schema is idempotent).
Progress is tracked in scan_runs.  Interrupt with Ctrl-C; re-run to resume.
"""

import argparse, base64, concurrent.futures, json, os, sqlite3, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from lib.timestamp import utc_now  # noqa: E402

STAGE = "llava-tag-photos"
DEFAULT_PROMPT = (
    "Describe this image concisely for a forensic investigator. "
    "Include: type of image (photo, screenshot, icon, diagram, document scan), "
    "main subjects (people, objects, places, activities), any visible text or dates, "
    "and estimated time period if apparent. "
    "One paragraph, factual, brief."
)
FINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS findings (
  id          INTEGER PRIMARY KEY,
  source_tool TEXT    NOT NULL,
  category    TEXT    NOT NULL,
  file_id     INTEGER,
  artifact_id INTEGER,
  path        TEXT,
  key         TEXT,
  value       TEXT,
  score       INTEGER DEFAULT 0,
  notes       TEXT,
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_tool     ON findings(source_tool);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_score    ON findings(score DESC);
CREATE INDEX IF NOT EXISTS idx_findings_key      ON findings(key);
"""


def log(msg):
    print(f"[{utc_now()}] {msg}", flush=True)


def parse_ollama_urls(cli_value, env=os.environ):
    raw = cli_value or env.get("OLLAMA_HOSTS") or env.get("OLLAMA_HOST") or "http://localhost:11434"
    urls = []
    seen = set()
    for part in raw.split(","):
        url = part.strip().rstrip("/")
        if not url or url in seen:
            continue
        urls.append(url)
        seen.add(url)
    return urls or ["http://localhost:11434"]


def default_worker_count(ollama_urls, requested):
    if requested is not None:
        return max(1, requested)
    return max(1, len(ollama_urls))


def open_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in FINDINGS_DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    return conn


def record_start(conn, scope, min_size, limit, model, ollama_urls, workers):
    ollama_label = ",".join(ollama_urls)
    cmd = (f"image-tag-photos.py <db> --scope {scope} --min-size {min_size} "
           f"--model {model} --ollama {ollama_label} --workers {workers}"
           + (f" --limit {limit}" if limit else ""))
    cur = conn.execute(
        "INSERT INTO scan_runs (stage, status, started_at, command_line) VALUES (?,?,?,?)",
        (STAGE, "running", utc_now(), cmd),
    )
    conn.commit()
    return cur.lastrowid


def record_end(conn, run_id, status, notes=""):
    conn.execute(
        "UPDATE scan_runs SET status=?, ended_at=?, notes=? WHERE id=?",
        (status, utc_now(), notes, run_id),
    )
    conn.commit()


def is_tagged(conn, artifact_id):
    return conn.execute(
        "SELECT 1 FROM findings WHERE source_tool='llava' AND artifact_id=? AND key='description'",
        (artifact_id,),
    ).fetchone() is not None


def store_description(conn, artifact_id, full_path, description):
    conn.execute(
        "INSERT OR REPLACE INTO findings "
        "(source_tool, category, artifact_id, path, key, value, score, created_at) "
        "VALUES ('llava','photo-description',?,?,'description',?,50,?)",
        (artifact_id, full_path, description, utc_now()),
    )
    conn.commit()


def call_llava(ollama_url, model, image_path, prompt, timeout=120):
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "options": {"temperature": 0.1},
    }).encode()
    req = urllib.request.Request(
        ollama_url.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["response"].strip()


def probe_ollama(ollama_url, model):
    req = urllib.request.Request(ollama_url.rstrip("/") + "/api/tags")
    with urllib.request.urlopen(req, timeout=10) as r:
        models = [m["name"] for m in json.loads(r.read()).get("models", [])]
    base = model.split(":")[0]
    if not any(base in m for m in models):
        print(f"WARNING: '{model}' not found. Available: {', '.join(models)}")
    else:
        print(f"Ollama OK — model '{model}' available")


def fmt_eta(seconds):
    if seconds <= 0:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return (f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("db", help="Path to *.analysis.sqlite database")
    ap.add_argument("--scope", choices=["real", "all"], default="real",
                    help="real=JPEG>=min-size (default); all=every image/* artifact")
    ap.add_argument("--min-size", type=int, default=None,
                    help="Min file size in bytes (default: 20480 for real, 10240 for all)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Stop after N images (useful for dry-runs)")
    ap.add_argument("--force", action="store_true",
                    help="Re-tag images already present in findings")
    ap.add_argument("--model", default="llava:7b",
                    help="Ollama vision model name (default: llava:7b)")
    ap.add_argument("--ollama", default=None,
                    help="Ollama base URL or comma-separated URLs (default: $OLLAMA_HOSTS, $OLLAMA_HOST, or http://localhost:11434)")
    ap.add_argument("--workers", type=int, default=None,
                    help="Parallel Ollama calls (default: number of configured Ollama URLs)")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="Prompt sent to the model for every image")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print candidates without calling Ollama")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="Total attempts per image (1 initial + N-1 retry rounds, default 3)")
    ap.add_argument("--retry-backoff", type=float, default=5.0,
                    help="Seconds to sleep between retry rounds (default 5)")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f"Database not found: {args.db}")

    ollama_urls = parse_ollama_urls(args.ollama)
    workers = default_worker_count(ollama_urls, args.workers)
    min_size = args.min_size if args.min_size is not None else (20480 if args.scope == "real" else 10240)

    log(f"DB:       {args.db}")
    log(f"Scope:    {args.scope}  |  min-size: {min_size:,} B  |  model: {args.model}")
    log(f"Ollama:   {', '.join(ollama_urls)}")
    log(f"Workers:  {workers}")
    log(f"Max attempts per image: {args.max_attempts}  |  retry backoff: {args.retry_backoff:.1f}s")
    if args.dry_run:
        log("DRY RUN — no Ollama calls will be made")

    if not args.dry_run:
        for ollama_url in ollama_urls:
            try:
                probe_ollama(ollama_url, args.model)
            except Exception as e:
                sys.exit(f"Cannot reach Ollama at {ollama_url}: {e}")

    conn = open_db(args.db)

    if args.scope == "real":
        where = "mime_type = 'image/jpeg' AND size_bytes >= ?"
    else:
        where = "mime_type LIKE 'image/%' AND size_bytes >= ?"

    candidates = conn.execute(
        f"SELECT id, full_path, mime_type, size_bytes, method "
        f"FROM recovered_artifacts "
        f"WHERE {where} AND full_path IS NOT NULL "
        f"ORDER BY size_bytes DESC",
        (min_size,),
    ).fetchall()

    total = len(candidates)
    log(f"Candidates: {total:,}")
    if total == 0:
        log("Nothing to tag.")
        conn.close()
        return

    if args.dry_run:
        for row in candidates[:20]:
            log(f"  {row['method']:12s}  {(row['size_bytes'] or 0)/1024:7.0f} KB  {row['full_path']}")
        if total > 20:
            log(f"  … and {total - 20} more")
        conn.close()
        return

    run_id = record_start(conn, args.scope, min_size, args.limit, args.model, ollama_urls, workers)

    tagged = skipped = 0
    pending = []   # rows that errored on this attempt — retried at end of pass
    missing = 0
    t_start = time.time()

    def attempt(row, label, ollama_url):
        """Try to tag one image. Returns a result tuple for main-thread DB writes."""
        artifact_id = row["id"]
        full_path   = row["full_path"]
        size_bytes  = row["size_bytes"] or 0

        if not os.path.isfile(full_path):
            return ("missing", row, full_path, "", 0.0, "")

        try:
            t1 = time.time()
            desc = call_llava(ollama_url, args.model, full_path, args.prompt)
            took = time.time() - t1
            return ("ok", row, full_path, desc, took, ollama_url)
        except Exception as e:
            return ("error", row, full_path, str(e), 0.0, ollama_url)

    def run_batch(rows, label_prefix):
        nonlocal tagged, missing
        failed = []
        if not rows:
            return failed
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {}
            for j, row in enumerate(rows):
                ollama_url = ollama_urls[j % len(ollama_urls)]
                label = f"{label_prefix} {j+1}/{len(rows)}"
                size_bytes = row["size_bytes"] or 0
                log(f"[{label}] {Path(row['full_path']).name} ({size_bytes/1024:.0f} KB) via {ollama_url} ...")
                future = pool.submit(attempt, row, label, ollama_url)
                future_map[future] = label
            for future in concurrent.futures.as_completed(future_map):
                label = future_map[future]
                status, row, full_path, value, took, ollama_url = future.result()
                if status == "ok":
                    store_description(conn, row["id"], full_path, value)
                    tagged += 1
                    log(f"[{label}] OK in {took:.1f}s via {ollama_url} - {value[:120]}")
                elif status == "missing":
                    missing += 1
                    log(f"[{label}] MISSING: {full_path}")
                else:
                    failed.append(row)
                    log(f"[{label}] ERROR via {ollama_url}: {value}")
        return failed

    try:
        # ---- Pass 1: walk all candidates ----
        first_pass = []
        for i, row in enumerate(candidates):
            if args.limit is not None and len(first_pass) >= args.limit:
                break

            if not args.force and is_tagged(conn, row["id"]):
                skipped += 1
                continue

            elapsed = time.time() - t_start
            rate = tagged / elapsed if elapsed > 0 and tagged > 0 else 0
            remaining_count = total - i - skipped
            eta = fmt_eta(remaining_count / rate) if rate > 0 else "?"
            log(f"[{i+1}/{total}] eta {eta} queued")
            first_pass.append(row)

        pending = run_batch(first_pass, "pass 1")

        # ---- Retry passes for transient failures ----
        round_idx = 1
        while pending and round_idx < args.max_attempts:
            if args.retry_backoff > 0:
                log(f"Retry round {round_idx}/{args.max_attempts - 1}: "
                    f"sleeping {args.retry_backoff:.1f}s before retrying "
                    f"{len(pending)} image(s)")
                time.sleep(args.retry_backoff)
            log(f"Retry round {round_idx}/{args.max_attempts - 1}: "
                f"{len(pending)} image(s) to retry")
            pending = run_batch(pending, f"retry {round_idx}")
            round_idx += 1

    except KeyboardInterrupt:
        log(f"Interrupted — {tagged} tagged so far. Re-run to resume.")

    errors = len(pending) + missing
    status = "ok" if errors == 0 else ("partial" if tagged > 0 else "failed")
    notes = (f"tagged={tagged} skipped={skipped} errors={errors} "
             f"(retry_failures={len(pending)} missing={missing})")
    record_end(conn, run_id, status, notes)

    if pending:
        log(f"Images still failing after {args.max_attempts} attempts "
            f"(will be retried on next run since no findings row was written):")
        for row in pending[:20]:
            log(f"  - {row['full_path']}")
        if len(pending) > 20:
            log(f"  … and {len(pending) - 20} more")

    conn.close()

    elapsed = time.time() - t_start
    log(f"Done: {tagged} tagged, {skipped} already done, {errors} errors "
        f"({len(pending)} retry-exhausted, {missing} missing) — {elapsed:.0f}s total")
    if tagged > 0:
        log(f"Average: {elapsed/tagged:.1f}s per image")


if __name__ == "__main__":
    main()
