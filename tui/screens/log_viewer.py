"""Live log streaming for running stages, or tail of an existing log file."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, RichLog

from executor import build_command, launch
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
    ) -> None:
        super().__init__()
        self.disk = disk
        self.stage = stage
        self.log_path = log_path     # if set, read-only tail mode
        self._process: Optional[asyncio.subprocess.Process] = None
        self._done = False
        self._start_time = time.monotonic()

    def compose(self) -> ComposeResult:
        title = "Log viewer" if self.log_path else f"Running: {self.stage.name}"
        yield Header(show_clock=True)
        yield RichLog(highlight=True, markup=True, wrap=True, id="log")
        yield Label("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.stage.name
        log = self.query_one(RichLog)
        if self.log_path:
            self._tail_log(log)
        else:
            self._run_stage(log)

    @work(thread=True)
    def _tail_log(self, log: RichLog) -> None:
        path = Path(self.log_path)
        if not path.exists():
            self.app.call_from_thread(log.write, f"[red]Log file not found: {self.log_path}[/red]")
            return
        try:
            text = path.read_text(errors="replace")
            self.app.call_from_thread(log.write, text)
            self.app.call_from_thread(
                self.query_one("#status-bar", Label).update,
                f"[dim]{self.log_path}  —  read-only  (B/Q to go back)[/dim]",
            )
        except Exception as exc:
            self.app.call_from_thread(log.write, f"[red]Error reading log: {exc}[/red]")

    @work
    async def _run_stage(self, log: RichLog) -> None:
        from executor import build_command
        cmd = build_command(self.disk, self.stage)
        log.write(f"[dim]$ {' '.join(cmd)}[/dim]\n")

        status_label = self.query_one("#status-bar", Label)

        try:
            self._process = await launch(self.disk, self.stage)
        except Exception as exc:
            log.write(f"[bold red]Failed to start process: {exc}[/bold red]")
            status_label.update("[red]Failed to start — B to go back[/red]")
            return

        pid = self._process.pid
        status_label.update(f"[cyan]Running PID {pid}… (B/Q to interrupt and go back)[/cyan]")

        # Stream output
        assert self._process.stdout is not None
        async for raw in self._process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            log.write(line)

        await self._process.wait()
        rc = self._process.returncode
        elapsed = time.monotonic() - self._start_time

        if rc == 0:
            log.write(f"\n[green]✓ Finished successfully  (exit 0, {elapsed:.0f}s)[/green]")
            status_label.update(f"[green]Done in {elapsed:.0f}s — B to go back[/green]")
        else:
            log.write(f"\n[red]✗ Exited with code {rc}  ({elapsed:.0f}s)[/red]")
            status_label.update(f"[red]Exit code {rc} — B to go back[/red]")

        self._done = True

    def action_go_back(self) -> None:
        if self._process and self._process.returncode is None:
            # Process still running — ask before leaving
            self._process.terminate()
            self.app.notify(
                "Process interrupted. It may still be running if it ignores SIGTERM. "
                "Check pgrep before re-running.",
                severity="warning",
                timeout=8,
            )
        self.app.pop_screen()
        # Trigger a state refresh on the disk_detail screen below
        from screens.disk_detail import DiskDetailScreen
        for screen in reversed(self.app.screen_stack):
            if isinstance(screen, DiskDetailScreen):
                screen.refresh_state()
                break
