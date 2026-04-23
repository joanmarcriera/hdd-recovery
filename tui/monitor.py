"""Lightweight system monitor widget — CPU sparkline, disk I/O, running-process timer."""
from __future__ import annotations

import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from state import DiskInfo, fmt_elapsed

# Unicode block chars for sparkline ▁▂▃▄▅▆▇█
_SPARKS = "▁▂▃▄▅▆▇█"
_DISK_DEV = "sdc"          # destination disk device name
_HISTORY = 10              # sparkline width

RECOVERY_PROCS = (
    "photorec", "bulk_extractor", "foremost", "scalpel",
    "ddrescue", "fiwalk", "extundelete", "ext4magic",
    "recollindex", "image-",
)


# ---------------------------------------------------------------------------
# Sampling helpers (all read-only, no side-effects)
# ---------------------------------------------------------------------------

class _CpuSampler:
    def __init__(self) -> None:
        self._prev: Optional[tuple[int, int]] = None  # (total, idle)
        self.history: deque[float] = deque(maxlen=_HISTORY)

    def sample(self) -> float:
        """Return current CPU % (0-100), updating sparkline history."""
        try:
            line = Path("/proc/stat").read_text().splitlines()[0]
            fields = list(map(int, line.split()[1:]))
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            total = sum(fields)
            if self._prev:
                pt, pi = self._prev
                dt = total - pt
                di = idle - pi
                pct = max(0.0, min(100.0, 100 * (1 - di / dt))) if dt > 0 else 0.0
            else:
                pct = 0.0
            self._prev = (total, idle)
            self.history.append(pct)
            return pct
        except Exception:
            return 0.0


class _DiskSampler:
    def __init__(self) -> None:
        self._prev: Optional[tuple[int, int, float]] = None  # (sectors_r, sectors_w, t)

    def sample(self) -> tuple[float, float]:
        """Return (read_MBs, write_MBs) for _DISK_DEV since last call."""
        try:
            for line in Path("/proc/diskstats").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 14 and parts[2] == _DISK_DEV:
                    sr = int(parts[5])   # sectors read
                    sw = int(parts[9])   # sectors written
                    now = time.monotonic()
                    if self._prev:
                        pr, pw, pt = self._prev
                        dt = now - pt
                        if dt > 0:
                            read_mbs  = (sr - pr) * 512 / dt / 1_048_576
                            write_mbs = (sw - pw) * 512 / dt / 1_048_576
                            self._prev = (sr, sw, now)
                            return (max(0.0, read_mbs), max(0.0, write_mbs))
                    self._prev = (sr, sw, now)
                    return (0.0, 0.0)
        except Exception:
            pass
        return (0.0, 0.0)


def _sparkline(history: deque[float], max_val: float = 100.0) -> str:
    if not history:
        return " " * _HISTORY
    out = []
    for v in history:
        idx = int(v / max_val * (len(_SPARKS) - 1))
        out.append(_SPARKS[max(0, min(idx, len(_SPARKS) - 1))])
    return "".join(out)


def _cpu_color(pct: float) -> str:
    if pct >= 80:
        return "bold red"
    if pct >= 50:
        return "yellow"
    return "green"


