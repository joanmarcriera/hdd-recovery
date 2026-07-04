#!/usr/bin/env python3
"""Push curated recovered photos into Immich, one album per source disk.

Selects real photos from every ``*.analysis.sqlite`` catalog (dedup primaries,
size floor, quality gate, camera/photo formats only — the same definition the
inventory counts), uploads each to Immich via its REST API, and files it in an
album named after the source disk. Immich dedupes exact copies by checksum on
upload, so re-runs and cross-disk exact duplicates are cheap; this tool also
records every upload in the per-image ``exports`` table and skips already-pushed
files on re-run.

It reads recovered files and the catalogs; it never touches source media. The
raw disk images are never uploaded — only carved/recovered photo files.

Config (env, never in the repo):
  IMMICH_INSTANCE_URL   e.g. http://nas.lan:2283   (the /api suffix is optional)
  IMMICH_API_KEY        an Immich API key

Usage:
  immich-export.py [--root <dir>] [--db <path>] [--run]
                   [--min-quality N] [--min-size BYTES] [--limit N]
                   [--album-prefix STR]

Dry-run by default: prints per-disk counts + album names + total bytes and
writes a manifest CSV. Pass --run to upload.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib import harvest  # noqa: E402
from lib.db import open_writable_db  # noqa: E402
from lib.exportlog import exported_refs, record_export  # noqa: E402
from lib.timestamp import utc_now  # noqa: E402
from lib.upload_http import http_json, http_upload_file  # noqa: E402

SOURCE_KIND = "immich-upload"
DEVICE_ID = "hdd-recovery"


def log(msg):
    print(f"[{utc_now()}] {msg}", flush=True)


def api_base(url):
    url = (url or "").rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    return url


def iso_created_at(row):
    """fileCreatedAt for Immich: the exiftool 'taken_at' if parseable, else the
    file mtime, else now. Immich still reads embedded EXIF for the timeline."""
    taken = row.get("taken_at")
    if taken:
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
            try:
                return datetime.strptime(taken[:19], fmt).replace(
                    tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
    fp = row.get("full_path")
    try:
        return datetime.fromtimestamp(os.path.getmtime(fp), timezone.utc).isoformat()
    except OSError:
        return datetime.now(timezone.utc).isoformat()


def device_asset_id(label, row):
    return row.get("sha256") or f"{label}:{row['id']}"


def resolve_album(base, headers, name, existing):
    """Return album id for ``name``, creating it if absent. ``existing`` is a
    cached {albumName: id} map that this fills lazily."""
    if name in existing:
        return existing[name]
    status, body = http_json("POST", f"{base}/api/albums", headers=headers,
                             json_body={"albumName": name})
    if status in (200, 201) and isinstance(body, dict) and body.get("id"):
        existing[name] = body["id"]
        return body["id"]
    raise RuntimeError(f"album create failed ({status}): {body}")


def load_existing_albums(base, headers):
    status, body = http_json("GET", f"{base}/api/albums", headers=headers)
    out = {}
    if status == 200 and isinstance(body, list):
        for a in body:
            if isinstance(a, dict) and a.get("albumName"):
                out[a["albumName"]] = a.get("id")
    return out


def upload_one(base, headers, row):
    """Upload a single asset. Returns (asset_id, status_str) or (None, error)."""
    fp = row["full_path"]
    if not fp or not os.path.isfile(fp):
        return None, "missing file"
    dev_asset = device_asset_id(row["_label"], row)
    created = iso_created_at(row)
    fields = {
        "deviceAssetId": dev_asset,
        "deviceId": DEVICE_ID,
        "fileCreatedAt": created,
        "fileModifiedAt": created,
        "isFavorite": "false",
    }
    status, body = http_upload_file(
        f"{base}/api/assets", fp, "assetData", fields, headers=headers,
        content_type=row.get("mime_type") or "application/octet-stream")
    if status in (200, 201) and isinstance(body, dict) and body.get("id"):
        return body["id"], body.get("status", "created")
    return None, f"upload failed ({status}): {body}"


def process_db(db_path, args, base, headers, album_cache, writer):
    label = harvest.db_disk_label(db_path)
    album_name = f"{args.album_prefix}{label}" if args.album_prefix else label
    photos = harvest.curated_photos(db_path, min_size=args.min_size,
                                    min_quality=args.min_quality)
    for p in photos:
        p["_label"] = label

    conn = open_writable_db(db_path)
    try:
        done = exported_refs(conn, SOURCE_KIND)
        pending = [p for p in photos if device_asset_id(label, p) not in done]
        if args.limit:
            pending = pending[: args.limit]
        total_bytes = sum(p.get("size_bytes") or 0 for p in pending)
        log(f"{label}: {len(photos)} curated, {len(pending)} to upload "
            f"({total_bytes/1048576:.0f} MB) -> album '{album_name}'")

        for p in pending:
            writer.writerow([label, album_name, p["full_path"],
                             p.get("size_bytes") or 0, p.get("mime_type") or "",
                             p.get("quality_score") if p.get("quality_score") is not None else ""])
        if not args.run:
            return len(pending), 0, total_bytes

        album_id = resolve_album(base, headers, album_name, album_cache)
        uploaded, asset_ids, failed = 0, [], 0
        for p in pending:
            asset_id, note = upload_one(base, headers, p)
            if asset_id:
                asset_ids.append(asset_id)
                record_export(conn, SOURCE_KIND, device_asset_id(label, p),
                              p.get("relative_path"), p["full_path"],
                              sha256=p.get("sha256"), size_bytes=p.get("size_bytes"),
                              notes=f"album={album_name} asset={asset_id} status={note}")
                uploaded += 1
            else:
                failed += 1
                log(f"  ! {os.path.basename(p['full_path'])}: {note}")
        if asset_ids:
            st, body = http_json("PUT", f"{base}/api/albums/{album_id}/assets",
                                 headers=headers, json_body={"ids": asset_ids})
            if st not in (200, 201):
                log(f"  ! album add failed ({st}): {body}")
        log(f"{label}: uploaded {uploaded}, failed {failed}")
        return uploaded, failed, total_bytes
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("DB_ROOT")
                    or os.environ.get("RECOVERY_ROOT") or "/data/db")
    ap.add_argument("--db", help="Export a single catalog instead of the whole root")
    ap.add_argument("--run", action="store_true", help="Perform uploads (default: dry-run)")
    ap.add_argument("--min-quality", type=float, default=harvest.PHOTO_MIN_QUALITY)
    ap.add_argument("--min-size", type=int, default=harvest.PHOTO_MIN_SIZE)
    ap.add_argument("--limit", type=int, default=0, help="Cap uploads per disk (testing)")
    ap.add_argument("--album-prefix", default="", help="Prefix for album names")
    ap.add_argument("--manifest-dir", default=None,
                    help="Where to write the manifest CSV (default: <root>/../reports)")
    args = ap.parse_args(argv)

    dbs = [args.db] if args.db else harvest.iter_databases(args.root)
    if not dbs:
        log(f"No catalogs found under {args.root}")
        return 1

    base, headers, album_cache = "", {}, {}
    if args.run:
        url = os.environ.get("IMMICH_INSTANCE_URL", "")
        key = os.environ.get("IMMICH_API_KEY", "")
        if not url or not key:
            log("ERROR: set IMMICH_INSTANCE_URL and IMMICH_API_KEY to upload.")
            return 2
        base = api_base(url)
        headers = {"x-api-key": key, "Accept": "application/json"}
        album_cache = load_existing_albums(base, headers)

    man_dir = args.manifest_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.root)), "reports")
    os.makedirs(man_dir, exist_ok=True)
    stamp = utc_now().replace("-", "").replace(":", "")
    manifest = os.path.join(man_dir, f"immich-export-{stamp}.csv")

    # In --run mode process_db returns the uploaded count as the first element;
    # in dry-run it returns the selected count. Either way it is "photos acted on".
    tot_acted = tot_fail = tot_bytes = 0
    with open(manifest, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["disk", "album", "full_path", "size_bytes", "mime_type", "quality"])
        for db in dbs:
            acted, failed, size = process_db(db, args, base, headers, album_cache, writer)
            tot_acted += acted
            tot_fail += failed
            tot_bytes += size

    mode = "UPLOADED" if args.run else "WOULD UPLOAD (dry-run)"
    log(f"{mode}: {tot_acted} photo(s), {tot_bytes/1048576:.0f} MB, failures={tot_fail}")
    log(f"Manifest: {manifest}")
    if not args.run:
        log("Re-run with --run to upload (needs IMMICH_INSTANCE_URL + IMMICH_API_KEY).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
