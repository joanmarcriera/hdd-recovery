"""Process activity sampling and stuckness classification for the TUI monitor."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class ProcessCounters:
    cpu_ticks: int
    io_bytes: int


@dataclass(frozen=True)
class ProcessActivity:
    cpu_pct: float
    io_mib_s: float


@dataclass(frozen=True)
class ActivitySample:
    cpu_pct: float = 0.0
    io_mib_s: float = 0.0
    last_progress_age_s: Optional[int] = None
    heartbeat_age_s: Optional[int] = None
    started_age_s: Optional[int] = None


@dataclass(frozen=True)
class ActivityJudgment:
    state: str
    label: str
    source: str
    style: str


def _clock_hz() -> int:
    try:
        return os.sysconf("SC_CLK_TCK")
    except Exception:
        return 100


def read_process_counters(pid: int) -> Optional[ProcessCounters]:
    """Return CPU ticks and cumulative read/write bytes for a process."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        close = stat.rfind(")")
        if close < 0:
            return None
        rest = stat[close + 2 :].split()
        # After comm, rest[0] is field 3. utime/stime are fields 14/15.
        cpu_ticks = int(rest[11]) + int(rest[12])
    except Exception:
        return None

    io_bytes = 0
    try:
        for line in Path(f"/proc/{pid}/io").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"read_bytes", "write_bytes"}:
                io_bytes += int(value.strip())
    except Exception:
        pass
    return ProcessCounters(cpu_ticks=cpu_ticks, io_bytes=io_bytes)


class ProcessActivitySampler:
    """Track per-process CPU and I/O deltas between monitor ticks."""

    def __init__(
        self,
        reader: Callable[[int], Optional[ProcessCounters]] = read_process_counters,
        time_fn: Callable[[], float] = time.monotonic,
        hz: Optional[int] = None,
    ) -> None:
        self._reader = reader
        self._time_fn = time_fn
        self._hz = hz or _clock_hz()
        self._prev: dict[int, tuple[ProcessCounters, float]] = {}

    def sample(self, pid: int) -> ProcessActivity:
        now = self._time_fn()
        counters = self._reader(pid)
        if counters is None:
            self._prev.pop(pid, None)
            return ProcessActivity(cpu_pct=0.0, io_mib_s=0.0)

        previous = self._prev.get(pid)
        self._prev[pid] = (counters, now)
        if previous is None:
            return ProcessActivity(cpu_pct=0.0, io_mib_s=0.0)

        prev_counters, prev_time = previous
        elapsed = now - prev_time
        if elapsed <= 0:
            return ProcessActivity(cpu_pct=0.0, io_mib_s=0.0)

        cpu_delta = max(0, counters.cpu_ticks - prev_counters.cpu_ticks)
        io_delta = max(0, counters.io_bytes - prev_counters.io_bytes)
        cpu_pct = (cpu_delta / self._hz) / elapsed * 100.0
        io_mib_s = io_delta / elapsed / 1_048_576
        return ProcessActivity(cpu_pct=cpu_pct, io_mib_s=io_mib_s)


def age_seconds(ts: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now_dt = now or datetime.now(timezone.utc)
        return max(0, int((now_dt - dt).total_seconds()))
    except Exception:
        return None


def format_duration(secs: int) -> str:
    secs = max(0, int(secs))
    if secs < 60:
        return f"{secs}s"
    minutes, seconds = divmod(secs, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def classify_activity(
    sample: ActivitySample,
    *,
    active_cpu_pct: float = 1.0,
    active_io_mib_s: float = 0.1,
    recent_progress_s: int = 120,
    stuck_s: int = 3600,
) -> ActivityJudgment:
    """Classify a running stage as active, idle, no-output, or probably stuck."""
    if sample.cpu_pct >= active_cpu_pct:
        return ActivityJudgment(
            state="active",
            label="active",
            source=f"cpu {sample.cpu_pct:.0f}%",
            style="green",
        )
    if sample.io_mib_s >= active_io_mib_s:
        return ActivityJudgment(
            state="active",
            label="active",
            source=f"io {sample.io_mib_s:.1f} MB/s",
            style="green",
        )

    if sample.last_progress_age_s is not None:
        age = sample.last_progress_age_s
        if age <= recent_progress_s:
            return ActivityJudgment(
                state="active",
                label="active",
                source=f"progress {format_duration(age)} ago",
                style="green",
            )
        if age >= stuck_s:
            return ActivityJudgment(
                state="probably stuck",
                label="probably stuck",
                source=f"no progress {format_duration(age)}",
                style="bold red",
            )
        return ActivityJudgment(
            state="idle",
            label=f"idle {format_duration(age)}",
            source="last progress",
            style="yellow",
        )

    age = sample.heartbeat_age_s
    source = "heartbeat"
    if age is None:
        age = sample.started_age_s
        source = "started"
    if age is None:
        return ActivityJudgment(
            state="no output",
            label="no output",
            source="no progress timestamp",
            style="yellow",
        )
    if age >= stuck_s:
        return ActivityJudgment(
            state="probably stuck",
            label="probably stuck",
            source=f"no progress timestamp {format_duration(age)}",
            style="bold red",
        )
    return ActivityJudgment(
        state="no output",
        label=f"no output {format_duration(age)}",
        source=source,
        style="yellow",
    )
