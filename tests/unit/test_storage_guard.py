"""Unit tests for the storage-misconfiguration guard (lib/storage_guard.py).

Pure logic, offline: device ids and existence are injected so the tests do not
depend on the host's real mount table.
"""
import os
import unittest

from _loader import load_module

sg = load_module("lib/storage_guard.py")

ROOT_DEV = 100  # device id of "/"
MOUNT_DEV = 200  # device id of a real bind mount


class _FakeStat:
    def __init__(self, dev):
        self.st_dev = dev


def make_stat(devmap):
    """Return a stat_fn over a {path: dev} map. Only the exact paths in the map
    'exist'; anything else raises FileNotFoundError, so the guard's walk up to
    the nearest existing ancestor is exercised."""
    def stat_fn(path):
        p = os.path.abspath(path)
        if p in devmap:
            return _FakeStat(devmap[p])
        raise FileNotFoundError(path)
    return stat_fn


class TestCheckRoots(unittest.TestCase):
    def _check(self, roots, in_container=True, devmap=None):
        devmap = devmap or {"/": ROOT_DEV}
        return sg.check_roots(
            roots, in_container=in_container, root_dev=ROOT_DEV,
            stat_fn=make_stat(devmap),
        )

    def test_unmounted_root_falls_to_overlay(self):
        # /data/exports does not exist -> nearest ancestor is "/" -> overlay dev.
        res = {r.name: r for r in self._check({"EXPORT_ROOT": "/data/exports"})}
        self.assertEqual(res["EXPORT_ROOT"].status, "overlay")

    def test_bind_mounted_root_is_ok(self):
        devmap = {"/": ROOT_DEV, "/mnt/recovery16tb/recovery": MOUNT_DEV}
        res = {r.name: r for r in self._check(
            {"EXPORT_ROOT": "/mnt/recovery16tb/recovery/exports"}, devmap=devmap)}
        # exports subdir does not exist yet, but its mounted ancestor is a
        # separate device -> safe.
        self.assertEqual(res["EXPORT_ROOT"].status, "ok")

    def test_outside_container_is_skipped(self):
        res = {r.name: r for r in self._check(
            {"EXPORT_ROOT": "/data/exports"}, in_container=False)}
        self.assertEqual(res["EXPORT_ROOT"].status, "skipped")

    def test_dangerous_roots_filters_overlay_only(self):
        devmap = {"/": ROOT_DEV, "/mnt/big": MOUNT_DEV}
        results = self._check(
            {"DB_ROOT": "/data/db", "EXPORT_ROOT": "/mnt/big/exports"},
            devmap=devmap)
        danger = sg.dangerous_roots(results)
        self.assertEqual([r.name for r in danger], ["DB_ROOT"])

    def test_in_container_env_detection(self):
        self.assertTrue(sg.in_container({"HDD_IN_CONTAINER": "1"}, dockerenv=False))
        self.assertTrue(sg.in_container({}, dockerenv=True))
        self.assertFalse(sg.in_container({}, dockerenv=False))

    def test_override_marks_allowed_but_keeps_status(self):
        # The override does not change detection; enforcement layers consult it.
        self.assertTrue(sg.overlay_allowed({"HDD_ALLOW_OVERLAY": "1"}))
        self.assertFalse(sg.overlay_allowed({}))


if __name__ == "__main__":
    unittest.main()
