"""Image discovery and registration helpers for the review UI.

The dashboard mostly reads SQLite, but registering a finished image is a small
operator action: discover raw .img files, infer the matching mapfile, and call
image-analysis-init.sh with an explicit DB path.  Keep that write-capable logic
separate from lib.serve_db's read-only query helpers.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lib.serve_db import find_databases

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT / "bin"
DB_SUFFIX = os.environ.get("DB_SUFFIX", ".analysis.sqlite")


@dataclass(frozen=True)
class ImageCandidate:
    image_path: str
    size_bytes: int
    map_path: str
    db_path: str
    registered: bool
    registered_db: str
    source_root: str


def _real(path: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.fspath(path))


def _unique_existing(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if not path:
            continue
        key = _real(path)
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        out.append(path)
    return out


def image_roots(web_root: str) -> list[str]:
    """Candidate directories containing raw .img files.

    Supports both the legacy layout (<recovery>/images with DBs beside images)
    and the Docker split layout (/data/images plus /data/db).
    """
    root = Path(web_root).expanduser()
    env_image = os.environ.get("IMAGE_ROOT")
    recovery = Path(os.environ.get("RECOVERY_ROOT", "/mnt/recovery16tb/recovery"))
    candidates = [
        Path(env_image).expanduser() if env_image else None,
        root / "images",
        root.parent / "images",
        recovery / "images",
        Path("/data/images"),
        root,
    ]
    return [str(p) for p in _unique_existing([p for p in candidates if p is not None])]


def _db_search_roots(web_root: str) -> list[str]:
    root = Path(web_root).expanduser()
    env_db = os.environ.get("DB_ROOT")
    candidates = [
        Path(env_db).expanduser() if env_db else None,
        root,
        root.parent / "db",
        Path("/data/db"),
    ]
    return [str(p) for p in _unique_existing([p for p in candidates if p is not None])]


def _known_databases(web_root: str) -> tuple[dict[str, str], set[str], list[str]]:
    """Return (image_realpath -> db_path, db_realpaths, db_paths)."""
    by_image: dict[str, str] = {}
    db_realpaths: set[str] = set()
    db_paths: list[str] = []
    for root in _db_search_roots(web_root):
        for db in find_databases(root):
            db_paths.append(db)
            db_realpaths.add(_real(db))
            try:
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    row = conn.execute(
                        "SELECT image_path FROM image_info WHERE id=1"
                    ).fetchone()
                finally:
                    conn.close()
            except Exception:
                row = None
            if row and row[0]:
                by_image[_real(row[0])] = db
    return by_image, db_realpaths, db_paths


def _db_path_for_image(image_path: Path, web_root: str, db_paths: list[str]) -> str:
    """Choose a DB path that matches the visible UI layout.

    If existing DBs are beside images, keep using that.  If the UI root is a DB
    directory (e.g. /data/db), create the DB there.  The init script receives
    this path explicitly, so shell config defaults cannot accidentally move it.
    """
    suffix = os.environ.get("DB_SUFFIX", DB_SUFFIX)
    image_dir = _real(image_path.parent)
    if any(_real(Path(db).parent) == image_dir for db in db_paths):
        return str(image_path) + suffix

    env_db = os.environ.get("DB_ROOT")
    if env_db:
        return str(Path(env_db).expanduser() / f"{image_path.name}{suffix}")

    root = Path(web_root).expanduser()
    if root.name.lower() == "db":
        return str(root / f"{image_path.name}{suffix}")
    if any(_real(Path(db).parent) == _real(root) for db in db_paths):
        return str(root / f"{image_path.name}{suffix}")

    return str(image_path) + suffix


def _map_roots(web_root: str, image_path: Path) -> list[Path]:
    root = Path(web_root).expanduser()
    env_log = os.environ.get("LOG_ROOT")
    candidates = [
        Path(env_log).expanduser() if env_log else None,
        image_path.parent.parent / "logs",
        root / "logs",
        root.parent / "logs",
        Path(os.environ.get("RECOVERY_ROOT", "/mnt/recovery16tb/recovery")) / "logs",
        Path("/data/logs"),
        image_path.parent,
    ]
    return _unique_existing([p for p in candidates if p is not None])


def find_matching_map(image_path: str, web_root: str) -> str:
    image = Path(image_path)
    stem = image.stem
    names = [f"{stem}.map", stem]
    for root in _map_roots(web_root, image):
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return str(candidate)
    return ""


def discover_images(web_root: str) -> list[ImageCandidate]:
    by_image, db_realpaths, db_paths = _known_databases(web_root)
    found: list[ImageCandidate] = []
    seen: set[str] = set()
    for root in image_roots(web_root):
        for image in sorted(Path(root).glob("*.img")):
            if not image.is_file():
                continue
            image_real = _real(image)
            if image_real in seen:
                continue
            seen.add(image_real)
            registered_db = by_image.get(image_real, "")
            db_path = registered_db or _db_path_for_image(image, web_root, db_paths)
            db_exists = _real(db_path) in db_realpaths or os.path.isfile(db_path)
            try:
                size = image.stat().st_size
            except OSError:
                size = 0
            found.append(ImageCandidate(
                image_path=str(image),
                size_bytes=size,
                map_path=find_matching_map(str(image), web_root),
                db_path=db_path,
                registered=bool(registered_db or db_exists),
                registered_db=registered_db,
                source_root=root,
            ))
    return sorted(found, key=lambda c: c.image_path)


def initialize_image_catalog(candidate: ImageCandidate, timeout: int = 120) -> str:
    """Run image-analysis-init.sh for a discovered image and return the DB path."""
    cmd = [
        str(BIN_DIR / "image-analysis-init.sh"),
        candidate.image_path,
        "--db",
        candidate.db_path,
        "--print-db-path",
    ]
    if candidate.map_path:
        cmd.extend(["--map", candidate.map_path])
    env = os.environ.copy()
    env.setdefault("HDD_RECOVERY_ROOT", str(ROOT))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"image-analysis-init.sh exited {result.returncode}")
    db = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else candidate.db_path
    return db
