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

import argparse, base64, json, os, sqlite3, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

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


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def record_start(conn, scope, min_size, limit, model, ollama_url):
    cmd = (f"image-tag-photos.py <db> --scope {scope} --min-size {min_size} "
           f"--model {model} --ollama {ollama_url}"
           + (f" --limit {limit}" if limit else ""))
    cur = conn.execute(
        "INSERT INTO scan_runs (stage, status, started_at, command_line) VALUES (?,?,?,?)",
        (STAGE, "running", now_iso(), cmd),
    )
    conn.commit()
    return cur.lastrowid


def record_end(conn, run_id, status, notes=""):
    conn.execute(
        "UPDATE scan_runs SET status=?, ended_at=?, notes=? WHERE id=?",
        (status, now_iso(), notes, run_id),
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
        (artifact_id, full_path, description, now_iso()),
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
                    help="Ollama base URL (default: $OLLAMA_HOST or http://localhost:11434)")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="Prompt sent to the model for every image")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print candidates without calling Ollama")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f"Database not found: {args.db}")

    ollama_url = args.ollama or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    min_size = args.min_size if args.min_size is not None else (20480 if args.scope == "real" else 10240)

    print(f"DB:       {args.db}")
    print(f"Scope:    {args.scope}  |  min-size: {min_size:,} B  |  model: {args.model}")
    print(f"Ollama:   {ollama_url}")
    if args.dry_run:
        print("DRY RUN — no Ollama calls will be made")
    print()

    if not args.dry_run:
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
    print(f"Candidates: {total:,}")
    if total == 0:
        print("Nothing to tag.")
        conn.close()
        return

    if args.dry_run:
        for row in candidates[:20]:
            print(f"  {row['method']:12s}  {(row['size_bytes'] or 0)/1024:7.0f} KB  {row['full_path']}")
        if total > 20:
            print(f"  … and {total - 20} more")
        conn.close()
        return

    run_id = record_start(conn, args.scope, min_size, args.limit, args.model, ollama_url)

    tagged = skipped = errors = 0
    t_start = time.time()

    try:
        for i, row in enumerate(candidates):
            if args.limit is not None and tagged >= args.limit:
                break

            artifact_id = row["id"]
            full_path   = row["full_path"]
            size_bytes  = row["size_bytes"] or 0

            if not args.force and is_tagged(conn, artifact_id):
                skipped += 1
                continue

            if not os.path.isfile(full_path):
                errors += 1
                print(f"[{i+1}/{total}] MISSING: {full_path}")
                continue

            elapsed = time.time() - t_start
            rate = tagged / elapsed if elapsed > 0 and tagged > 0 else 0
            remaining_count = total - i - skipped
            eta = fmt_eta(remaining_count / rate) if rate > 0 else "?"

            print(
                f"[{i+1}/{total}] {Path(full_path).name} "
                f"({size_bytes/1024:.0f} KB)  eta {eta} …",
                end="", flush=True,
            )

            try:
                t1 = time.time()
                desc = call_llava(ollama_url, args.model, full_path, args.prompt)
                took = time.time() - t1
                store_description(conn, artifact_id, full_path, desc)
                tagged += 1
                print(f" {took:.1f}s")
                print(f"    {desc[:120]}")
            except Exception as e:
                errors += 1
                print(f" ERROR: {e}")

    except KeyboardInterrupt:
        print(f"\nInterrupted — {tagged} tagged so far. Re-run to resume.")

    status = "ok" if errors == 0 else ("partial" if tagged > 0 else "failed")
    notes = f"tagged={tagged} skipped={skipped} errors={errors}"
    record_end(conn, run_id, status, notes)
    conn.close()

    elapsed = time.time() - t_start
    print(f"\nDone: {tagged} tagged, {skipped} already done, {errors} errors — {elapsed:.0f}s total")
    if tagged > 0:
        print(f"Average: {elapsed/tagged:.1f}s per image")


if __name__ == "__main__":
    main()
