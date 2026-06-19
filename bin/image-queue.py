#!/usr/bin/env python3
"""Run image-pipeline.py over several analysis DBs, sequentially or with a
bounded number of parallel workers.

This is the orchestrator behind the web UI's "Queue work" page: it loops over
the selected images and invokes `image-pipeline.py <db> --run …` for each, so
each image still gets its own per-stage logging and DB tracking. Its own stdout
(the queue log) records START/DONE markers per image.

Usage:
  image-queue.py [--jobs N] [--skip-done] [--keep-going] [--dry-run]
                 --stages s1,s2,... DB [DB ...]

  --jobs N        max images processed at once (1 = sequential; default 1).
                  Keep this low for carving on spinning disks.
  --stages CSV    comma-separated pipeline stage keys to run on every image.
  --skip-done     pass through: skip stages already status=ok.
  --keep-going    pass through: continue past a failing stage.
  --dry-run       print the per-image commands without executing.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent / "image-pipeline.py"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_cmd(db: str, stages: list[str], skip_done: bool, keep_going: bool) -> list[str]:
    """Build the image-pipeline.py command for one image (pure/testable)."""
    cmd = [sys.executable, str(PIPELINE), db, "--run"]
    if skip_done:
        cmd.append("--skip-done")
    if keep_going:
        cmd.append("--keep-going")
    cmd += stages
    return cmd


def run_one(db: str, stages, skip_done, keep_going) -> int:
    cmd = build_cmd(db, stages, skip_done, keep_going)
    name = Path(db).name
    print(f"[{_ts()}] START {name}", flush=True)
    t0 = time.time()
    try:
        rc = subprocess.run(cmd).returncode
    except Exception as e:
        print(f"[{_ts()}] ERROR {name}: {e}", flush=True)
        return 1
    print(f"[{_ts()}] DONE  {name}  rc={rc}  ({int(time.time()-t0)}s)", flush=True)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dbs", nargs="+", help="paths to *.analysis.sqlite")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--stages", default="", help="comma-separated stage keys")
    ap.add_argument("--skip-done", action="store_true")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    if not stages:
        ap.error("no --stages given")
    jobs = max(1, args.jobs)

    print(f"[{_ts()}] queue: {len(args.dbs)} image(s), jobs={jobs}, "
          f"stages={','.join(stages)}", flush=True)

    if args.dry_run:
        for db in args.dbs:
            print("DRY " + " ".join(build_cmd(db, stages, args.skip_done, args.keep_going)),
                  flush=True)
        return 0

    rcs: list[int] = []
    if jobs == 1:
        for db in args.dbs:
            rcs.append(run_one(db, stages, args.skip_done, args.keep_going))
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(run_one, db, stages, args.skip_done, args.keep_going)
                    for db in args.dbs]
            rcs = [f.result() for f in futs]

    failed = sum(1 for rc in rcs if rc != 0)
    print(f"[{_ts()}] queue finished: {len(rcs)-failed} ok, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
