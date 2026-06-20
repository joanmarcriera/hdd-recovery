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


_DEV_RE = re.compile(r"^(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|hd[a-z]+|mmcblk\d+)(p?\d+)?$")


def mounted_whole_disks(mounts_text: str) -> set[str]:
    """Whole disks that have a mounted partition per /proc/mounts (real /dev
    sources only — tmpfs/proc/ZFS datasets are ignored)."""
    out: set[str] = set()
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith("/dev/"):
            continue
        name = parts[0].rsplit("/", 1)[-1]
        if name.startswith(("loop", "dm-", "md")):
            continue
        if _DEV_RE.match(name):
            out.add(strip_partition(name))
    return out


def zfs_member_disks(zpool_text: str) -> set[str]:
    """Whole disks used as ZFS vdev members, parsed from `zpool status` output.
    Tokens are filtered to device-looking names so pool names and state words
    (ONLINE/DEGRADED/…) are ignored."""
    out: set[str] = set()
    for tok in zpool_text.split():
        if _DEV_RE.match(tok):
            out.add(strip_partition(tok))
    return out


def blocked_devices(mounts_text: str = "", zpool_text: str = "",
                    dest_dev: str = "") -> set[str]:
    """Whole-disk names that must never be imaged: anything mounted (incl. the
    root filesystem), any ZFS member, and the recovery destination disk.
    Derived at runtime instead of a hardcoded {sda,sdb,sdc} (#13). Pure —
    inputs are passed in so it is unit-testable offline."""
    blocked = mounted_whole_disks(mounts_text) | zfs_member_disks(zpool_text)
    if dest_dev:
        blocked.add(strip_partition(dest_dev))
    return blocked


def detect_blocked_devices(default_dest: str = "sdc") -> set[str]:
    """Runtime wrapper: read /proc/mounts and `zpool status`, resolve the
    destination disk, and return the blocked set. Never raises — missing inputs
    (no ZFS, no /proc/mounts) just contribute nothing."""
    import subprocess
    try:
        mounts_text = Path("/proc/mounts").read_text()
    except OSError:
        mounts_text = ""
    zpool_text = ""
    try:
        zpool_text = subprocess.run(
            ["zpool", "status"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pass
    dest = resolve_dest_dev(default_dest, mounts_text or None)
    return blocked_devices(mounts_text, zpool_text, dest)


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
