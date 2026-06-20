"""Dispatch smoke tests for lib/serve_app.py (#18 Handler extraction)."""
import os
import io
import sqlite3
import tempfile
import unittest
import urllib.parse

from _loader import REPO_ROOT, load_module

app = load_module("lib/serve_app.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


def _make_db(root):
    db = os.path.join(root, "x.img.analysis.sqlite")
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text())
    now = "2026-06-20T00:00:00Z"
    conn.execute(
        "INSERT INTO image_info(id,image_path,image_name,image_basename,"
        "export_root,created_at,updated_at) VALUES (1,?,?,?,?,?,?)",
        (os.path.join(root, "x.img"), "x.img", "x", root, now, now))
    conn.commit()
    conn.close()
    return db


class CaptureHandler(app.Handler):
    """Minimal Handler test double: dispatches routes without opening a socket."""

    def __init__(self, path, root):
        self.path = path
        self.root = root
        self.command = "GET"
        self.headers = {}
        self.captured = None
        self.close_connection = False

    def check_auth(self):
        return True

    def send_html(self, content, status=200):
        self.captured = ("html", status, content)

    def send_bytes_cached(self, data, mime, etag, max_age=86400, extra_headers=None):
        self.captured = ("bytes", 200, mime, data)

    def send_response(self, status):
        self.captured = ("response", status)

    def send_header(self, key, value):
        if self.captured and self.captured[0] == "response":
            headers = self.captured[2] if len(self.captured) > 2 else {}
            headers[key] = value
            self.captured = ("response", self.captured[1], headers)

    def end_headers(self):
        pass


class CapturePostHandler(CaptureHandler):
    def __init__(self, path, root, body=""):
        super().__init__(path, root)
        self.command = "POST"
        self.body = body.encode("utf-8")
        self.rfile = io.BytesIO(self.body)
        self.headers = {"Content-Length": str(len(self.body))}


class TestServeAppDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = _make_db(self.tmp.name)

    def dispatch(self, path):
        h = CaptureHandler(path, self.tmp.name)
        h.do_GET()
        return h.captured

    def test_home_and_db_routes_render(self):
        kind, status, body = self.dispatch("/")
        self.assertEqual((kind, status), ("html", 200))
        self.assertIn("Recovery Dashboard", body)
        self.assertIn("x.img", body)

        db_url = "/db?db=" + urllib.parse.quote(self.db)
        kind, status, detail = self.dispatch(db_url)
        self.assertEqual((kind, status), ("html", 200))
        self.assertIn("Image Info", detail)
        self.assertIn("Run Pipeline", detail)

    def test_api_queue_returns_json_payload(self):
        kind, status, mime, data = self.dispatch("/api/queue")
        self.assertEqual((kind, status, mime), ("bytes", 200, "application/json"))
        self.assertIn(b'"running": false', data)

    def test_stop_queue_after_stage_redirects_to_queue_log(self):
        h = CapturePostHandler("/stop_queue_after_stage", self.tmp.name)
        h.do_POST()
        self.assertEqual(h.captured[0:2], ("response", 302))
        self.assertEqual(h.captured[2]["Location"], "/queue_log")

    def test_stop_pipeline_after_stage_redirects_home_without_db(self):
        h = CapturePostHandler("/stop_pipeline_after_stage", self.tmp.name)
        h.do_POST()
        self.assertEqual(h.captured[0:2], ("response", 302))
        self.assertEqual(h.captured[2]["Location"], "/")


if __name__ == "__main__":
    unittest.main()
