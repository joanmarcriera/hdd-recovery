"""Per-disk stage checklist with detail panel."""
from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from executor import build_command, command_display, has_concurrent_db_writer
from monitor import SystemBar
from stages import STAGES, StageDef, STAGE_BY_KEY
from state import (
    DiskInfo, StageStatus, ICON,
    count_done, discover_disks, fmt_bytes, get_stage_status, get_stage_note,
)


_STAGE_DETAIL_PLACEHOLDER = (
    "[dim]Select a stage to see details.\n\n"
    "  Enter  run / preview\n"
    "  L      view log of selected stage\n"
    "  T      tail log of currently running stage\n"
    "  B      back to dashboard\n"
    "  R      refresh state[/dim]"
)


class StageDetailPanel(Static):
    DEFAULT_CSS = """
    StageDetailPanel {
        width: 1fr;
        border-left: solid $surface;
        padding: 1 2;
        overflow-y: auto;
    }
    """


class DiskDetailScreen(Screen):
    BINDINGS = [
        Binding("b",      "go_back",      "Back"),
        Binding("r",      "refresh",      "Refresh"),
        Binding("enter",  "run_stage",    "Run / Preview"),
        Binding("l",      "view_log",     "View log"),
        Binding("t",      "tail_running", "Tail active log"),
        Binding("q",      "app.quit",     "Quit"),
    ]

    CSS = """
    DiskDetailScreen {
        layout: vertical;
    }
    #body {
        layout: horizontal;
        height: 1fr;
    }
    #stage-table {
        width: 68%;
    }
    """

    def __init__(self, disk: DiskInfo) -> None:
        super().__init__()
        self.disk = disk
        self._statuses: dict[str, StageStatus] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="stage-table", cursor_type="row", zebra_stripes=True)
            yield StageDetailPanel(_STAGE_DETAIL_PLACEHOLDER, id="detail-panel")
        yield SystemBar(disk=self.disk)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.disk.job_name
        table = self.query_one(DataTable)
        table.add_columns("#", "Stage", "Status", "Note", "Runtime")
        self.refresh_state()
        self.set_interval(10, self.refresh_state)

    @work(thread=True)
    def refresh_state(self) -> None:
        # Reload disk state (scan_runs, pgrep, etc.)
        from state import _populate
        _populate(self.disk)
        statuses = {s.key: get_stage_status(self.disk, s) for s in STAGES}
        self.app.call_from_thread(self._update_table, statuses)

    def _update_table(self, statuses: dict[str, StageStatus]) -> None:
        self._statuses = statuses
        table = self.query_one(DataTable)
        prev_row = table.cursor_row
        table.clear()

        done = sum(1 for v in statuses.values() if v == StageStatus.DONE)
        self.sub_title = f"{self.disk.job_name}  [{done}/{len(STAGES)} done]"

        for stage in STAGES:
            st = statuses[stage.key]
            char, style = ICON[st]
            icon = Text(char, style=style)
            num = Text(str(stage.number), style="dim")
            name_text = Text(stage.name)
            if stage.is_optional:
                name_text.append(" (opt)", style="dim italic")
            note = get_stage_note(self.disk, stage, st)
            note_style = _note_style(st)
            note_text = Text(note, style=note_style) if note else Text("")
            table.add_row(num, name_text, icon, note_text, Text(stage.runtime_hint, style="dim"), key=stage.key)

        # Restore cursor or move to first non-done
        if prev_row < table.row_count:
            table.move_cursor(row=prev_row)
        else:
            self._jump_to_next()

    def _jump_to_next(self) -> None:
        """Move cursor to first pending/partial/failed/running stage."""
        table = self.query_one(DataTable)
        for i, stage in enumerate(STAGES):
            st = self._statuses.get(stage.key, StageStatus.PENDING)
            if st in (StageStatus.PENDING, StageStatus.PARTIAL, StageStatus.FAILED, StageStatus.RUNNING):
                table.move_cursor(row=i)
                return

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # row_key was set to stage.key when adding rows
        key = str(event.row_key.value) if event.row_key else ""
        stage = STAGE_BY_KEY.get(key)
        if not stage:
            # fallback: use cursor index
            idx = event.cursor_row
            if 0 <= idx < len(STAGES):
                stage = STAGES[idx]
        if stage:
            self._show_detail(stage)

    def _show_detail(self, stage: StageDef) -> None:
        panel = self.query_one("#detail-panel", StageDetailPanel)
        st = self._statuses.get(stage.key, StageStatus.PENDING)
        char, style = ICON[st]

        lines: list[str] = []
        lines.append(f"[bold]{stage.number}. {stage.name}[/bold]")
        lines.append(f"Status: [{style}]{char} {st.value}[/{style}]")
        lines.append("")

        # Description
        for ln in stage.description.splitlines():
            lines.append(ln if ln else "")

        lines.append("")
        lines.append("[bold]Command:[/bold]")
        if stage.script:
            cmd = command_display(self.disk, stage)
            lines.append(f"[dim on default]{cmd}[/dim on default]")
        else:
            lines.append("[dim](manual step)[/dim]")

        # Previous runs
        if stage.scan_run_key:
            runs = self.disk.all_runs(stage.scan_run_key)
            if runs:
                lines.append("")
                lines.append(f"[bold]Run history ({len(runs)}):[/bold]")
                for r in runs[-3:]:
                    ended = r.ended_at or "…"
                    lines.append(f"  [{_run_style(r.status)}]{r.status}[/{_run_style(r.status)}]  {r.started_at[:16]} → {ended[:16]}")
                    if r.notes:
                        lines.append(f"  [dim]{r.notes}[/dim]")
                    if r.log_path:
                        lines.append(f"  log: [dim]{r.log_path}[/dim]")

        # Log file (latest run)
        latest = self.disk.latest_run(stage.scan_run_key) if stage.scan_run_key else None
        if latest and latest.log_path:
            lines.append("")
            lines.append(f"[bold]Log:[/bold] {latest.log_path}")
        if latest and latest.output_dir:
            lines.append(f"[bold]Output:[/bold] {latest.output_dir}")

        # Warning
        if stage.warning:
            lines.append("")
            lines.append(f"[bold red]⚠ {stage.warning}[/bold red]")

        panel.update("\n".join(lines))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_run_stage()

    def _selected_stage(self) -> StageDef | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        # Rows are added in STAGES order; cursor_row is a direct index.
        idx = table.cursor_row
        if 0 <= idx < len(STAGES):
            return STAGES[idx]
        return None

    def action_run_stage(self) -> None:
        stage = self._selected_stage()
        if not stage:
            return
        st = self._statuses.get(stage.key, StageStatus.PENDING)

        if stage.is_manual:
            self.app.notify(
                f"Manual step: {stage.name}\n\nSee detail panel for instructions.",
                title="Manual Step", severity="information",
            )
            return

        if stage.is_view_only and not stage.script:
            self.app.notify(
                "This step requires manual action. See instructions in the detail panel.",
                severity="information",
            )
            return

        self._confirm_and_run(stage, st)

    @work
    async def _confirm_and_run(self, stage: StageDef, st: StageStatus) -> None:
        from screens.confirm import ConfirmScreen
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(self.disk, stage, st)
        )
        if confirmed:
            from screens.log_viewer import LogViewerScreen
            self.app.push_screen(LogViewerScreen(self.disk, stage))

    def action_view_log(self) -> None:
        stage = self._selected_stage()
        if not stage or not stage.scan_run_key:
            self.app.notify("No log available for this stage.", severity="warning")
            return
        run = self.disk.latest_run(stage.scan_run_key)
        if not run or not run.log_path:
            self.app.notify("No log file found for the latest run.", severity="warning")
            return
        from screens.log_viewer import LogViewerScreen
        self.app.push_screen(LogViewerScreen(self.disk, stage, log_path=run.log_path))

    def action_tail_running(self) -> None:
        """Jump straight to the log of whichever stage is currently running."""
        running_stage = None
        running_run = None
        for s in STAGES:
            if self._statuses.get(s.key) == StageStatus.RUNNING and s.scan_run_key:
                run = self.disk.latest_run(s.scan_run_key)
                if run and run.log_path:
                    running_stage = s
                    running_run = run
                    break
        if not running_stage or not running_run:
            self.app.notify("No running stage with a known log found.", severity="warning")
            return
        from screens.log_viewer import LogViewerScreen
        self.app.push_screen(
            LogViewerScreen(self.disk, running_stage, log_path=running_run.log_path)
        )

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.refresh_state()


def _run_style(status: str) -> str:
    return {"ok": "green", "partial": "yellow", "failed": "bold red", "running": "cyan"}.get(status, "dim")


def _note_style(status: StageStatus) -> str:
    return {
        StageStatus.SKIPPED: "dim",
        StageStatus.RUNNING: "cyan",
        StageStatus.PARTIAL: "yellow",
        StageStatus.FAILED:  "bold red",
        StageStatus.PENDING: "dim",
        StageStatus.DONE:    "dim",
    }.get(status, "dim")