def _running_proc_info(disk: Optional[DiskInfo]) -> Optional[str]:
    """
    Return a short string like '⟳ photorec-broad  1h 23m  [T] tail log'
    if a recovery process is found for the given disk.  Returns None otherwise.
    """
    # 1. Check scan_runs for a 'running' row and confirm via pgrep
    if disk and disk.db_exists:
        from state import _load_scan_runs
        runs = _load_scan_runs(disk.db_path)
        for run in reversed(runs):
            if run.status != "running":
                continue
            if not _pgrep_alive(disk.basename):
                continue
            elapsed = _elapsed(run.started_at)
            log_hint = "  [dim][T] tail log[/dim]" if run.log_path else ""
            return f"[cyan]⟳[/cyan] [bold]{run.stage}[/bold]  {elapsed}{log_hint}"

    # 2. Generic pgrep fallback (no disk context)
    try:
        r = subprocess.run(
            ["pgrep", "-af", "|".join(RECOVERY_PROCS)],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stdout.splitlines():
            pid, _, cmd = line.partition(" ")
            cmd = cmd.strip()
            # skip monitor/watch processes
            if "watch" in cmd or "tail" in cmd or "show-" in cmd:
                continue
            name = cmd.split("/")[-1].split()[0][:28]
            elapsed_s = _pid_elapsed(int(pid))
            return f"[cyan]⟳[/cyan] {name}  {elapsed_s}"
    except Exception:
        pass
    return None


def _pgrep_alive(basename: str) -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-fa", "|".join(RECOVERY_PROCS)],
            capture_output=True, text=True, timeout=3,
        )
        return basename in r.stdout
    except Exception:
        return False


def _elapsed(started_at_utc: str) -> str:
    """Convert ISO-8601 UTC string to human-readable elapsed time."""
    try:
        dt = datetime.fromisoformat(started_at_utc.replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        return fmt_elapsed(secs)
    except Exception:
        return "?"


def _pid_elapsed(pid: int) -> str:
    """Get elapsed time for a PID via /proc/<pid>/stat."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().split()
        start_ticks = int(stat[21])
        hz = _clock_hz()
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        secs = int(uptime - start_ticks / hz)
        return fmt_elapsed(max(0, secs))
    except Exception:
        return "?"


_HZ: Optional[int] = None

def _clock_hz() -> int:
    global _HZ
    if _HZ is None:
        try:
            import os
            _HZ = os.sysconf("SC_CLK_TCK")
        except Exception:
            _HZ = 100
    return _HZ


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class SystemBar(Widget):
    """One-line system monitor: CPU sparkline | disk I/O | running-process timer."""

    DEFAULT_CSS = """
    SystemBar {
        height: 1;
        background: $surface;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def __init__(self, disk: Optional[DiskInfo] = None) -> None:
        super().__init__()
        self.disk = disk
        self._cpu = _CpuSampler()
        self._disk = _DiskSampler()
        self._label: Optional[Static] = None

    def compose(self) -> ComposeResult:
        self._label = Static("[dim]CPU … │ sdc … │ checking processes…[/dim]", markup=True)
        yield self._label

    def on_mount(self) -> None:
        self._cpu.sample()   # prime samplers so first delta is meaningful
        self._disk.sample()
        self._tick()                        # render immediately, don't wait 2s
        self.set_interval(2, self._tick)    # then keep refreshing

    @work(thread=True)
    def _tick(self) -> None:
        try:
            cpu_pct = self._cpu.sample()
            read_mbs, write_mbs = self._disk.sample()
            proc_info = _running_proc_info(self.disk)
            self.app.call_from_thread(self._redraw, cpu_pct, read_mbs, write_mbs, proc_info)
        except Exception as exc:
            self.app.call_from_thread(
                self._label.update, f"[dim]monitor error: {exc}[/dim]"
            )

    def _redraw(
        self,
        cpu_pct: float,
        read_mbs: float,
        write_mbs: float,
        proc_info: Optional[str],
    ) -> None:
        if self._label is None:
            return
        style = _cpu_color(cpu_pct)
        spark = _sparkline(self._cpu.history)

        parts = [
            f"CPU [{style}]{spark} {cpu_pct:4.0f}%[/{style}]",
            f"[dim]{_DISK_DEV}[/dim] ↓{read_mbs:5.1f} ↑{write_mbs:5.1f} MB/s",
        ]
        if proc_info:
            parts.append(proc_info)

        self._label.update("  │  ".join(parts))

    def update_disk(self, disk: Optional[DiskInfo]) -> None:
        """Call from the screen when the active disk changes."""
        self.disk = disk
