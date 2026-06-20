"""Live log streaming for running stages, or tail of an existing log file."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from rich.markup import escape

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, RichLog

from executor import build_command, launch, launch_cmd
from lib.progress import build_stage_progress_probe
from lib.watchdog import (
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_PROGRESS_INTERVAL,
    DEFAULT_PROGRESS_TIMEOUT,
    DEFAULT_STAGE_TIMEOUT,
    stream_process,
    timeout_from_env,
)
from stages import StageDef
from state import DiskInfo


class LogViewerScreen(Screen):
    """
    Two modes:
      - live:     launch the stage command, stream output in real time
      - readonly: tail an existing log file (when L is pressed on disk_detail)
    """

    BINDINGS = [
        Binding("b",     "go_back", "Back"),
        Binding("q",     "go_back", "Back"),
    ]

    CSS = """
    LogViewerScreen {
        layout: vertical;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 2;
    }
    RichLog {
        height: 1fr;
        border: none;
        padding: 0 1;
        scrollbar-gutter: stable;
    }
    """

    def __init__(
        self,
        disk: DiskInfo,
        stage: StageDef,
        log_path: Optional[str] = None,
        cmd_override: Optional[list[str]] = None,
    ) -> None:
        super().__init__()
        self.disk = disk
        self.stage = stage
        self.log_path = log_path          # if set, read-only tail mode
        self.cmd_override = cmd_override  # if set, run this instead of build_command()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._done = False

    def compose(self) -> ComposeResult:
        title = "Log viewer" if self.log_path else f"Running: {self.stage.name}"
        yield Header(show_clock=True)
        yield RichLog(highlight=True, markup=True, wrap=True, id="log")
        yield Label("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        self.sub_title = self.stage.name
        log = self.query_one(RichLog)
        if self.log_path:
            self._tail_log(log)
        else:
            self._run_stage(log)

    @work(thread=True)
    def _tail_log(self, log: RichLog) -> None:
        import time as _time
        path = Path(self.log_path)
        if not path.exists():
            self.app.call_from_thread(log.write, f"[red]Log file not found: {escape(str(self.log_path))}[/red]")
            return
        try:
            text = path.read_text(errors="replace")
            stat = path.stat()
            age_s = _time.time() - stat.st_mtime
            if age_s < 60:
                age = f"{int(age_s)}s ago"
            elif age_s < 3600:
                age = f"{int(age_s/60)}m ago"
            else:
                age = f"{int(age_s/3600)}h {int((age_s%3600)/60)}m ago"
            size = (f"{stat.st_size/1_048_576:.1f} MB" if stat.st_size >= 1_048_576
                    else f"{stat.st_size/1024:.1f} KB")
            self.app.call_from_thread(log.write, text)
            self.app.call_from_thread(
                self.query_one("#status-bar", Label).update,
                f"[dim]{escape(str(self.log_path))}  —  {size}  modified {age}  (B/Q to go back)[/dim]",
            )
        except Exception as exc:
            self.app.call_from_thread(log.write, f"[red]Error reading log: {escape(str(exc))}[/red]")

    @work
    async def _run_stage(self, log: RichLog) -> None:
        import shlex
        if self.cmd_override:
            cmd = self.cmd_override
        else:
            from executor import build_command
            cmd = build_command(self.disk, self.stage)
        log.write(f"[dim]$ {escape(shlex.join(cmd))}[/dim]\n")

        status_label = self.query_one("#status-bar", Label)

        try:
            if self.cmd_override:
                self._process = await launch_cmd(self.cmd_override)
            else:
                self._process = await launch(self.disk, self.stage)
        except Exception as exc:
            log.write(f"[bold red]Failed to start process: {escape(str(exc))}[/bold red]")
            status_label.update("[red]Failed to start — B to go back[/red]")
            return

        pid = self._process.pid
        wall_timeout = timeout_from_env("STAGE_TIMEOUT", DEFAULT_STAGE_TIMEOUT)
        idle_timeout = timeout_from_env("STAGE_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT)
        progress_timeout = timeout_from_env(
            "STAGE_PROGRESS_TIMEOUT",
            DEFAULT_PROGRESS_TIMEOUT,
        )
        progress_interval = timeout_from_env(
            "STAGE_PROGRESS_INTERVAL",
            DEFAULT_PROGRESS_INTERVAL,
        )
        progress_probe = build_stage_progress_probe(
            str(self.disk.db_path),
            self.stage.scan_run_key,
            map_path=str(self.disk.map_path) if self.disk.map_path else "",
        )
        limits = []
        if wall_timeout > 0:
            limits.append(f"wall {wall_timeout}s")
        if idle_timeout > 0:
            limits.append(f"idle {idle_timeout}s")
        if progress_timeout > 0 and progress_probe is not None:
            limits.append(f"progress {progress_timeout}s")
        limit_text = ", ".join(limits) if limits else "timeouts disabled"
        status_label.update(
            f"[cyan]Running PID {pid} ({escape(limit_text)})… "
            "(B/Q to go back — process continues in background)[/cyan]"
        )

        def write_output(text: str) -> None:
            lines = text.splitlines()
            if not lines and text:
                log.write("")
                return
            for line in lines:
                log.write(escape(line))

        def write_event(message: str) -> None:
            log.write(f"[yellow]{escape(message)}[/yellow]")

        # Stream output; catch CancelledError (screen popped) and keep a
        # detached watchdog draining the pipe so the subprocess never blocks.
        try:
            result = await stream_process(
                self._process,
                wall_timeout=wall_timeout,
                idle_timeout=idle_timeout,
                progress_timeout=progress_timeout,
                progress_interval=progress_interval,
                progress_probe=progress_probe,
                on_progress=progress_probe.mark_progress if progress_probe else None,
                on_output=write_output,
                log_event=write_event,
            )
        except asyncio.CancelledError:
            if self._process and self._process.returncode is None:
                asyncio.ensure_future(
                    self._supervise_detached(
                        wall_timeout,
                        idle_timeout,
                        progress_timeout,
                        progress_interval,
                        progress_probe,
                    )
                )
            raise

        rc = result.rc
        elapsed = result.elapsed

        if rc == 0:
            log.write(f"\n[green]✓ Finished successfully  (exit 0, {elapsed:.0f}s)[/green]")
            status_label.update(f"[green]Done in {elapsed:.0f}s — B to go back[/green]")
        elif result.timed_out:
            if result.timeout_kind == "idle":
                label = "Idle timeout"
            elif result.timeout_kind == "progress":
                label = "Progress timeout"
            else:
                label = "Wall timeout"
            log.write(
                f"\n[bold red]✗ {escape(label)} "
                f"(exit {rc}, {elapsed:.0f}s)[/bold red]"
            )
            status_label.update(f"[red]{escape(label)} — B to go back[/red]")
        else:
            log.write(f"\n[red]✗ Exited with code {rc}  ({elapsed:.0f}s)[/red]")
            status_label.update(f"[red]Exit code {rc} — B to go back[/red]")

        self._done = True

    async def _supervise_detached(
        self,
        wall_timeout: int,
        idle_timeout: int,
        progress_timeout: int,
        progress_interval: int,
        progress_probe,
    ) -> None:
        """Keep supervising a process after the log screen is popped."""
        try:
            process = self._process
            if process is None or process.returncode is not None:
                return
            await stream_process(
                process,
                wall_timeout=wall_timeout,
                idle_timeout=idle_timeout,
                progress_timeout=progress_timeout,
                progress_interval=progress_interval,
                progress_probe=progress_probe,
                on_progress=progress_probe.mark_progress if progress_probe else None,
            )
        except Exception:
            pass

    def action_go_back(self) -> None:
        if self._process and self._process.returncode is None:
            # Process is still running — leave it running, just detach.
            # _drain_stdout is scheduled via CancelledError handler in _run_stage.
            self.app.notify(
                "Stage is still running in the background. "
                "The watchdog remains active; use T (Tail active log) from "
                "the checklist to re-attach.",
                severity="information",
                timeout=8,
            )
        self.app.pop_screen()
        # Trigger a state refresh on the disk_detail screen below
        from screens.disk_detail import DiskDetailScreen
        for screen in reversed(self.app.screen_stack):
            if isinstance(screen, DiskDetailScreen):
                screen.refresh_state()
                break
