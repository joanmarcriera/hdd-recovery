#!/usr/bin/env python3
"""Parse hashcat status output and update crack_tasks progress."""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time

PROGRESS_RE = re.compile(r"Progress\.*:\s*\d+\s*/\s*\d+\s*\(([\d.]+)%\)")
ETA_PAREN_RE = re.compile(r"\(([^()]*(?:sec|min|hour|day)[^()]*)\)\s*$", re.I)
ETA_TOKEN_RE = re.compile(
    r"(\d+)\s*(day|days|hour|hours|hr|hrs|min|mins|minute|minutes|sec|secs|second|seconds)",
    re.I,
)


def parse_eta_seconds(text: str) -> int | None:
    match = ETA_PAREN_RE.search(text.strip())
    if not match:
        return None
    total = 0
    for value, unit in ETA_TOKEN_RE.findall(match.group(1)):
        n = int(value)
        unit = unit.lower()
        if unit.startswith("day"):
            total += n * 86400
        elif unit.startswith("hour") or unit in {"hr", "hrs"}:
            total += n * 3600
        elif unit.startswith("min"):
            total += n * 60
        else:
            total += n
    return total if total > 0 else None


def parse_hashcat_status_line(line: str) -> dict[str, float | int]:
    out: dict[str, float | int] = {}
    progress = PROGRESS_RE.search(line)
    if progress:
        out["progress_pct"] = float(progress.group(1))
    if line.lstrip().startswith("Time.Estimated"):
        eta = parse_eta_seconds(line)
        if eta is not None:
            out["eta_seconds"] = eta
    return out


def update_task_progress(
    db_path: str,
    task_id: int,
    *,
    progress_pct: float | None = None,
    eta_seconds: int | None = None,
) -> None:
    if progress_pct is None and eta_seconds is None:
        return
    sets = []
    params: list[float | int] = []
    if progress_pct is not None:
        sets.append("progress_pct=?")
        params.append(progress_pct)
    if eta_seconds is not None:
        sets.append("eta_seconds=?")
        params.append(eta_seconds)
    params.append(task_id)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"UPDATE crack_tasks SET {', '.join(sets)} WHERE id=?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def stream_updates(
    db_path: str,
    task_id: int,
    *,
    pass_through: bool = False,
    min_interval: float = 5.0,
) -> None:
    last_write = 0.0
    pending: dict[str, float | int] = {}
    for line in sys.stdin:
        if pass_through:
            print(line, end="", flush=True)
        parsed = parse_hashcat_status_line(line)
        if parsed:
            pending.update(parsed)
        now = time.monotonic()
        if pending and now - last_write >= min_interval:
            update_task_progress(
                db_path,
                task_id,
                progress_pct=pending.get("progress_pct"),
                eta_seconds=pending.get("eta_seconds"),
            )
            pending.clear()
            last_write = now
    if pending:
        update_task_progress(
            db_path,
            task_id,
            progress_pct=pending.get("progress_pct"),
            eta_seconds=pending.get("eta_seconds"),
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("task_id", type=int)
    ap.add_argument("--pass-through", action="store_true")
    ap.add_argument("--min-interval", type=float, default=5.0)
    args = ap.parse_args()
    stream_updates(
        args.db,
        args.task_id,
        pass_through=args.pass_through,
        min_interval=max(0.0, args.min_interval),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
