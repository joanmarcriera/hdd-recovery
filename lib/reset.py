"""Per-image full reset: delete an image's analysis DB and export tree.

Wipes ``<image>.analysis.sqlite`` (plus ``-wal``/``-shm`` sidecars) and the
entire ``exports/<image>/`` tree so an image can be re-analysed from scratch.

**Never** touches the raw ``.img`` or the ddrescue ``.map`` — re-imaging is
expensive/irreversible. The plan resolver asserts no delete target equals or
contains the image or map path, refuses ``/`` and the EXPORT_ROOT base, and
(when EXPORT_ROOT is set) requires the export tree to live under it.

Pure-ish: ``resolve_plan`` only reads the DB read-only; ``perform_reset`` takes
injectable ``is_active`` / ``audit_log`` so it is unit-testable offline.
"""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


class ResetError(Exception):
    """A reset was refused (unsafe target, missing data, or active writer)."""


@dataclass(frozen=True)
class ResetPlan:
    db_path: str
    image_path: str
    map_path: str
    export_root: str
    delete_targets: list[str]   # absolute paths to remove
    total_bytes: int


@dataclass(frozen=True)
class ResetResult:
    deleted: list[str]
    freed_bytes: int
    audit_log: str


def _abspath(p):
    return os.path.abspath(p) if p else ""


def _is_within(child, parent):
    """True when child is parent or a descendant of parent."""
    child, parent = _abspath(child), _abspath(parent)
    if not child or not parent:
        return False
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _read_image_info(db_path):
    if not os.path.isfile(db_path):
        raise ResetError(f"database not found: {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM image_info WHERE id=1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ResetError(f"cannot read image_info from {db_path}: {exc}")
    if row is None:
        raise ResetError(f"image_info row missing in {db_path}")
    return dict(row)


def _dir_size(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                total += os.lstat(fp).st_size
            except OSError:
                pass
    return total


def resolve_plan(db_path, env=None) -> ResetPlan:
    env = os.environ if env is None else env
    db_path = _abspath(db_path)
    info = _read_image_info(db_path)

    image_path = _abspath(info.get("image_path") or "")
    map_path = _abspath(info.get("ddrescue_map_path") or "")
    export_root = _abspath(info.get("export_root") or "")

    image_root = _abspath(env.get("IMAGE_ROOT") or "")
    log_root = _abspath(env.get("LOG_ROOT") or "")
    export_base = _abspath(env.get("EXPORT_ROOT") or "")

    # ---- guards on the export tree --------------------------------------
    if not export_root:
        raise ResetError("image_info.export_root is empty — refusing to guess")
    if export_root == os.sep:
        raise ResetError("export_root resolves to '/' — refusing")
    for protected, label in ((export_base, "EXPORT_ROOT"),
                             (image_root, "IMAGE_ROOT"),
                             (log_root, "LOG_ROOT")):
        if protected and export_root == protected:
            raise ResetError(
                f"export_root equals {label} ({protected}) — refusing to wipe "
                f"a shared root")
    if export_base and not _is_within(export_root, export_base):
        raise ResetError(
            f"export_root {export_root} is not under EXPORT_ROOT {export_base} "
            f"— refusing")
    # The export tree must never cover the raw image or the ddrescue map.
    for protected, label in ((image_path, "the raw image"),
                             (map_path, "the ddrescue map")):
        if protected and _is_within(protected, export_root):
            raise ResetError(
                f"export_root {export_root} contains {label} ({protected}) — "
                f"refusing")

    # ---- delete targets --------------------------------------------------
    targets: list[str] = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        cand = db_path + suffix
        if os.path.isfile(cand):
            targets.append(cand)
    total = sum(os.path.getsize(t) for t in targets if os.path.isfile(t))
    if os.path.isdir(export_root):
        targets.append(export_root)
        total += _dir_size(export_root)

    # Final defensive assertion: image/map must not be among the targets.
    for t in targets:
        if _is_within(image_path, t) or _is_within(map_path, t):
            raise ResetError(
                f"refusing — delete target {t} would remove the image or map")

    return ResetPlan(db_path, image_path, map_path, export_root, targets, total)


def default_is_active(db_path) -> bool:
    """True when a pipeline or queue is currently writing to this DB.

    Imported lazily so unit tests that inject ``is_active`` never touch the
    supervised-run machinery.
    """
    try:
        from lib.supervised import active_supervised_runs
        for kind in ("pipeline", "queue"):
            if active_supervised_runs(db_path, run_kind=kind):
                return True
    except Exception:
        pass
    # Fallback: a live image-pipeline/image-queue process naming this DB.
    try:
        import subprocess
        out = subprocess.run(
            ["pgrep", "-af", "image-(pipeline|queue).py"],
            capture_output=True, text=True, timeout=5)
        if db_path in out.stdout:
            return True
    except Exception:
        pass
    return False


def _audit_log_path(env, plan):
    log_root = env.get("LOG_ROOT") or os.path.dirname(plan.export_root)
    return os.path.join(log_root, "resets.log")


def perform_reset(db_path, *, env=None, is_active=default_is_active,
                  audit_log=None, now=None) -> ResetResult:
    env = os.environ if env is None else env
    plan = resolve_plan(db_path, env=env)

    if is_active(plan.db_path):
        raise ResetError(
            "a pipeline or queue is active for this image — stop it before "
            "resetting")

    audit_log = audit_log or _audit_log_path(env, plan)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (f"[{stamp}] host={socket.gethostname()} image={plan.image_path} "
            f"db={plan.db_path} export_root={plan.export_root} "
            f"freed_bytes={plan.total_bytes} "
            f"targets={','.join(plan.delete_targets)}\n")
    try:
        os.makedirs(os.path.dirname(audit_log), exist_ok=True)
        with open(audit_log, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        # Auditing is best-effort; never block a reset on an unwritable log.
        pass

    for target in plan.delete_targets:
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target, ignore_errors=False)
        elif os.path.exists(target) or os.path.islink(target):
            os.remove(target)

    return ResetResult(list(plan.delete_targets), plan.total_bytes, audit_log)
