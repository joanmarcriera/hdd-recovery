"""Shared UTC timestamp helper.

Python twin of ``lib/common.sh::timestamp_utc``. Both emit ISO-8601 at second
precision with a trailing ``Z`` and write the same SQLite columns
(``scan_runs.started_at`` etc.), so the two formats MUST stay in sync.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    """Return the current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
