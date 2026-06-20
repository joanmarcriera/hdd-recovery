"""Shared subprocess watchdog for CLI and TUI stage execution."""
from __future__ import annotations

import asyncio
import inspect
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DEFAULT_STAGE_TIMEOUT = 43200  # 12h; matches the historical CLI default
DEFAULT_IDLE_TIMEOUT = 3600    # 1h without stdout/stderr output
DEFAULT_PROGRESS_TIMEOUT = 3600
DEFAULT_PROGRESS_INTERVAL = 30
TIMEOUT_RC = 124               # matches coreutils `timeout`
KILL_GRACE_S = 10              # SIGTERM -> SIGKILL grace for the process group


@dataclass(frozen=True)
class WatchdogResult:
    rc: int
    elapsed: float
    timed_out: bool = False
    timeout_kind: str | None = None
    message: str = ""


def timeout_from_env(name: str, default: int) -> int:
    """Read a non-negative integer timeout from env; invalid values use default."""
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


async def _emit(callback: Callable[[Any], Any] | None, value: Any) -> None:
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await result


async def terminate_process_group(
    process: asyncio.subprocess.Process,
    *,
    grace_s: int = KILL_GRACE_S,
) -> None:
    """Terminate a process group, escalating to SIGKILL if it ignores SIGTERM."""
    if process.returncode is not None:
        return
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return

    for sig, wait_s in ((signal.SIGTERM, grace_s), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=wait_s)
            return
        except asyncio.TimeoutError:
            continue


def _next_event(
    *,
    start: float,
    last_output: float,
    last_progress: float,
    next_probe: float | None,
    wall_timeout: float,
    idle_timeout: float,
    progress_timeout: float,
) -> tuple[str | None, float | None]:
    events: list[tuple[str, float]] = []
    if wall_timeout > 0:
        events.append(("wall", start + wall_timeout))
    if idle_timeout > 0:
        events.append(("idle", last_output + idle_timeout))
    if progress_timeout > 0:
        events.append(("progress", last_progress + progress_timeout))
    if next_probe is not None:
        events.append(("probe", next_probe))
    if not events:
        return None, None
    return min(events, key=lambda item: item[1])


def _timeout_message(kind: str, timeout_s: int) -> str:
    if kind == "idle":
        return (
            f"  IDLE TIMEOUT after {timeout_s}s without output "
            f"- terminating stage process group"
        )
    if kind == "progress":
        return (
            f"  PROGRESS TIMEOUT after {timeout_s}s without useful progress "
            f"- terminating stage process group"
        )
    return f"  TIMEOUT after {timeout_s}s - terminating stage process group"


