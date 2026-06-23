"""Unit tests for the lib/common.sh work-dir helpers (de-dup cycle 1).

Drives the bash functions through a subprocess that sources common.sh, so the
real shell code is exercised (no reimplementation in Python).
"""
import os
import re
import subprocess
import tempfile
import unittest

from _loader import REPO_ROOT

COMMON = REPO_ROOT / "lib" / "common.sh"
_TS = re.compile(r"^\d{8}T\d{6}Z$")


def _run(snippet, root):
    script = (
        "set -Eeuo pipefail\n"
        f'export HDD_RECOVERY_ROOT="{REPO_ROOT}"\n'
        f'source "{COMMON}"\n'
        + snippet
    )
    proc = subprocess.run(
        ["bash", "-c", script], cwd=root,
        capture_output=True, text=True)
    return proc


def _kv(stdout):
    out = {}
    for line in stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


class TestPrepareWorkDirs(unittest.TestCase):
    def test_sets_paths_and_creates_dirs(self):
        with tempfile.TemporaryDirectory() as root:
            export_root = os.path.join(root, "exp")
            proc = _run(
                f'prepare_work_dirs "{export_root}" trid enrich-trid\n'
                'echo "OUT=$out_dir"\n'
                'echo "LOG=$log_path"\n'
                'echo "TS=$timestamp"\n'
                '[ -d "$out_dir" ] && echo "OUTDIR=ok"\n'
                '[ -d "$(dirname "$log_path")" ] && echo "LOGDIR=ok"\n',
                root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            kv = _kv(proc.stdout)
            ts = kv["TS"]
            self.assertRegex(ts, _TS)
            self.assertEqual(kv["OUT"], f"{export_root}/hits/trid/{ts}")
            self.assertEqual(kv["LOG"], f"{export_root}/logs/enrich-trid-{ts}.log")
            self.assertEqual(kv["OUTDIR"], "ok")
            self.assertEqual(kv["LOGDIR"], "ok")

    def test_creates_extra_dirs(self):
        with tempfile.TemporaryDirectory() as root:
            export_root = os.path.join(root, "exp")
            extra = os.path.join(export_root, "state", "hashcat")
            proc = _run(
                f'prepare_work_dirs "{export_root}" crack-wallet crack-wallet "{extra}"\n'
                '[ -d "' + extra + '" ] && echo "EXTRA=ok"\n',
                root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(_kv(proc.stdout).get("EXTRA"), "ok")


class TestRotateWithBackup(unittest.TestCase):
    def test_rotates_existing_file_and_dir(self):
        with tempfile.TemporaryDirectory() as root:
            f = os.path.join(root, "out.log")
            d = os.path.join(root, "outdir")
            proc = _run(
                f'touch "{f}"\n'
                f'mkdir -p "{d}"\n'
                f'rotate_with_backup "{f}"\n'
                f'rotate_with_backup "{d}"\n'
                f'[ -e "{f}" ] || echo "FILE_GONE=ok"\n'
                f'[ -e "{d}" ] || echo "DIR_GONE=ok"\n'
                f'ls -d "{f}".prev-* >/dev/null 2>&1 && echo "FILE_BACKUP=ok"\n'
                f'ls -d "{d}".prev-* >/dev/null 2>&1 && echo "DIR_BACKUP=ok"\n',
                root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            kv = _kv(proc.stdout)
            self.assertEqual(kv.get("FILE_GONE"), "ok")
            self.assertEqual(kv.get("DIR_GONE"), "ok")
            self.assertEqual(kv.get("FILE_BACKUP"), "ok")
            self.assertEqual(kv.get("DIR_BACKUP"), "ok")

    def test_rotates_empty_dir(self):
        # The helper rotates ANY existing path; the "skip empty dirs" policy
        # lives in callers (e.g. image-carve.sh's ls -A guard), not here.
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "empty")
            proc = _run(
                f'mkdir -p "{d}"\n'
                f'rotate_with_backup "{d}"\n'
                f'[ -e "{d}" ] || echo "GONE=ok"\n'
                f'ls -d "{d}".prev-* >/dev/null 2>&1 && echo "BACKUP=ok"\n',
                root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            kv = _kv(proc.stdout)
            self.assertEqual(kv.get("GONE"), "ok")
            self.assertEqual(kv.get("BACKUP"), "ok")

    def test_missing_path_is_noop(self):
        with tempfile.TemporaryDirectory() as root:
            proc = _run(
                f'rotate_with_backup "{root}/nope"\n'
                'echo "RC=done"\n',
                root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(_kv(proc.stdout).get("RC"), "done")


if __name__ == "__main__":
    unittest.main()
