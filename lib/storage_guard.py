"""Detect data roots that resolve to the container's writable overlay layer.

Background: the image defaults DB_ROOT/EXPORT_ROOT/IMAGE_ROOT/LOG_ROOT to
``/data/*``. When those are not bind-mounted, writes fall through to the
container's writable layer. On TrueNAS that layer lives on the FastPool SSD —
filling it and discarding all data on the next container recreate.

The reliable in-container signal is: a data root (or its nearest existing
ancestor) shares a device with ``/``. A real bind mount has a distinct
``st_dev``; an unmounted path resolves up to ``/`` and matches it.

Outside a container everything legitimately lives on ``/``, so the check is
advisory only there.

Pure logic: device ids, existence, and environment are all injectable so this
is unit-testable offline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# The data roots this guard watches, in display order.
ROOT_VARS = ("DB_ROOT", "EXPORT_ROOT", "IMAGE_ROOT", "LOG_ROOT")

# Defaults mirror config/analysis-pipeline.env so the guard sees the same paths
# the pipeline would actually write to when an env var is unset.
ROOT_DEFAULTS = {
    "DB_ROOT": "/data/db",
    "EXPORT_ROOT": "/data/exports",
    "IMAGE_ROOT": "/data/images",
    "LOG_ROOT": "/data/logs",
}


@dataclass(frozen=True)
class RootStatus:
    name: str          # e.g. "EXPORT_ROOT"
    path: str          # the configured path
    status: str        # "ok" | "overlay" | "skipped" | "unknown"
    dev: int | None    # st_dev of the nearest existing ancestor
    resolved: str      # the nearest existing ancestor that was stat-ed
    detail: str        # human-readable explanation


def in_container(env=None, dockerenv=None) -> bool:
    """True when running inside the forensics container.

    The Dockerfile sets HDD_IN_CONTAINER=1; /.dockerenv is the fallback signal.
    """
    env = os.environ if env is None else env
    if str(env.get("HDD_IN_CONTAINER", "")).strip() == "1":
        return True
    if dockerenv is None:
        dockerenv = os.path.exists("/.dockerenv")
    return bool(dockerenv)


def overlay_allowed(env=None) -> bool:
    """True when the operator has explicitly opted into overlay writes."""
    env = os.environ if env is None else env
    return str(env.get("HDD_ALLOW_OVERLAY", "")).strip() == "1"


def _nearest_existing(path, stat_fn):
    """Return (st, resolved_path) for the nearest existing ancestor of path."""
    p = os.path.abspath(path)
    while True:
        try:
            return stat_fn(p), p
        except (FileNotFoundError, NotADirectoryError):
            parent = os.path.dirname(p)
            if parent == p:
                return None, p
            p = parent


def check_roots(roots, *, in_container, root_dev=None, stat_fn=os.stat):
    """Classify each {name: path} data root.

    ``in_container``: enforce only inside the container (advisory otherwise).
    ``root_dev``: st_dev of "/"; looked up via stat_fn when not given.
    """
    if root_dev is None:
        try:
            root_dev = stat_fn("/").st_dev
        except OSError:
            root_dev = None

    results = []
    for name, path in roots.items():
        if not in_container:
            results.append(RootStatus(name, path, "skipped", None, "",
                                       "not enforced outside the container"))
            continue
        st, resolved = _nearest_existing(path, stat_fn)
        if st is None or root_dev is None:
            results.append(RootStatus(name, path, "unknown", None, resolved,
                                      "could not stat the path or its parents"))
            continue
        if st.st_dev == root_dev:
            detail = (f"resolves to {resolved} on the same device as / — "
                      f"this is the container writable layer (overlay)")
            results.append(RootStatus(name, path, "overlay", st.st_dev,
                                      resolved, detail))
        else:
            results.append(RootStatus(name, path, "ok", st.st_dev, resolved,
                                      f"on a separate mount ({resolved})"))
    return results


def dangerous_roots(results):
    """Return the subset of results that resolve to the overlay layer."""
    return [r for r in results if r.status == "overlay"]


def resolve_roots(env=None):
    """Build the {name: path} map from the environment, applying defaults."""
    env = os.environ if env is None else env
    return {name: (env.get(name) or ROOT_DEFAULTS[name]) for name in ROOT_VARS}


def check_environment(env=None, stat_fn=os.stat):
    """Convenience wrapper: classify the configured roots for the current env."""
    env = os.environ if env is None else env
    return check_roots(resolve_roots(env), in_container=in_container(env),
                       stat_fn=stat_fn)
