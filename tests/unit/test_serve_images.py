"""Unit tests for web UI image discovery/registration helpers."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _loader import load_module

imgmod = load_module("lib/serve_images.py")


def _write_db(path, image_path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE image_info(id INTEGER PRIMARY KEY, image_path TEXT)")
    conn.execute("INSERT INTO image_info(id, image_path) VALUES (1, ?)", (image_path,))
    conn.commit()
    conn.close()


class TestServeImages(unittest.TestCase):
    def _env(self, root, **extra):
        env = {
            "IMAGE_ROOT": os.path.join(root, "images"),
            "RECOVERY_ROOT": root,
        }
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_discover_legacy_beside_images_and_map(self):
        with tempfile.TemporaryDirectory() as root, self._env(root):
            images = os.path.join(root, "images")
            logs = os.path.join(root, "logs")
            os.makedirs(images)
            os.makedirs(logs)
            new_img = os.path.join(images, "new.img")
            old_img = os.path.join(images, "old.img")
            Path(new_img).write_bytes(b"new")
            Path(old_img).write_bytes(b"old")
            Path(os.path.join(logs, "new.map")).write_text("map\n")
            old_db = old_img + ".analysis.sqlite"
            _write_db(old_db, old_img)

            found = {os.path.basename(c.image_path): c
                     for c in imgmod.discover_images(root)
                     if c.image_path.startswith(images)}

            self.assertFalse(found["new.img"].registered)
            self.assertEqual(found["new.img"].db_path, new_img + ".analysis.sqlite")
            self.assertEqual(found["new.img"].map_path, os.path.join(logs, "new.map"))
            self.assertTrue(found["old.img"].registered)
            self.assertEqual(found["old.img"].registered_db, old_db)

    def test_discover_split_db_layout_uses_web_db_root(self):
        with tempfile.TemporaryDirectory() as root, self._env(root):
            images = os.path.join(root, "images")
            db_root = os.path.join(root, "db")
            os.makedirs(images)
            os.makedirs(db_root)
            image = os.path.join(images, "disk.img")
            Path(image).write_bytes(b"data")

            found = [c for c in imgmod.discover_images(db_root)
                     if c.image_path == image]

            self.assertEqual(len(found), 1)
            self.assertEqual(
                found[0].db_path,
                os.path.join(db_root, "disk.img.analysis.sqlite"),
            )

    def test_initialize_invokes_init_script_with_explicit_paths(self):
        candidate = imgmod.ImageCandidate(
            image_path="/data/images/disk.img",
            size_bytes=4,
            map_path="/data/logs/disk.map",
            db_path="/data/db/disk.img.analysis.sqlite",
            registered=False,
            registered_db="",
            source_root="/data/images",
        )
        completed = mock.Mock(returncode=0, stdout="/data/db/disk.img.analysis.sqlite\n", stderr="")
        with mock.patch.object(imgmod.subprocess, "run", return_value=completed) as run:
            db = imgmod.initialize_image_catalog(candidate)

        self.assertEqual(db, "/data/db/disk.img.analysis.sqlite")
        cmd = run.call_args.args[0]
        self.assertIn("--db", cmd)
        self.assertIn("/data/db/disk.img.analysis.sqlite", cmd)
        self.assertIn("--map", cmd)
        self.assertIn("/data/logs/disk.map", cmd)
        self.assertIn("--print-db-path", cmd)


if __name__ == "__main__":
    unittest.main()
