"""Regression tests for the parameterized SQL in the picture/wallet/file pages.
Builds a real schema-initialized SQLite DB (from sql/analysis-schema.sql),
populates a few rows, and asserts the page queries execute and honor LIMIT.
Protects against breakage when the f-string LIMIT was switched to bound params."""
import os
import sqlite3
import tempfile
import unittest

from _loader import REPO_ROOT, load_module

srv = load_module("bin/image-serve.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".analysis.sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text())
    now = "2026-06-20T00:00:00Z"
    conn.execute(
        "INSERT INTO image_info(id,image_path,image_name,image_basename,"
        "export_root,created_at,updated_at) VALUES (1,?,?,?,?,?,?)",
        ("/x.img", "x.img", "x", "/tmp/exp", now, now))
    # 3 files, 3 wallet + 3 picture candidates, 3 image artifacts
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO files(id,source_tool,path,name,size_bytes,mime_type) "
            "VALUES (?,?,?,?,?,?)",
            (i, "tsk", f"/p/f{i}.jpg", f"f{i}.jpg", 1000 + i, "image/jpeg"))
        conn.execute(
            "INSERT INTO wallet_candidates(file_id,source_stage,score,reason,created_at)"
            " VALUES (?,?,?,?,?)", (i, "detect", 90 - i, f"reason{i}", now))
        conn.execute(
            "INSERT INTO picture_candidates(file_id,source_stage,score,reason,created_at)"
            " VALUES (?,?,?,?,?)", (i, "detect", 80 - i, f"pic{i}", now))
        conn.execute(
            "INSERT INTO recovered_artifacts(method,relative_path,full_path,"
            "size_bytes,mime_type,created_at) VALUES (?,?,?,?,?,?)",
            ("foremost", f"r/f{i}.jpg", f"/tmp/exp/r/f{i}.jpg", 2000 + i,
             "image/jpeg", now))
    conn.commit()
    conn.close()
    return path


class TestParameterizedPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.db)

    def _assert_no_sql_error(self, html):
        for needle in ("no such column", "no such table", "syntax error",
                       "incomplete input"):
            self.assertNotIn(needle, html.lower())

    def test_wallets_limit_respected(self):
        html = srv.page_wallets(self.db, limit=2)
        self._assert_no_sql_error(html)
        self.assertIn("2 row(s)", html)  # 3 inserted, capped at 2

    def test_files_search_limit(self):
        html = srv.page_files(self.db, pattern="%", limit=2)
        self._assert_no_sql_error(html)
        self.assertIn("2 row(s)", html)

    def test_pictures_both_sections_limit(self):
        html = srv.page_pictures(self.db, limit=2)
        self._assert_no_sql_error(html)
        # candidates section + carved-artifacts section, each capped at 2
        self.assertGreaterEqual(html.count("2 row(s)"), 2)


class TestBadge(unittest.TestCase):
    def test_known_statuses(self):
        for status, cls in [("ok", "ok"), ("failed", "failed"),
                            ("running", "running"), ("partial", "partial")]:
            self.assertIn(f'badge {cls}', srv.badge(status))

    def test_reconciled_statuses_not_pending(self):
        # interrupted/timeout must render distinctly, not as "pending"
        self.assertIn("badge partial", srv.badge("interrupted"))
        self.assertIn("badge failed", srv.badge("timeout"))

    def test_unknown_falls_back_to_pending(self):
        self.assertIn("badge pending", srv.badge("weird"))


if __name__ == "__main__":
    unittest.main()
