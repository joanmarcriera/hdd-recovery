#!/usr/bin/env python3
"""Sequential pipeline runner for non-destructive analysis stages.

Reuses the TUI stage registry (tui/stages.py) so there is one source of truth.
Acquisition stages (ddrescue-*, safecopy-*) and operator-only stages
(is_manual=True) are refused. Stages run one at a time so the CLAUDE.md rule
"do not start a new DB-writing stage while another is running" is honored.

Usage:
  image-pipeline.py <db> [options] STAGE [STAGE ...]
  image-pipeline.py <db> [options] --preset NAME
  image-pipeline.py --list

Options:
  --run             execute (default: dry-run; print the resolved commands)
  --keep-going      continue after a stage fails (default: stop on first failure)
  --skip-done       skip stages whose most recent completed scan_run is status=ok
  --list            print all eligible stage keys and exit
  --preset NAME     run a named bundle (see --list-presets)
  --list-presets    print available presets and exit
  --log <path>      combined runner log (default: <export_root>/logs/pipeline-<ts>.log)
"""
from __future__ import annotations

import argparse
import os
import shlex
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.stages import STAGES  # noqa: E402
from lib.runs import reconcile_running  # noqa: E402
from lib.watchdog import (  # noqa: E402
    DEFAULT_STAGE_TIMEOUT,
    TIMEOUT_RC,
    run_command_sync,
)

ACQUISITION_PREFIXES = ("ddrescue-", "safecopy-")
CRACKING_KEYS = {"crack-wallet", "crack-keepass", "btcrecover"}

