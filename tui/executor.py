"""Command building and async subprocess execution."""
from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import BIN_DIR
from stages import StageDef
from state import DiskInfo


def build_command(disk: DiskInfo, stage: StageDef) -> list[str]:
    """Return the full argv list for a stage, substituting disk context."""
    if not stage.script:
        return []
    ctx = {
        "image": str(disk.image_path),
        "db": str(disk.db_path),
        "mapfile": str(disk.map_path) if disk.map_path else "",
        "conf": str(disk.conf_path) if disk.conf_path else "",
        "export_root": str(disk.export_root),
    }
    script_path = str(BIN_DIR / stage.script)
    args: list[str] = []
    for i, tmpl in enumerate(stage.args_template):
        val = tmpl.format(**ctx)
        # Drop "--flag" + empty-value pairs (e.g. "--map" when mapfile unknown)
        if val == "" and tmpl.startswith("{") and i > 0 and stage.args_template[i - 1].startswith("--"):
            args.pop()  # remove the preceding flag
            continue
        if val == "" and tmpl.startswith("{"):
            continue
        args.append(val)
    return [script_path, *args]


def command_display(disk: DiskInfo, stage: StageDef) -> str:
    """Human-readable command string."""
    cmd = build_command(disk, stage)
    if not cmd:
        return "(manual step — no command)"
    return " ".join(shlex.quote(a) for a in cmd)


async def launch(
    disk: DiskInfo,
    stage: StageDef,
) -> asyncio.subprocess.Process:
    """Launch the stage command and return the running process."""
    cmd = build_command(disk, stage)
    if not cmd:
        raise ValueError(f"Stage {stage.key} has no script to run")

    stdin_mode = asyncio.subprocess.PIPE if stage.auto_stdin else None

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=stdin_mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )

    if stage.auto_stdin and process.stdin:
        # ddrescue uses --ask: write confirmation and close stdin
        process.stdin.write(stage.auto_stdin)
        await process.stdin.drain()
        process.stdin.close()

    return process


async def launch_cmd(cmd: list[str]) -> asyncio.subprocess.Process:
    """Launch an arbitrary argv list (used when a config screen overrides stage args)."""
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )


def has_concurrent_db_writer(disk: DiskInfo, exclude_stage_key: str = "") -> Optional[str]:
    """Return the name of a currently-running DB-writing stage, or None."""
    from stages import STAGES
    from state import is_process_live

    for s in STAGES:
        if s.key == exclude_stage_key:
            continue
        if not s.requires_db_write:
            continue
        if not s.pgrep_pattern:
            continue
        if is_process_live(s.pgrep_pattern, disk):
            return s.name
    return None


def has_any_live_process(disk: DiskInfo, exclude_stage_key: str = "") -> Optional[str]:
    """Return the name of any currently-running stage process, or None.

    Skips view-only stages (fast read-only commands that never conflict).
    Used to enforce the rule that only one substantive process runs at a time.
    """
    from stages import STAGES
    from state import is_process_live

    for s in STAGES:
        if s.key == exclude_stage_key:
            continue
        if s.is_view_only:
            continue  # fast read-only; never a conflict
        if not s.pgrep_pattern:
            continue
        if is_process_live(s.pgrep_pattern, disk):
            return s.name
    return None
