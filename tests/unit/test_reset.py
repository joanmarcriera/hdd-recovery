"""Unit tests for per-image full reset (lib/reset.py).

Offline: builds a temp analysis DB + a fake export tree and verifies the plan
deletes only the DB sidecars and the export tree, never the raw image or
ddrescue map, refuses while a writer is active, and writes an audit record.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from _loader import REPO_ROOT, load_module

reset = load_module("lib/reset.py")
SCHEMA = REPO_ROOT / "sql" / "analysis-schema.sql"


class ResetCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reset_test_")
        self.addCleanup(self._rmtree, self.tmp)
        self.image_root = os.path.join(self.tmp, "images")
        self.export_root_base = os.path.join(self.tmp, "exports")
        self.log_root = os.path.join(self.tmp, "logs")
        for d in (self.image_root, self.export_root_base, self.log_root):
            os.makedirs(d)
        self.image = os.path.join(self.image_root, "disk.img")
        Path(self.image).write_bytes(b"RAWIMAGE")
        self.mapfile = os.path.join(self.log_root, "disk.map")
        Path(self.mapfile).write_text("0x0 + status\n")
        self.export_root = os.path.join(self.export_root_base, "disk")
        os.makedirs(os.path.join(self.export_root, "recovered"))
        Path(os.path.join(self.export_root, "recovered", "carved01.jpg")
             ).write_bytes(b"x" * 4096)
        self.db = os.path.join(self.image_root, "disk.img.analysis.sqlite")
        self._make_db(self.db)
        # -wal sidecar present to confirm it is removed too.
        Path(self.db + "-wal").write_bytes(b"wal")
        self.env = {
            "IMAGE_ROOT": self.image_root,
            "EXPORT_ROOT": self.export_root_base,
            "LOG_ROOT": self.log_root,
        }

    def _rmtree(self, path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def _make_db(self, path, export_root=None):
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA.read_text())
        conn.execute(
            "INSERT INTO image_info(id,image_path,image_name,image_basename,"
            "ddrescue_map_path,export_root,created_at,updated_at) "
            "VALUES(1,?,?,?,?,?,?,?)",
            (self.image, "disk.img", "disk", self.mapfile,
             export_root if export_root is not None else self.export_root,
             "now", "now"),
        )
        conn.commit()
        conn.close()

    # ---- plan ------------------------------------------------------------
    def test_plan_targets_db_and_export_tree(self):
        plan = reset.resolve_plan(self.db, env=self.env)
        self.assertEqual(plan.export_root, self.export_root)
        self.assertIn(self.db, plan.delete_targets)
        self.assertIn(self.db + "-wal", plan.delete_targets)
        self.assertIn(self.export_root, plan.delete_targets)
        self.assertGreater(plan.total_bytes, 0)

    def test_plan_never_targets_image_or_map(self):
        plan = reset.resolve_plan(self.db, env=self.env)
        for target in plan.delete_targets:
            self.assertNotEqual(os.path.abspath(target), os.path.abspath(self.image))
            self.assertNotEqual(os.path.abspath(target), os.path.abspath(self.mapfile))
            # and no target is an ancestor of the image/map
            self.assertFalse(os.path.abspath(self.image).startswith(
                os.path.abspath(target) + os.sep))
            self.assertFalse(os.path.abspath(self.mapfile).startswith(
                os.path.abspath(target) + os.sep))

    def test_guard_rejects_root_export(self):
        bad = os.path.join(self.image_root, "disk2.img.analysis.sqlite")
        self._make_db(bad, export_root="/")
        with self.assertRaises(reset.ResetError):
            reset.resolve_plan(bad, env=self.env)

    def test_guard_rejects_export_root_base_itself(self):
        bad = os.path.join(self.image_root, "disk3.img.analysis.sqlite")
        self._make_db(bad, export_root=self.export_root_base)
        with self.assertRaises(reset.ResetError):
            reset.resolve_plan(bad, env=self.env)

    def test_guard_rejects_export_covering_image(self):
        # export_root set to IMAGE_ROOT would delete the raw image -> reject.
        bad = os.path.join(self.image_root, "disk4.img.analysis.sqlite")
        self._make_db(bad, export_root=self.image_root)
        with self.assertRaises(reset.ResetError):
            reset.resolve_plan(bad, env=self.env)

    # ---- perform ---------------------------------------------------------
    def test_perform_refuses_when_active(self):
        with self.assertRaises(reset.ResetError):
            reset.perform_reset(self.db, env=self.env,
                                is_active=lambda *_: True)

    def test_perform_deletes_db_and_exports_keeps_image(self):
        audit = os.path.join(self.log_root, "resets.log")
        result = reset.perform_reset(
            self.db, env=self.env, is_active=lambda *_: False,
            audit_log=audit)
        self.assertFalse(os.path.exists(self.db))
        self.assertFalse(os.path.exists(self.db + "-wal"))
        self.assertFalse(os.path.exists(self.export_root))
        # raw image + map survive
        self.assertTrue(os.path.exists(self.image))
        self.assertTrue(os.path.exists(self.mapfile))
        self.assertGreater(result.freed_bytes, 0)
        # audit trail written
        self.assertTrue(os.path.exists(audit))
        self.assertIn("disk.img", Path(audit).read_text())


if __name__ == "__main__":
    unittest.main()
