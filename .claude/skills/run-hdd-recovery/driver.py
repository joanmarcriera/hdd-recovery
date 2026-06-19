#!/usr/bin/env python3
"""Driver for the hdd-recovery web review UI (bin/image-serve.py).

The review UI is a stdlib http.server app that renders SQLite analysis
databases (one per disk image) plus the carved/recovered image files they
point at. It needs *data* to be interesting, so this driver synthesizes a
throwaway workspace — a sample `*.analysis.sqlite` DB and a real JPEG carved
"artifact" — then launches the server against it.

Modes:
  smoke   (default) launch, assert the key routes/headers, tear down, exit
          non-zero on any failure. This is the agent path.
  serve   launch against the sample workspace and stay up (Ctrl-C to stop)
          so you can open it in a browser. Prints the URL.

Both modes print the workspace path. Requires: python3 + Pillow (PIL).
"""
from __future__ import annotations

import argparse
import http.client
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]          # <unit> = repo root
SERVE = REPO / "bin" / "image-serve.py"
SCHEMA = REPO / "sql" / "analysis-schema.sql"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_workspace(work: Path) -> tuple[str, Path]:
    """Create root/<db> + a carved JPEG. Returns (db_path, jpeg_path)."""
    try:
        from PIL import Image
    except Exception:
        sys.exit("FATAL: Pillow not installed. Install it: "
                 "pip3 install pillow  (or: apt-get install -y python3-pil)")

    # The server root must contain BOTH the DBs and the carved files they
    # point at (full_path is rejected with 403 if it resolves outside root).
    img_root = work / "images"
    export_root = work / "exports" / "sample"
    carved = export_root / "recovered" / "foremost"
    for d in (img_root, carved):
        d.mkdir(parents=True, exist_ok=True)

    # A real, deliberately large-ish image so the thumbnail is visibly smaller.
    jpeg = carved / "00000001.jpg"
    Image.new("RGB", (1800, 1200), (40, 120, 90)).save(jpeg, "JPEG", quality=92)
    img_size = jpeg.stat().st_size

    image_path = img_root / "sample.img"
    image_path.write_bytes(b"\x00" * 4096)            # placeholder image
    db_path = str(img_root / "sample.img.analysis.sqlite")

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA.read_text())
    # image_size_bytes is stored, not derived from the (placeholder) file — the
    # home page should prefer it. Use a realistic raw-disk size.
    conn.execute(
        "INSERT INTO image_info (id,image_path,image_name,image_basename,"
        "image_size_bytes,export_root,created_at,updated_at) VALUES (1,?,?,?,?,?,?,?)",
        (str(image_path), "sample.img", "sample", 320072933376,
         str(export_root), _now(), _now()),
    )
    # A spread of stages with varied status so the dashboard's stage column /
    # badges have something realistic to render.
    for stage, status in [
        ("structure-scan", "ok"), ("index-tsk", "ok"),
        ("detect-wallets", "ok"), ("detect-pictures", "partial"),
        ("carve-foremost", "ok"), ("carve-scalpel", "running"),
        ("bulk-extractor-raw", "failed"),
    ]:
        ended = None if status == "running" else _now()
        conn.execute(
            "INSERT INTO scan_runs (stage,status,started_at,ended_at) "
            "VALUES (?,?,?,?)", (stage, status, _now(), ended),
        )
    conn.execute(
        "INSERT INTO recovered_artifacts (method,relative_path,full_path,"
        "size_bytes,mime_type,created_at) VALUES "
        "('foremost','recovered/foremost/00000001.jpg',?,?,?,?)",
        (str(jpeg), img_size, "image/jpeg", _now()),
    )
    # one file + picture/wallet candidate so the home counts and pages render
    conn.execute(
        "INSERT INTO files (source_tool,path,name,extension,size_bytes,mime_type) "
        "VALUES ('fiwalk','/DCIM/IMG_0001.jpg','IMG_0001.jpg','jpg',?, 'image/jpeg')",
        (img_size,),
    )
    fid = conn.execute("SELECT id FROM files LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO picture_candidates (file_id,source_stage,score,reason,"
        "camera_model,taken_at,created_at) VALUES (?,'detect',80,'ext',"
        "'TestCam','2019-08-01',?)", (fid, _now()),
    )
    conn.execute(
        "INSERT INTO wallet_candidates (file_id,source_stage,score,reason,"
        "created_at) VALUES (?,'detect',50,'keyword:wallet',?)", (fid, _now()),
    )
    conn.commit()
    conn.close()
    return db_path, jpeg