async def stream_process(
    process: asyncio.subprocess.Process,
    *,
    wall_timeout: int = 0,
    idle_timeout: int = 0,
    progress_timeout: float = 0,
    progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
    progress_probe: Callable[[], Any] | None = None,
    on_progress: Callable[[float], Any] | None = None,
    on_output: Callable[[str], Any] | None = None,
    log_event: Callable[[str], Any] | None = None,
) -> WatchdogResult:
    """Stream a launched process until exit or timeout.

    The process must have been launched in its own session when group-kill
    semantics are required. `wall_timeout=0` and `idle_timeout=0` disable the
    respective checks.
    """
    start = time.monotonic()
    last_output = start
    last_progress = start
    last_probe_value: float | None = None
    if progress_probe is not None and progress_timeout > 0:
        next_probe: float | None = start
        progress_interval = max(0.1, progress_interval)
    else:
        next_probe = None
    wait_task = asyncio.create_task(process.wait())
    read_task: asyncio.Task[bytes] | None = None
    stdout = process.stdout
    if stdout is not None:
        read_task = asyncio.create_task(stdout.read(8192))

    try:
        while True:
            tasks = {wait_task}
            if read_task is not None:
                tasks.add(read_task)

            kind, deadline = _next_event(
                start=start,
                last_output=last_output,
                last_progress=last_progress,
                next_probe=next_probe,
                wall_timeout=wall_timeout,
                idle_timeout=idle_timeout,
                progress_timeout=progress_timeout if next_probe is not None else 0,
            )
            wait_s = None if deadline is None else max(0.0, deadline - time.monotonic())
            done, _ = await asyncio.wait(
                tasks,
                timeout=wait_s,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                assert kind is not None
                if kind == "probe":
                    assert progress_probe is not None
                    value = progress_probe()
                    if inspect.isawaitable(value):
                        value = await value
                    if value is not None:
                        value = float(value)
                        advanced = (
                            last_probe_value is None and value > 0
                        ) or (
                            last_probe_value is not None
                            and value > last_probe_value
                        )
                        last_probe_value = value
                        if advanced:
                            last_progress = time.monotonic()
                            await _emit(on_progress, value)
                    next_probe = time.monotonic() + progress_interval
                    continue
                if kind == "idle":
                    timeout_s = idle_timeout
                elif kind == "progress":
                    timeout_s = progress_timeout
                else:
                    timeout_s = wall_timeout
                message = _timeout_message(kind, timeout_s)
                await _emit(log_event, message)
                await terminate_process_group(process)
                if read_task is not None:
                    read_task.cancel()
                wait_task.cancel()
                return WatchdogResult(
                    rc=TIMEOUT_RC,
                    elapsed=time.monotonic() - start,
                    timed_out=True,
                    timeout_kind=kind,
                    message=message,
                )

            if read_task is not None and read_task in done:
                chunk = read_task.result()
                if chunk:
                    last_output = time.monotonic()
                    await _emit(
                        on_output,
                        chunk.decode("utf-8", errors="replace"),
                    )
                    read_task = asyncio.create_task(stdout.read(8192))
                else:
                    read_task = None

            if wait_task in done:
                rc = wait_task.result()
                if read_task is not None:
                    try:
                        chunk = await asyncio.wait_for(read_task, timeout=1)
                    except asyncio.TimeoutError:
                        read_task.cancel()
                        chunk = b""
                    if chunk:
                        await _emit(
                            on_output,
                            chunk.decode("utf-8", errors="replace"),
                        )
                if stdout is not None:
                    while not stdout.at_eof():
                        chunk = await stdout.read(8192)
                        if not chunk:
                            break
                        await _emit(
                            on_output,
                            chunk.decode("utf-8", errors="replace"),
                        )
                return WatchdogResult(rc=rc, elapsed=time.monotonic() - start)
    except asyncio.CancelledError:
        if read_task is not None:
            read_task.cancel()
        wait_task.cancel()
        raise


async def run_command_async(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    wall_timeout: int = 0,
    idle_timeout: int = 0,
    progress_timeout: float = 0,
    progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
    progress_probe: Callable[[], Any] | None = None,
    on_progress: Callable[[float], Any] | None = None,
    stdin_data: bytes = b"",
    on_start: Callable[[asyncio.subprocess.Process], Any] | None = None,
    on_output: Callable[[str], Any] | None = None,
    log_event: Callable[[str], Any] | None = None,
) -> WatchdogResult:
    """Launch a command in its own session and supervise it with the watchdog."""
    stdin_mode = asyncio.subprocess.PIPE if stdin_data else None
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdin=stdin_mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        await _emit(on_start, process)
        if stdin_data and process.stdin:
            process.stdin.write(stdin_data)
            await process.stdin.drain()
            process.stdin.close()
        return await stream_process(
            process,
            wall_timeout=wall_timeout,
            idle_timeout=idle_timeout,
            progress_timeout=progress_timeout,
            progress_interval=progress_interval,
            progress_probe=progress_probe,
            on_progress=on_progress,
            on_output=on_output,
            log_event=log_event,
        )
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            await terminate_process_group(process)
        raise


def run_command_sync(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    wall_timeout: int = 0,
    idle_timeout: int = 0,
    progress_timeout: float = 0,
    progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
    progress_probe: Callable[[], Any] | None = None,
    on_progress: Callable[[float], Any] | None = None,
    on_output: Callable[[str], Any] | None = None,
    log_event: Callable[[str], Any] | None = None,
) -> WatchdogResult:
    """Synchronous wrapper for CLI callers."""
    return asyncio.run(
        run_command_async(
            cmd,
            env=env,
            wall_timeout=wall_timeout,
            idle_timeout=idle_timeout,
            progress_timeout=progress_timeout,
            progress_interval=progress_interval,
            progress_probe=progress_probe,
            on_progress=on_progress,
            on_output=on_output,
            log_event=log_event,
        )
    )
