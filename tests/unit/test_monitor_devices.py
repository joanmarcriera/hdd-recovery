"""F8 — derive the monitored destination disk instead of hardcoding 'sdc'.
Tests the pure helpers in tui/devices.py (no textual import needed)."""
import os
import unittest

from _loader import load_module

dev = load_module("tui/devices.py")

MOUNTS = "\n".join([
    "proc /proc proc rw 0 0",
    "/dev/sda2 / ext4 rw 0 0",
    "/dev/sdc1 /mnt/recovery16tb ext4 rw 0 0",
    "/dev/nvme0n1p1 /data/db ext4 rw 0 0",
    "BigDisk/exports /data/exports zfs rw 0 0",   # ZFS: no /dev source
]) + "\n"


class TestStripPartition(unittest.TestCase):
    def test_cases(self):
        cases = {
            "sdc1": "sdc", "sda": "sda", "sdab12": "sdab",
            "nvme0n1p2": "nvme0n1", "nvme0n1": "nvme0n1",
            "mmcblk0p1": "mmcblk0", "loop0": "loop0", "dm-3": "dm-3",
        }
        for name, want in cases.items():
            self.assertEqual(dev.strip_partition(name), want, name)


class TestBackingDevice(unittest.TestCase):
    def test_resolves_whole_disk(self):
        self.assertEqual(dev.backing_device("/mnt/recovery16tb", MOUNTS), "sdc")
        self.assertEqual(dev.backing_device("/data/db", MOUNTS), "nvme0n1")

    def test_longest_prefix_wins(self):
        # a nested path under recovery16tb still maps to sdc, not root sda
        self.assertEqual(
            dev.backing_device("/mnt/recovery16tb/images/x.img", MOUNTS), "sdc")

    def test_zfs_dataset_has_no_dev(self):
        self.assertEqual(dev.backing_device("/data/exports", MOUNTS), "")


class TestResolveDestDev(unittest.TestCase):
    def _clear_env(self):
        for k in ("MONITOR_DEST_DEV", "IMAGE_ROOT", "EXPORT_ROOT"):
            old = os.environ.pop(k, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, k, old)

    def test_env_override_wins(self):
        self._clear_env()
        os.environ["MONITOR_DEST_DEV"] = "sdx"
        self.addCleanup(os.environ.pop, "MONITOR_DEST_DEV", None)
        self.assertEqual(dev.resolve_dest_dev(mounts_text=MOUNTS), "sdx")

    def test_derives_from_mounts(self):
        self._clear_env()
        self.assertEqual(
            dev.resolve_dest_dev(mounts_text=MOUNTS,
                                 candidates=["/mnt/recovery16tb"]), "sdc")

    def test_fallback_default_on_zfs_only(self):
        # candidate lives on a ZFS dataset (no /dev) → use the default
        self._clear_env()
        self.assertEqual(
            dev.resolve_dest_dev(default="sdc", mounts_text=MOUNTS,
                                 candidates=["/data/exports"]), "sdc")


if __name__ == "__main__":
    unittest.main()
