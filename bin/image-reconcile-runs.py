#!/usr/bin/env python3
"""Reconcile scan_runs rows left 'running' by a killed/crashed/restarted stage.

Marks rows whose recorded pid is gone as 'interrupted'; rows without a pid
(pre-supervision) are left for human review. Safe to run on startup.

Usage:
  image-reconcile-runs.py <db> [<db> ...]
  image-reconcile-runs.py --root /data/db
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.runs import reconcile_running  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dbs", nargs="*", help="paths to *.analysis.sqlite")
    ap.add_argument("--root", help="reconcile every DB found under this directory")
    ap.add_argument("--suffix", default=".analysis.sqlite")
    args = ap.parse_args()

    dbs = list(args.dbs)
    if args.root:
        dbs += sorted(glob.glob(os.path.join(args.root, f"**/*{args.suffix}"),
                                recursive=True))
    if not dbs:
        ap.error("give one or more DB paths or --root")

    total = 0
    for db in dbs:
        if not os.path.isfile(db):
            print(f"skip (missing): {db}")
            continue
        try:
            n = reconcile_running(db)
        except Exception as e:  # never let reconciliation abort a caller
            print(f"error {db}: {e}")
            continue
        total += n
        if n:
            print(f"{db}: reconciled {n} stale run(s)")
    print(f"reconciled {total} stale run(s) across {len(dbs)} db(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
