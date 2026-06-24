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

    def test_db_path_prefers_existing_beside_db_over_db_root_stub(self):
        # Regression: a beside-image DB already holds the analysis. Even with
        # DB_ROOT set (Docker split layout) and the search set not yet aware of
        # it, discovery must reuse the beside-image DB, never assign a fresh
        # DB_ROOT path that a later init turns into an empty "no progress" stub.
        with tempfile.TemporaryDirectory() as root:
            images = Path(root) / "images"
            images.mkdir()
            image = images / "disk.img"
            image.touch()
            beside = str(image) + ".analysis.sqlite"
            _write_db(beside, str(image))
            db_root = Path(root) / "db"
            db_root.mkdir()
            with mock.patch.dict(os.environ, {"DB_ROOT": str(db_root)}, clear=True):
                # Empty db_paths simulates the beside DB being outside the search set.
                chosen = imgmod._db_path_for_image(image, root, [])
            self.assertEqual(chosen, beside)

    def test_discover_marks_beside_db_image_registered_with_db_root_set(self):
        # End-to-end: an image with a populated beside-image DB must come back
        # registered (pointing at that DB), so the UI won't offer to re-init it
        # into a DB_ROOT stub.
        with tempfile.TemporaryDirectory() as root, self._env(root, DB_ROOT=os.path.join(root, "db")):
            images = os.path.join(root, "images")
            os.makedirs(images)
            os.makedirs(os.path.join(root, "db"))
            image = os.path.join(images, "disk.img")
            Path(image).write_bytes(b"data")
            _write_db(image + ".analysis.sqlite", image)

            found = [c for c in imgmod.discover_images(root) if c.image_path == image]
            self.assertEqual(len(found), 1)
            self.assertTrue(found[0].registered)
            self.assertEqual(found[0].db_path, image + ".analysis.sqlite")

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
