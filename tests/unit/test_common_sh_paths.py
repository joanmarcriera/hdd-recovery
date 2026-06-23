"""Characterization tests for lib/common.sh path-derivation helpers.

These lock the CURRENT behavior of image_name / image_basename /
default_db_path / default_export_root (incl. DB_ROOT / EXPORT_ROOT / DB_SUFFIX
env precedence) so the deferred bash-retrofit / portability work cannot change
it silently. Driven through a bash subprocess that sources common.sh.
"""
import subprocess
import unittest

from _loader import REPO_ROOT

COMMON = REPO_ROOT / "lib" / "common.sh"


def _run(pre_source, body):
    """Run a bash snippet. `pre_source` lines run BEFORE sourcing common.sh
    (so they can set env vars that the config's :=defaults then respect)."""
    script = (
        "set -Eeuo pipefail\n"
        f'export HDD_RECOVERY_ROOT="{REPO_ROOT}"\n'
        + pre_source
        + f'source "{COMMON}"\n'
        + body
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _out(pre_source, expr):
    proc = _run(pre_source, f'printf "%s" "{expr}"\n')
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class TestNameHelpers(unittest.TestCase):
    def test_image_name(self):
        self.assertEqual(_out("", '$(image_name /mnt/x/foo.img)'), "foo.img")

    def test_image_basename_strips_last_ext(self):
        self.assertEqual(_out("", '$(image_basename /mnt/x/foo.img)'), "foo")

    def test_image_basename_only_last_ext(self):
        self.assertEqual(_out("", '$(image_basename /a/foo.tar.gz)'), "foo.tar")

    def test_image_basename_no_extension(self):
        self.assertEqual(_out("", '$(image_basename /mnt/x/diskimage)'), "diskimage")

    def test_image_basename_trailing_dot(self):
        self.assertEqual(_out("", '$(image_basename /mnt/x/foo.)'), "foo")


class TestDefaultDbPath(unittest.TestCase):
    def test_uses_db_root_when_set_from_config(self):
        # config default DB_ROOT=/data/db, DB_SUFFIX=.analysis.sqlite
        self.assertEqual(
            _out("", '$(default_db_path /imgs/foo.img)'),
            "/data/db/foo.img.analysis.sqlite")

    def test_honours_db_root_override(self):
        self.assertEqual(
            _out('export DB_ROOT=/nvme/db\n', '$(default_db_path /imgs/foo.img)'),
            "/nvme/db/foo.img.analysis.sqlite")

    def test_falls_back_to_image_path_when_db_root_unset(self):
        # Unset AFTER sourcing (config would otherwise set it).
        proc = _run("", 'unset DB_ROOT\nprintf "%s" "$(default_db_path /imgs/foo.img)"\n')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "/imgs/foo.img.analysis.sqlite")

    def test_honours_db_suffix_override(self):
        self.assertEqual(
            _out('export DB_SUFFIX=.sqlite\nexport DB_ROOT=/db\n',
                 '$(default_db_path /imgs/foo.img)'),
            "/db/foo.img.sqlite")


class TestDefaultExportRoot(unittest.TestCase):
    def test_uses_export_root_from_config(self):
        self.assertEqual(
            _out("", '$(default_export_root /imgs/foo.img)'),
            "/data/exports/foo")

    def test_honours_export_root_override(self):
        self.assertEqual(
            _out('export EXPORT_ROOT=/big/exports\n',
                 '$(default_export_root /imgs/foo.img)'),
            "/big/exports/foo")


if __name__ == "__main__":
    unittest.main()