# Wall-clock backstop so a single hung stage (e.g. the bulk_extractor
# --scope recovered finalization spin noted in CLAUDE.md) can never stall the
# whole image / queue forever. Generous by default; override per run with
# --stage-timeout or the STAGE_TIMEOUT env var (seconds; 0 disables).
PRESETS: dict[str, list[str]] = {
    "fast":      ["structure-scan", "index-tsk", "detect-wallets", "detect-pictures"],
    "carve":     ["carve-foremost", "carve-scalpel"],
    "wallet":    ["detect-wallets", "wallet-inspect", "text-seed-scan"],
    "photos":    ["detect-pictures", "enrich-photos", "dedup-photos", "tag-photos"],
    "windows":   ["ntfs-artifact-summary", "regripper", "rifiuti2",
                  "extract-winmem"],
    "indexing":  ["bulk-extractor-raw", "yara-scan", "enrich-trid"],
    # All non-destructive eligible stages in dependency order.
    # Use with --skip-done to skip stages already completed (status=ok in scan_runs).
    "full": [
        # Phase 1: metadata
        "init-db", "structure-scan", "index-tsk", "detect-wallets", "detect-pictures",
        "enrich-photos",
        # Phase 2: filesystem-specific recovery (each exits quickly if fs type doesn't match)
        "ext-recover", "ntfs-recover", "fat-recover", "xfs-recover", "btrfs-recover",
        "extract-winmem",
        # Phase 3: raw image scanning
        "bulk-extractor-raw", "yara-scan", "pdf-extract",
        # Phase 4: carving (lightest first)
        "carve-recoverjpeg", "carve-foremost", "carve-scalpel", "carve-magicrescue",
        # Phase 5: post-carve analysis (needs carving done)
        "bulk-extractor-recovered", "text-seed-scan", "enrich-trid", "recoll-index",
        # Phase 6: Windows forensics (needs bulk-extractor-raw)
        "ntfs-artifact-summary", "regripper", "rifiuti2",
        # Phase 7: timeline + broad unallocated recovery
        "plaso-timeline", "photorec-broad",
        # Phase 8: enrichment, wallet inspection, photo tagging
        "dedup-photos", "wallet-inspect", "tag-photos",
        # Phase 9: final report
        "generate-report",
    ],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_pipeline_env() -> dict[str, str]:
    """Load config/analysis-pipeline.env; values don't override existing env vars."""
    cfg = ROOT / "config" / "analysis-pipeline.env"
    result: dict[str, str] = {}
    if not cfg.exists():
        return result
    with open(cfg) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                result[k] = v
    return result


def stage_is_done(db_path: str, scan_run_key: str) -> bool:
    """Return True if the most recent completed (non-running) scan_run has status='ok'."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT status FROM scan_runs WHERE stage=? AND status != 'running'"
            " ORDER BY started_at DESC LIMIT 1",
            (scan_run_key,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None and row[0] == "ok"


def unmet_prerequisites(db_path: str, stage, idx) -> list[str]:
    """Return the requires_prior stage keys that are not yet status=ok in
    scan_runs. Only DB-tracked prerequisites (those with a scan_run_key) are
    checked; untracked/unknown prereqs are not treated as blockers."""
    unmet = []
    for pk in getattr(stage, "requires_prior", None) or []:
        prereq = idx.get(pk)
        if prereq is None or not prereq.scan_run_key:
            continue
        if not stage_is_done(db_path, prereq.scan_run_key):
            unmet.append(pk)
    return unmet


def log(msg: str, fh=None) -> None:
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
        fh.flush()


def stage_index() -> dict[str, "object"]:
    return {s.key: s for s in STAGES}


def is_eligible(stage) -> tuple[bool, str]:
    if stage.script is None:
        return False, "no script (manual placeholder)"
    if stage.is_manual:
        return False, "is_manual=True (operator-driven)"
    if any(stage.key.startswith(p) for p in ACQUISITION_PREFIXES):
        return False, "acquisition stage (writes a disk image)"
    if stage.key in CRACKING_KEYS:
        return False, "cracking stage (long-running, manual)"
    return True, ""


def list_stages() -> None:
    print("Eligible non-destructive analysis stages (key — name — runtime hint):\n")
    for s in STAGES:
        ok, _ = is_eligible(s)
        if not ok:
            continue
        opt = " [optional]" if s.is_optional else ""
        print(f"  {s.key:30s}  {s.name}{opt}  ({s.runtime_hint})")


def list_presets() -> None:
    print("Available presets:\n")
    for name, keys in PRESETS.items():
        print(f"  {name:10s}  {' → '.join(keys)}")


def db_image_context(db_path: str) -> dict[str, str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT image_path, export_root, ddrescue_map_path "
            "FROM image_info WHERE id=1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        sys.exit(f"image_info row missing in {db_path} — run image-analysis-init.sh first")
    return {
        "db": db_path,
        "image": row[0] or "",
        "export_root": row[1] or "",
        "mapfile": row[2] or "",
        "conf": "",
    }


def resolve_args(stage, ctx: dict[str, str]) -> list[str]:
    out = []
    for tok in stage.args_template:
        try:
            out.append(tok.format(**ctx))
        except KeyError as e:
            sys.exit(f"stage {stage.key}: unknown placeholder {{{e.args[0]}}} in args_template")
    return out


def run_command(cmd: list[str], env: dict[str, str], timeout: int,
                fh=None) -> tuple[int, float]:
    """Run cmd to completion, returning (rc, elapsed_seconds). A positive
    timeout kills the whole process group and returns TIMEOUT_RC. Testable in
    isolation (no stage/db objects required)."""
    def emit_output(text: str) -> None:
        print(text, end="", flush=True)
        if fh:
            fh.write(text)
            fh.flush()

    try:
        result = run_command_sync(
            cmd,
            env=env,
            wall_timeout=timeout,
            on_output=emit_output,
            log_event=lambda msg: log(msg, fh),
        )
    except KeyboardInterrupt:
        log("  INTERRUPTED at user request - terminating stage process group", fh)
        raise
    return result.rc, result.elapsed


def run_stage(stage, ctx: dict[str, str], do_run: bool, fh,
              extra_env: dict[str, str] | None = None,
              timeout: int = 0) -> tuple[int, float]:
    script = ROOT / "bin" / stage.script
    if not script.exists():
        log(f"  ERROR: script not found: {script}", fh)
        return 127, 0.0
    cmd = [str(script)] + resolve_args(stage, ctx)
    log(f"  CMD: {shlex.join(cmd)}", fh)
    if not do_run:
        return 0, 0.0
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        return run_command(cmd, env, timeout, fh)
    except FileNotFoundError:
        log(f"  ERROR: cannot execute: {script}", fh)
        return 127, 0.0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="image-pipeline.py <db> [options] STAGE [STAGE ...]")
    ap.add_argument("db", nargs="?", help="path to *.analysis.sqlite")
    ap.add_argument("stages", nargs="*", help="stage keys to run in order")
    ap.add_argument("--run", action="store_true",
                    help="execute (default: dry-run / preview)")
    ap.add_argument("--keep-going", action="store_true",
                    help="continue after a stage fails")
    ap.add_argument("--skip-done", action="store_true",
                    help="skip stages whose most recent completed scan_run has status=ok")
    ap.add_argument("--require-prereqs", action="store_true",
                    help="skip a stage when its requires_prior stages are not yet "
                         "status=ok (recommended for unattended queues so a stage "
                         "with missing inputs doesn't burn hours)")
    ap.add_argument("--list", action="store_true",
                    help="list eligible stage keys and exit")
    ap.add_argument("--list-presets", action="store_true",
                    help="list named presets and exit")
    ap.add_argument("--preset", help=f"run a named bundle ({', '.join(PRESETS)})")
    ap.add_argument("--log", help="combined runner log path")
    ap.add_argument("--stage-timeout", type=int, default=None,
                    help="per-stage wall-clock limit in seconds (0 disables; "
                         "default from STAGE_TIMEOUT env or "
                         f"{DEFAULT_STAGE_TIMEOUT})")
    args = ap.parse_args()

    if args.stage_timeout is not None:
        stage_timeout = args.stage_timeout
    else:
        try:
            stage_timeout = int(os.environ.get("STAGE_TIMEOUT",
                                               DEFAULT_STAGE_TIMEOUT))
        except ValueError:
            stage_timeout = DEFAULT_STAGE_TIMEOUT

    if args.list:
        list_stages()
        return 0
    if args.list_presets:
        list_presets()
        return 0

    if not args.db:
        ap.error("db path required (or use --list / --list-presets)")
    if not os.path.isfile(args.db):
        sys.exit(f"database not found: {args.db}")

    keys: list[str]
    if args.preset:
        if args.preset not in PRESETS:
            sys.exit(f"unknown preset: {args.preset} (try --list-presets)")
        keys = list(PRESETS[args.preset])
        if args.stages:
            keys += args.stages
    else:
        keys = list(args.stages)

    if not keys:
        ap.error("no stages given (pass keys positionally or use --preset)")

    idx = stage_index()
    plan = []
    for k in keys:
        s = idx.get(k)
        if s is None:
            sys.exit(f"unknown stage key: {k} (try --list)")
        ok, reason = is_eligible(s)
        if not ok:
            sys.exit(f"refusing stage {k}: {reason}")
        plan.append(s)

    ctx = db_image_context(args.db)
    extra_env = load_pipeline_env()

    log_path = args.log
    if not log_path and ctx["export_root"]:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = os.path.join(ctx["export_root"], "logs", f"pipeline-{ts}.log")
    fh = None
    if log_path and args.run:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        fh = open(log_path, "a", buffering=1)

    log(f"DB:          {args.db}", fh)
    log(f"Image:       {ctx['image']}", fh)
    log(f"Export root: {ctx['export_root']}", fh)
    log(f"Mode:        {'RUN' if args.run else 'dry-run'}"
        f"{'  keep-going' if args.keep_going else ''}"
        f"{'  skip-done' if args.skip_done else ''}"
        f"{'  require-prereqs' if args.require_prereqs else ''}", fh)
    log(f"Stage limit: {stage_timeout}s" if stage_timeout > 0
        else "Stage limit: disabled", fh)
    if fh:
        log(f"Runner log:  {log_path}", fh)
    log(f"Plan:        {len(plan)} stage(s) — {' → '.join(s.key for s in plan)}", fh)

    # Correct any rows a previous kill/crash/restart left stuck at 'running'
    # before we start, so status reflects reality. Never fatal.
    if args.run:
        try:
            reconciled = reconcile_running(args.db)
            if reconciled:
                log(f"Reconciled:  {reconciled} stale 'running' run(s) -> interrupted", fh)
        except Exception as e:
            log(f"  (reconcile skipped: {e})", fh)
    log("", fh)

    results: list[tuple[str, str, float, int]] = []
    failed_any = False
    for i, s in enumerate(plan, 1):
        log(f"[{i}/{len(plan)}] {s.key} — {s.name} ({s.runtime_hint})", fh)
        if args.skip_done and s.scan_run_key and stage_is_done(args.db, s.scan_run_key):
            log(f"  SKIP: already completed (status=ok in scan_runs)", fh)
            results.append((s.key, "skipped", 0.0, 0))
            continue
        if args.require_prereqs:
            unmet = unmet_prerequisites(args.db, s, idx)
            if unmet:
                log(f"  SKIP: prerequisites not ok: {', '.join(unmet)}", fh)
                results.append((s.key, "skipped", 0.0, 0))
                continue
        if s.warning:
            log(f"  WARNING: {s.warning}", fh)
        try:
            rc, dt = run_stage(s, ctx, args.run, fh, extra_env, stage_timeout)
        except KeyboardInterrupt:
            results.append((s.key, "interrupted", 0.0, 130))
            failed_any = True
            break
        if not args.run:
            results.append((s.key, "preview", 0.0, 0))
            continue
        status = "ok" if rc == 0 else ("timeout" if rc == TIMEOUT_RC else "failed")
        results.append((s.key, status, dt, rc))
        log(f"  -> {status} in {dt:.1f}s (rc={rc})", fh)
        if rc != 0:
            failed_any = True
            if not args.keep_going:
                log(f"  stopping (use --keep-going to continue past failures)", fh)
                break

    log("", fh)
    log("Summary:", fh)
    for key, status, dt, rc in results:
        log(f"  {key:30s} {status:10s} {dt:7.1f}s  rc={rc}", fh)
    if fh:
        fh.close()

    return 1 if (args.run and failed_any) else 0


if __name__ == "__main__":
    sys.exit(main())
