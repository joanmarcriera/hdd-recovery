"""Unit tests for lib/serve_gallery.py (#18 extraction)."""
import os
import sqlite3
import tempfile
import unittest

from _loader import REPO_ROOT, load_module

gallery = load_module("lib/serve_gallery.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


def _make_db(export_root):
    fd, path = tempfile.mkstemp(suffix=".analysis.sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text())
    now = "2026-06-20T00:00:00Z"
    conn.execute(
        "INSERT INTO image_info(id,image_path,image_name,image_basename,"
        "export_root,created_at,updated_at) VALUES (1,?,?,?,?,?,?)",
        ("/x.img", "x.img", "x", export_root, now, now))
    conn.execute(
        "INSERT INTO recovered_artifacts(method,relative_path,full_path,"
        "size_bytes,mime_type,created_at,dedup_cluster_id,is_cluster_primary) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("foremost", "r/a.jpg", os.path.join(export_root, "r", "a.jpg"),
         2048, "image/jpeg", now, 3, 1))
    conn.commit()
    conn.close()
    return path


class TestServeGallery(unittest.TestCase):
    def test_resolve_under_root_blocks_path_escape(self):
        with tempfile.TemporaryDirectory() as root:
            inside = os.path.join(root, "ok.txt")
            open(inside, "w").close()
            abs_path, err = gallery._resolve_under_root(inside, root)
            self.assertEqual(abs_path, os.path.realpath(inside))
            self.assertIsNone(err)

            outside, err = gallery._resolve_under_root(
                os.path.join(root, "..", "escape.txt"), root)
            self.assertIsNone(outside)
            self.assertIn("outside", err)

    def test_page_gallery_groups_query_stays_valid(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "r"))
            with open(os.path.join(root, "r", "a.jpg"), "wb") as fh:
                fh.write(b"not really a jpeg")
            db = _make_db(root)
            self.addCleanup(os.unlink, db)

            html = gallery.page_gallery(db, root, groups=True)
            self.assertIn("Image Gallery", html)
            self.assertIn("duplicates", html)
            self.assertNotIn("no such column", html.lower())

    def test_thumb_cache_dir_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as cache:
            old = os.environ.get("HDD_THUMB_CACHE")
            os.environ["HDD_THUMB_CACHE"] = cache
            try:
                self.assertEqual(gallery._thumb_cache_dir(), cache)
            finally:
                if old is None:
                    os.environ.pop("HDD_THUMB_CACHE", None)
                else:
                    os.environ["HDD_THUMB_CACHE"] = old


if __name__ == "__main__":
    unittest.main()
