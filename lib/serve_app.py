"""HTTP request handler and CLI entrypoint for the review UI.

Extracted from bin/image-serve.py (#18). Route dispatch and response streaming
live here; rendering, DB helpers, gallery helpers, pipeline helpers, auth, and
map parsing are separate modules.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import http.server
import json
import mimetypes
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BIN_DIR = ROOT / "bin"

from lib.serve_auth import _webui_auth_ok  # noqa: E402
from lib.serve_db import find_databases, query_scalar, run_query, safe_sql  # noqa: E402
from lib.serve_gallery import (  # noqa: E402
    _resolve_under_root,
    export_file_via_icat,
    make_thumbnail,
    page_gallery,
    page_gallery_fs,
)
from lib.serve_pages import (  # noqa: E402
    _queue_progress_html,
    page_artifacts,
    page_bulk_hits,
    page_db,
    page_files,
    page_findings,
    page_help,
    page_home,
    page_mapview,
    page_pictures,
    page_queue,
    page_queue_log,
    page_sql,
    page_timeline,
    page_wallets,
    queue_log_payload,
)
from lib.serve_pipeline import (  # noqa: E402
    _queue_log_dir,
    cancel_active_pipeline,
    cancel_active_queue,
    page_pipeline_log,
    pipeline_active_for,
    queue_active,
    request_stop_active_pipeline,
    request_stop_active_queue,
    spawn_pipeline,
    spawn_queue,
)
from lib.serve_queue_log import queue_progress_cached as _queue_progress_cached  # noqa: E402
from lib.serve_ui import h, human_size as _human_size, page  # noqa: E402
from lib.supervised import reconcile_supervised_runs  # noqa: E402

_WEB_AUTH_REALM = "hdd-recovery"

# ── request handler ───────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    # Fallback only; main() overrides this from --root / DB_ROOT. RECOVERY_ROOT
    # lets the legacy default be set without code changes.
    root = os.environ.get("RECOVERY_ROOT", "/mnt/recovery16tb/recovery")

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

    def check_auth(self):
        if _webui_auth_ok(self.path, self.headers.get("Authorization")):
            return True
        body = b"Authentication required\n"
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Basic realm="{_WEB_AUTH_REALM}", charset="UTF-8"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return False

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
        if not self.check_auth():
            return
        p = self.path_only()
        db = self.qsval("db")

        try:
            if p == "/":
                self.send_html(page_home(self.root))
            elif p == "/help":
                self.send_html(page_help(self.root))
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
                    script = BIN_DIR / "image-timeline.sh"
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
                                                method_filter=self.qsval("method"),
                                                groups=self.qsval("groups") == "1"))
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
            elif p == "/api/queue":
                # Machine-readable queue progress for monitoring/automation.
                qpid, _ = queue_active(self.root)
                qdir = os.path.realpath(_queue_log_dir(self.root))
                logs = sorted(glob.glob(os.path.join(qdir, "queue-*.log")),
                              key=os.path.getmtime, reverse=True)
                prog = _queue_progress_cached(logs[0]) if logs else None
                out = {"running": bool(qpid), "queue_pid": qpid,
                       "log": logs[0] if logs else None, "progress": prog}
                self.send_bytes_cached(json.dumps(out).encode("utf-8"),
                                       "application/json", "", max_age=0)
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
        if not self.check_auth():
            return
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
        elif p == "/cancel_pipeline":
            if db and os.path.isfile(db):
                cancel_active_pipeline(db)
            self.send_response(302)
            self.send_header(
                "Location",
                f'/db?db={urllib.parse.quote(db)}' if db else "/",
            )
            self.end_headers()
        elif p == "/stop_pipeline_after_stage":
            if db and os.path.isfile(db):
                request_stop_active_pipeline(db)
            self.send_response(302)
            self.send_header(
                "Location",
                f'/db?db={urllib.parse.quote(db)}' if db else "/",
            )
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
            if queue_active(self.root)[0]:
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
        elif p == "/cancel_queue":
            cancel_active_queue(self.root)
            self.send_response(302)
            self.send_header("Location", "/queue_log")
            self.end_headers()
        elif p == "/stop_queue_after_stage":
            request_stop_active_queue(self.root)
            self.send_response(302)
            self.send_header("Location", "/queue_log")
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

    # Startup reconciliation: correct rows left 'running' by a previous
    # kill/crash/restart so the UI doesn't show phantom-active stages. Best-effort.
    try:
        _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _repo not in sys.path:
            sys.path.insert(0, _repo)
        from lib.runs import reconcile_running
        total = 0
        supervised_total = 0
        for db in find_databases(args.root):
            try:
                total += reconcile_running(db)
                supervised_total += reconcile_supervised_runs(db)
            except Exception:
                pass
        if total:
            print(f"Reconciled {total} stale 'running' run(s) at startup.")
        if supervised_total:
            print(f"Reconciled {supervised_total} stale supervised run(s) at startup.")
    except Exception as e:
        print(f"(startup reconcile skipped: {e})")

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
