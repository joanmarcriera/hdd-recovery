"""Derive which block device backs the recovery destination, instead of
hardcoding it. Pure stdlib (no textual import) so it stays unit-testable.

Resolution order: $MONITOR_DEST_DEV → the device backing the image/export root
in /proc/mounts (partition digits stripped to the whole disk) → a default.
On ZFS datasets (no /dev source in /proc/mounts) derivation yields nothing and
the default/env value is used.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def strip_partition(name: str) -> str:
    """sdc1->sdc, nvme0n1p2->nvme0n1, mmcblk0p1->mmcblk0; loop/dm/md and
    whole-disk nvme/mmc names unchanged."""
    if name.startswith(("loop", "dm-", "md")):
        return name
    if re.match(r"^(nvme\d+n\d+|mmcblk\d+)$", name):       # already a whole disk
        return name
    m = re.match(r"^(nvme\d+n\d+|mmcblk\d+)p\d+$", name)
    if m:
        return m.group(1)
    return re.sub(r"\d+$", "", name)


def backing_device(path: str, mounts_text: str) -> str:
    """Return the whole-disk name backing `path` per /proc/mounts content. Picks
    the longest-matching mountpoint, then returns '' unless that mount has a
    /dev source (so a ZFS dataset resolves to '' rather than the root disk)."""
    best_mp, best_dev = "", None
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        dev, mp = parts[0], parts[1].replace("\\040", " ")
        norm = mp.rstrip("/") or "/"
        matched = norm == "/" or path == norm or path.startswith(norm + "/")
        if matched and len(norm) >= len(best_mp):
            best_mp, best_dev = norm, dev
    if not best_dev or not best_dev.startswith("/dev/"):
        return ""
    return strip_partition(best_dev.rsplit("/", 1)[-1])


def resolve_dest_dev(default: str = "sdc", mounts_text: str | None = None,
                     candidates: list[str] | None = None) -> str:
    env = os.environ.get("MONITOR_DEST_DEV")
    if env:
        return env
    if mounts_text is None:
        try:
            mounts_text = Path("/proc/mounts").read_text()
        except OSError:
            return default
    if candidates is None:
        candidates = [os.environ.get("IMAGE_ROOT", "/data/images"),
                      os.environ.get("EXPORT_ROOT", "/data/exports"),
                      "/mnt/recovery16tb"]
    for path in candidates:
        dev = backing_device(path, mounts_text)
        if dev:
            return dev
    return default