def wait_up(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            c.request("GET", "/")
            c.getresponse().read()
            c.close()
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"server did not come up on :{port}")


def req(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, body


def smoke(port: int, db_path: str, jpeg: Path) -> int:
    enc_db = urllib.parse.quote(db_path)
    enc_img = urllib.parse.quote(str(jpeg))
    passed, failed = 0, 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  ok   {name}")
        else:
            failed += 1
            print(f"  FAIL {name}  {detail}")

    # home: gzip + version footer
    st, hd, body = req(port, "/", {"Accept-Encoding": "gzip"})
    check("GET / → 200", st == 200, f"status={st}")
    check("home gzipped", hd.get("content-encoding") == "gzip", str(hd))
    import gzip as _gz
    home_txt = _gz.decompress(body).decode("utf-8", "replace")
    check("home shows build footer", "build " in home_txt, "no footer")
    check("home shows image name (not db filename)", ">sample.img</a>" in home_txt)
    check("home has Size + Stages columns", ">Size<" in home_txt and ">Stages<" in home_txt)
    check("home shows human image size", "298.1 GB" in home_txt, "image_size_bytes not rendered")
    check("home shows stage-group chips", 'class="chip' in home_txt and ">fast</span>" in home_txt)

    # queue page: lists images + presets, has the start control
    st, hd, body = req(port, "/queue")
    q = body.decode("utf-8", "replace")
    check("GET /queue → 200", st == 200, f"status={st}")
    check("queue lists the image", "sample.img" in q)
    check("queue offers presets + start", 'name="preset" value="fast"' in q and "Start queue" in q)
    # POST /queue with nothing selected must validate (400), not spawn anything
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", "/queue", body="", headers={"Content-Type": "application/x-www-form-urlencoded"})
    rp = c.getresponse(); rp.read(); c.close()
    check("POST /queue empty → 400", rp.status == 400, f"status={rp.status}")

    # DB detail page renders the stage history we seeded
    st, hd, body = req(port, f"/db?db={enc_db}")
    dbpage = body.decode("utf-8", "replace")
    check("GET /db → 200", st == 200, f"status={st}")
    check("db page shows seeded stages", "carve-foremost" in dbpage and "index-tsk" in dbpage)

    # gallery points <img> at /thumb, not full-res /file
    st, hd, body = req(port, f"/gallery?db={enc_db}")
    gal = body.decode("utf-8", "replace")
    check("GET /gallery → 200", st == 200, f"status={st}")
    check("gallery uses /thumb thumbnails", 'src="/thumb?path=' in gal)

    # thumbnail: jpeg, ETag, and smaller than the original
    st, hd, body = req(port, f"/thumb?path={enc_img}")
    etag = hd.get("etag", "")
    orig = jpeg.stat().st_size
    check("GET /thumb → 200 image/jpeg", st == 200 and hd.get("content-type") == "image/jpeg")
    check("thumb has ETag + Cache-Control", bool(etag) and "max-age" in hd.get("cache-control", ""))
    check(f"thumb < original ({len(body)} < {orig} B)", len(body) < orig)

    # conditional GET → 304
    st, hd, body = req(port, f"/thumb?path={enc_img}", {"If-None-Match": etag})
    check("thumb revalidation → 304", st == 304, f"status={st}")

    # full file is cached too
    st, hd, body = req(port, f"/file?path={enc_img}")
    check("GET /file → 200 with ETag", st == 200 and bool(hd.get("etag")))

    # path traversal blocked
    st, hd, body = req(port, "/file?path=/etc/passwd")
    check("traversal /etc/passwd → 403", st == 403, f"status={st}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", nargs="?", default="smoke", choices=["smoke", "serve"])
    ap.add_argument("--port", type=int, default=7799)
    ap.add_argument("--keep", action="store_true", help="keep the workspace on exit")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="hdd-run-"))
    db_path, jpeg = build_workspace(work)
    root = work
    print(f"workspace: {work}")
    print(f"db:        {db_path}")

    env = dict(os.environ, APP_VERSION="driver-smoke")
    proc = subprocess.Popen(
        [sys.executable, str(SERVE), "--root", str(root),
         "--host", "127.0.0.1", "--port", str(args.port)],
        env=env,
    )
    try:
        wait_up(args.port)
        if args.mode == "serve":
            print(f"\nserving → http://127.0.0.1:{args.port}/   (Ctrl-C to stop)")
            signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
            proc.wait()
            return 0
        print(f"\nsmoke-testing http://127.0.0.1:{args.port}/ ...")
        return smoke(args.port, db_path, jpeg)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if args.keep:
            print(f"workspace kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
