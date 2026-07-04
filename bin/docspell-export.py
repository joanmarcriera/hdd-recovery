#!/usr/bin/env python3
"""Push curated recovered documents into Docspell.

Selects documents from every ``*.analysis.sqlite`` catalog (pdf/office/rtf/epub
by MIME or TrID; text/plain is opt-in), de-duplicates identical files by sha256
across disks, and uploads each to Docspell via its anonymous multipart upload
API. Each item is tagged with its source disk. Docspell also skips duplicates
server-side; this tool records every upload in the per-image ``exports`` table
and skips already-pushed files on re-run.

It reads recovered files and the catalogs; it never touches source media.

Ingestion (choose via env — Source URL is recommended, decoupled and needs no
uid/gid match with Docspell's 3006:3006 / /data storage):
  DOCSPELL_URL              e.g. http://nas.lan:7880
  DOCSPELL_SOURCE_ID        a Source id created in the Docspell web UI
    ── or the integration endpoint (must be enabled server-side) ──
  DOCSPELL_COLLECTIVE       collective name/id
  DOCSPELL_INTEGRATION_SECRET   the endpoint secret (sent as Docspell-Integration)

Usage:
  docspell-export.py [--root <dir>] [--db <path>] [--run]
                     [--min-size BYTES] [--include-text] [--limit N]

Dry-run by default: prints per-disk counts and writes a manifest CSV. Pass --run
to upload.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import harvest  # noqa: E402
from lib.db import open_writable_db  # noqa: E402
from lib.exportlog import exported_refs, record_export  # noqa: E402
from lib.timestamp import utc_now  # noqa: E402
from lib.upload_http import http_upload_file  # noqa: E402

SOURCE_KIND = "docspell-upload"


def log(msg):
    print(f"[{utc_now()}] {msg}", flush=True)


def resolve_endpoint():
    """Return (url, headers) for the configured Docspell ingestion mode, or raise."""
    base = os.environ.get("DOCSPELL_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("set DOCSPELL_URL")
    source_id = os.environ.get("DOCSPELL_SOURCE_ID", "")
    if source_id:
        return f"{base}/api/v1/open/upload/item/{source_id}", {}
    collective = os.environ.get("DOCSPELL_COLLECTIVE", "")
    secret = os.environ.get("DOCSPELL_INTEGRATION_SECRET", "")
    if collective and secret:
        return (f"{base}/api/v1/open/integration/item/{collective}",
                {"Docspell-Integration": secret})
    raise RuntimeError("set DOCSPELL_SOURCE_ID, or DOCSPELL_COLLECTIVE + "
                       "DOCSPELL_INTEGRATION_SECRET")


def doc_ref(label, row):
    return row.get("sha256") or f"{label}:{row['id']}"


def upload_one(url, headers, row, label):
    """Upload a single document with source-disk tag. Returns (ok, note)."""
    fp = row["full_path"]
    if not fp or not os.path.isfile(fp):
        return False, "missing file"
    meta = json.dumps({
        "multiple": False,
        "skipDuplicates": True,
        "tags": {"items": [f"disk:{label}"]},
    })
    status, body = http_upload_file(
        url, fp, "file", {"meta": meta}, headers=headers,
        content_type=row.get("mime_type") or "application/octet-stream")
    if status == 200:
        msg = body.get("message") if isinstance(body, dict) else str(body)
        return True, msg or "submitted"
    return False, f"upload failed ({status}): {body}"


def process_db(db_path, args, url, headers, seen_sha, writer):
    label = harvest.db_disk_label(db_path)
    docs = harvest.curated_docs(db_path, min_size=args.min_size,
                                include_text=args.include_text)
    conn = open_writable_db(db_path)
    try:
        done = exported_refs(conn, SOURCE_KIND)
        acted = failed = deduped = 0
        pending = []
        for d in docs:
            ref = doc_ref(label, d)
            if ref in done:
                continue                      # resume: already pushed
            sha = d.get("sha256")
            if sha and sha in seen_sha:
                deduped += 1                  # identical content already sent this run
                continue
            pending.append(d)
        if args.limit:
            pending = pending[: args.limit]
        total_bytes = sum(d.get("size_bytes") or 0 for d in pending)
        log(f"{label}: {len(docs)} curated, {len(pending)} to upload "
            f"({total_bytes/1048576:.0f} MB), {deduped} cross-disk dup(s) skipped")

        for d in pending:
            writer.writerow([label, d["full_path"], d.get("size_bytes") or 0,
                             d.get("mime_type") or "", d.get("trid_top_ext") or ""])
        if not args.run:
            return len(pending), 0, deduped, total_bytes

        for d in pending:
            ok, note = upload_one(url, headers, d, label)
            if ok:
                if d.get("sha256"):
                    seen_sha.add(d["sha256"])
                record_export(conn, SOURCE_KIND, doc_ref(label, d),
                              d.get("relative_path"), d["full_path"],
                              sha256=d.get("sha256"), size_bytes=d.get("size_bytes"),
                              notes=note)
                acted += 1
            else:
                failed += 1
                log(f"  ! {os.path.basename(d['full_path'])}: {note}")
        log(f"{label}: uploaded {acted}, failed {failed}")
        return acted, failed, deduped, total_bytes
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("DB_ROOT")
                    or os.environ.get("RECOVERY_ROOT") or "/data/db")
    ap.add_argument("--db", help="Export a single catalog instead of the whole root")
    ap.add_argument("--run", action="store_true", help="Perform uploads (default: dry-run)")
    ap.add_argument("--min-size", type=int, default=harvest.DOC_MIN_SIZE)
    ap.add_argument("--include-text", action="store_true",
                    help="Also upload text/plain (noisy; off by default)")
    ap.add_argument("--limit", type=int, default=0, help="Cap uploads per disk (testing)")
    ap.add_argument("--manifest-dir", default=None,
                    help="Where to write the manifest CSV (default: <root>/../reports)")
    args = ap.parse_args(argv)

    dbs = [args.db] if args.db else harvest.iter_databases(args.root)
    if not dbs:
        log(f"No catalogs found under {args.root}")
        return 1

    url, headers = "", {}
    if args.run:
        try:
            url, headers = resolve_endpoint()
        except RuntimeError as e:
            log(f"ERROR: {e}")
            return 2

    man_dir = args.manifest_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.root)), "reports")
    os.makedirs(man_dir, exist_ok=True)
    stamp = utc_now().replace("-", "").replace(":", "")
    manifest = os.path.join(man_dir, f"docspell-export-{stamp}.csv")

    seen_sha = set()
    tot_acted = tot_fail = tot_dedup = tot_bytes = 0
    with open(manifest, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["disk", "full_path", "size_bytes", "mime_type", "trid_ext"])
        for db in dbs:
            acted, failed, deduped, size = process_db(
                db, args, url, headers, seen_sha, writer)
            tot_acted += acted
            tot_fail += failed
            tot_dedup += deduped
            tot_bytes += size

    mode = "UPLOADED" if args.run else "WOULD UPLOAD (dry-run)"
    log(f"{mode}: {tot_acted} doc(s), {tot_bytes/1048576:.0f} MB, "
        f"cross-disk dups skipped={tot_dedup}, failures={tot_fail}")
    log(f"Manifest: {manifest}")
    if not args.run:
        log("Re-run with --run to upload (needs DOCSPELL_URL + DOCSPELL_SOURCE_ID).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
