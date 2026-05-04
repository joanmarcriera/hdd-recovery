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
    """Track read/write MB/s for multiple named block devices."""

    def __init__(self) -> None:
        # {dev_name: (sectors_read, sectors_write, monotonic_time)}
        self._prev: dict[str, tuple[int, int, float]] = {}

    def _parse_diskstats(self) -> dict[str, tuple[int, int]]:
        """Return {dev_name: (sectors_read, sectors_written)} from /proc/diskstats."""
        result: dict[str, tuple[int, int]] = {}
        try:
            for line in Path("/proc/diskstats").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 10:
                    name = parts[2]
                    result[name] = (int(parts[5]), int(parts[9]))
        except Exception:
            pass
        return result

    def sample(self, devices: list[str]) -> dict[str, tuple[float, float]]:
        """Return {dev: (read_MBs, write_MBs)} for each requested device."""
        now = time.monotonic()
        stats = self._parse_diskstats()
        result: dict[str, tuple[float, float]] = {}
        for dev in devices:
            if dev not in stats:
                result[dev] = (0.0, 0.0)
                continue
            sr, sw = stats[dev]
            if dev in self._prev:
                pr, pw, pt = self._prev[dev]
                dt = now - pt
                if dt > 0:
                    r = max(0.0, (sr - pr) * 512 / dt / 1_048_576)
                    w = max(0.0, (sw - pw) * 512 / dt / 1_048_576)
                    result[dev] = (r, w)
                else:
                    result[dev] = (0.0, 0.0)
            else:
                result[dev] = (0.0, 0.0)
            self._prev[dev] = (sr, sw, now)
        return result

    def active_loop_devices(self, min_mbs: float = 0.1) -> list[str]:
        """Return loop device names with recent I/O above min_mbs (read or write)."""
        now = time.monotonic()
        stats = self._parse_diskstats()
        active = []
        for name, (sr, sw) in stats.items():
            if not name.startswith("loop"):
                continue
            if name in self._prev:
                pr, pw, pt = self._prev[name]
                dt = now - pt
                if dt > 0:
                    r = (sr - pr) * 512 / dt / 1_048_576
                    w = (sw - pw) * 512 / dt / 1_048_576
                    if r >= min_mbs or w >= min_mbs:
                        active.append(name)
        return sorted(active)


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


def _mem_summary() -> str:
    """Return a short 'RAM X GB free / Y GB  swap Z GB free' string."""
    try:
        info: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k.strip()] = int(v.split()[0])  # kB
        avail_gb = info.get("MemAvailable", 0) / 1_048_576
        total_gb = info.get("MemTotal",     0) / 1_048_576
        swap_free_gb  = info.get("SwapFree",  0) / 1_048_576
        swap_total_gb = info.get("SwapTotal", 0) / 1_048_576
        ram_pct = 100 * (1 - avail_gb / total_gb) if total_gb else 0
        ram_style = "bold red" if ram_pct > 90 else "yellow" if ram_pct > 75 else "green"
        ram_part = f"[{ram_style}]{avail_gb:.1f}[/{ram_style}]/{total_gb:.0f} GB"
        swap_part = ""
        if swap_total_gb > 0:
            swap_pct = 100 * (1 - swap_free_gb / swap_total_gb)
            sw_style = "bold red" if swap_pct > 90 else "yellow" if swap_pct > 75 else "dim"
            swap_part = f"  swap [{sw_style}]{swap_free_gb:.0f}[/{sw_style}]/{swap_total_gb:.0f} GB"
        else:
            swap_part = "  [yellow]no swap[/yellow]"
        return f"RAM {ram_part}{swap_part}"
    except Exception:
        return ""


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
        self._cpu.sample()              # prime CPU sampler so first delta is meaningful
        self._disk.sample([_DISK_DEV])  # prime disk sampler
        self._tick()
        self.set_interval(2, self._tick)

    @work(thread=True)
    def _tick(self) -> None:
        try:
            cpu_pct = self._cpu.sample()

            # Always sample the destination disk; add source dev and active loops
            devs = [_DISK_DEV]
            src = (self.disk.source_dev or "").lstrip("/dev/") if self.disk else ""
            if src and src != _DISK_DEV:
                devs.append(src)
            loops = self._disk.active_loop_devices()
            for lp in loops:
                if lp not in devs:
                    devs.append(lp)

            io = self._disk.sample(devs)
            proc_info = _running_proc_info(self.disk)
            mem_info  = _mem_summary()
            self.app.call_from_thread(self._redraw, cpu_pct, io, devs, src, proc_info, mem_info)
        except Exception as exc:
            self.app.call_from_thread(
                self._label.update, f"[dim]monitor error: {exc}[/dim]"
            )

    def _redraw(
        self,
        cpu_pct: float,
        io: dict[str, tuple[float, float]],
        devs: list[str],
        src_dev: str,
        proc_info: Optional[str],
        mem_info: str = "",
    ) -> None:
        if self._label is None:
            return
        style = _cpu_color(cpu_pct)
        spark = _sparkline(self._cpu.history)

        # Build per-device I/O segments.  Destination always first; label it.
        io_parts: list[str] = []
        for dev in devs:
            r, w = io.get(dev, (0.0, 0.0))
            if dev == _DISK_DEV:
                label = f"[dim]{dev}[/dim]"
            elif dev == src_dev:
                # Source disk during imaging: highlight reads
                r_style = "bold cyan" if r > 5 else "cyan"
                label = f"[{r_style}]{dev}[/{r_style}]"
            else:
                # Loop device (image read during analysis)
                r_style = "cyan" if r > 5 else "dim"
                label = f"[{r_style}]{dev}[/{r_style}]"
            io_parts.append(f"{label} ↓{r:5.1f} ↑{w:4.1f}")

        parts = [
            f"CPU [{style}]{spark} {cpu_pct:4.0f}%[/{style}]",
            "  ".join(io_parts),
        ]
        if mem_info:
            parts.append(mem_info)
        if proc_info:
            parts.append(proc_info)

        self._label.update("  │  ".join(parts))

    def update_disk(self, disk: Optional[DiskInfo]) -> None:
        """Call from the screen when the active disk changes."""
        self.disk = disk
