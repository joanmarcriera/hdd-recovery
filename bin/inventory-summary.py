#!/usr/bin/env python3
"""Cross-image inventory summary across the whole recovery haul.

Walks every ``*.analysis.sqlite`` catalog under a root and writes a timestamped
markdown + JSON + CSV report aggregating file/photo/document/wallet counts and
the highest-value hits (wallet candidates, wallet/seed findings, encrypted
containers, unique bulk_extractor values) with per-disk provenance.

Strictly read-only: it never writes to a recovery catalog or touches source
media. Reports are additive — each run gets its own timestamped filenames.

Usage:
  inventory-summary.py [--root <dir>] [--out-dir <dir>]

Root defaults to $DB_ROOT, then $RECOVERY_ROOT, then /data/db.
Out-dir defaults to $INVENTORY_REPORT_DIR, then <root>/../reports.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.inventory import collect_inventory, render_csv, render_markdown  # noqa: E402
from lib.timestamp import utc_now  # noqa: E402


def default_root():
    return os.environ.get("DB_ROOT") or os.environ.get("RECOVERY_ROOT") or "/data/db"


def default_out_dir(root):
    return (os.environ.get("INVENTORY_REPORT_DIR")
            or os.path.join(os.path.dirname(os.path.abspath(root)), "reports"))


def fs_stamp(iso):
    """utc_now() -> filesystem-safe stamp, e.g. 2026-07-04T10:15:00Z -> 20260704T101500Z."""
    return iso.replace("-", "").replace(":", "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=default_root(),
                    help="Directory tree to scan for *.analysis.sqlite (default: $DB_ROOT)")
    ap.add_argument("--out-dir", default=None,
                    help="Where to write reports (default: <root>/../reports)")
    args = ap.parse_args(argv)

    out_dir = args.out_dir or default_out_dir(args.root)
    generated_at = utc_now()

    data = collect_inventory(args.root)
    data_out = dict(data, generated_at=generated_at)

    os.makedirs(out_dir, exist_ok=True)
    stamp = fs_stamp(generated_at)
    base = os.path.join(out_dir, f"inventory-{stamp}")
    md_path, json_path, csv_path = base + ".md", base + ".json", base + ".csv"

    with open(md_path, "w") as fh:
        fh.write(render_markdown(data, generated_at))
    with open(json_path, "w") as fh:
        json.dump(data_out, fh, indent=2, default=str)
    with open(csv_path, "w") as fh:
        fh.write(render_csv(data))

    t = data["totals"]
    print(f"Scanned {t['disks']} image(s) under {args.root}")
    print(f"  files={t['files']:,}  artifacts={t['artifacts']:,}  "
          f"photos={t['photos']:,}  docs={t['docs']:,}  "
          f"wallets={t['wallet_candidates']:,}  hi-findings={t['findings_high']:,}  "
          f"encrypted={t['encrypted']:,}")
    print("Reports written:")
    for p in (md_path, json_path, csv_path):
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
