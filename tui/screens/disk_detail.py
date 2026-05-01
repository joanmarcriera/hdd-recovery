"""Per-disk stage checklist with detail panel."""
from __future__ import annotations

import time
from typing import Optional

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from executor import build_command, command_display, has_concurrent_db_writer
from monitor import SystemBar
from stages import STAGES, StageDef, STAGE_BY_KEY, clean_markup
from state import (
    DiskInfo, StageStatus, ICON,
    count_done, fmt_bytes, get_stage_status, get_stage_note,
    fetch_smart_attrs, parse_rate_log, fmt_elapsed,
)

_DDRESCUE_KEYS = frozenset({
    "ddrescue-first", "ddrescue-retry", "ddrescue-reverse", "ddrescue-retrim",
})

_STAGE_DETAIL_PLACEHOLDER = (
    "[dim]Select a stage to see details.\n\n"
    "  Enter  run / preview\n"
    "  W      write command to /tmp/hdd_stage_cmd.sh\n"
    "  L      view log of selected stage\n"
    "  T      tail log of currently running stage\n"
    "  N      edit notes for this disk\n"
    "  B      back to dashboard\n"
    "  R      refresh state[/dim]"
)


def _progress_bar(pct: float, width: int = 30) -> str:
    filled = int(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}]"


def _eta_from_history(
    history: list[tuple[float, float]], current_pct: float
) -> Optional[str]:
    """Estimate time remaining from a coverage-vs-time history."""
    if len(history) < 2:
        return None
    # Use oldest and newest points that are at least 30 seconds apart
    for i in range(len(history) - 1, -1, -1):
        t0, p0 = history[i]
        t1, p1 = history[-1]
        if t1 - t0 >= 30:
            dp = p1 - p0
            dt = t1 - t0
            if dp <= 0:
                return None
            rate_pct_per_s = dp / dt
            remaining_pct = 100.0 - current_pct
            if remaining_pct <= 0:
                return "done"
            secs = int(remaining_pct / rate_pct_per_s)
            return fmt_elapsed(secs)
    return None


def _eta_from_rate_log(disk: DiskInfo) -> Optional[str]:
    """Parse the ddrescue rate log to compute ETA from rate and rescued bytes."""
    if not disk.rate_log_path or not disk.rate_log_path.exists():
        return None
    info = parse_rate_log(disk.rate_log_path)
    if not info or info["rate_bps"] <= 0:
        return None
    # Use image size or conf size to compute remaining
    total = disk.source_size_bytes or disk.image_size_bytes
    if not total:
        return None
    remaining = total - info["rescued_bytes"]
    if remaining <= 0:
        return "done"
    secs = int(remaining / info["rate_bps"])
    rate_mb = info["rate_bps"] / 1_048_576
    return f"{fmt_elapsed(secs)}  ({rate_mb:.0f} MB/s)"


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
        Binding("n",      "open_notes",   "Notes"),
        Binding("w",      "write_cmd",    "Write cmd → /tmp"),
        Binding("q",      "app.quit",     "Quit"),
    ]

    CSS = """
    DiskDetailScreen { layout: vertical; }
    #body { layout: horizontal; height: 1fr; }
    #stage-table { width: 68%; }
    """

    def __init__(self, disk: DiskInfo) -> None:
        super().__init__()
        self.disk = disk
        self._statuses: dict[str, StageStatus] = {}
        # Coverage history for ETA: list of (monotonic_time, coverage_pct)
        self._coverage_history: list[tuple[float, float]] = []
        # Cached SMART output (fetch_time, text)
        self._smart_cache: tuple[float, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="stage-table", cursor_type="row", zebra_stripes=True)
            yield StageDetailPanel(_STAGE_DETAIL_PLACEHOLDER, id="detail-panel")
        yield SystemBar(disk=self.disk)
        yield Footer()

    async def on_mount(self) -> None:
        self.sub_title = self.disk.job_name
        table = self.query_one(DataTable)
        table.add_columns("#", "Stage", "Status", "Note", "Runtime")
        self.refresh_state()
        self.set_interval(10, self.refresh_state)

    @work(thread=True)
    def refresh_state(self) -> None:
        from state import _populate
        _populate(self.disk)
        statuses = {s.key: get_stage_status(self.disk, s) for s in STAGES}

        # Update coverage history if ddrescue is running
        ddrescue_running = any(
            statuses.get(k) == StageStatus.RUNNING for k in _DDRESCUE_KEYS
        )
        if ddrescue_running and self.disk.map_coverage_pct is not None:
            self.app.call_from_thread(
                self._append_coverage, self.disk.map_coverage_pct
            )

        # Refresh SMART cache if ddrescue is running and we have a source device
        if ddrescue_running and self.disk.source_dev:
            now = time.monotonic()
            if self._smart_cache is None or (now - self._smart_cache[0]) > 60:
                smart_text = fetch_smart_attrs(self.disk.source_dev)
                self._smart_cache = (now, smart_text)

        self.app.call_from_thread(self._update_table, statuses)

    def _append_coverage(self, pct: float) -> None:
        self._coverage_history.append((time.monotonic(), pct))
        self._coverage_history = self._coverage_history[-20:]

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
            note_text = Text(note, style=_note_style(st)) if note else Text("")
            table.add_row(num, name_text, icon, note_text, Text(stage.runtime_hint, style="dim"), key=stage.key)

        if prev_row < table.row_count:
            table.move_cursor(row=prev_row)
        else:
            self._jump_to_next()

    def _jump_to_next(self) -> None:
        table = self.query_one(DataTable)
        for i, stage in enumerate(STAGES):
            st = self._statuses.get(stage.key, StageStatus.PENDING)
            if st in (StageStatus.PENDING, StageStatus.PARTIAL, StageStatus.FAILED, StageStatus.RUNNING):
                table.move_cursor(row=i)
                return

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value) if event.row_key else ""
        stage = STAGE_BY_KEY.get(key)
        if not stage:
            idx = event.cursor_row
            if 0 <= idx < len(STAGES):
                stage = STAGES[idx]
        if stage:
            self._show_detail(stage)

    def _show_detail(self, stage: StageDef) -> None:  # noqa: C901
        panel = self.query_one("#detail-panel", StageDetailPanel)
        st = self._statuses.get(stage.key, StageStatus.PENDING)
        char, style = ICON[st]

        lines: list[str] = []
        lines.append(f"[bold]{stage.number}. {stage.name}[/bold]")
        lines.append(f"Status: [{style}]{char} {st.value}[/{style}]")
        lines.append("")

        for ln in stage.description.splitlines():
            lines.append(ln if ln else "")

        # ── ddrescue progress bar + ETA ────────────────────────────────────
        if stage.key in _DDRESCUE_KEYS and st == StageStatus.RUNNING:
            pct = self.disk.map_coverage_pct
            if pct is not None:
                bar = _progress_bar(pct)
                lines.append("")
                lines.append(f"[bold]Progress:[/bold] {bar}  {pct:.2f}%")
                # Try rate log first, then history-based ETA
                eta = _eta_from_rate_log(self.disk) or _eta_from_history(
                    self._coverage_history, pct
                )
                if eta:
                    lines.append(f"[bold]ETA:[/bold] ~{eta}")

            # SMART attributes
            if self.disk.source_dev and self._smart_cache:
                _, smart_text = self._smart_cache
                if smart_text:
                    lines.append("")
                    lines.append(f"[bold]SMART:[/bold]  {smart_text}")

        lines.append("")
        lines.append("[bold]Command:[/bold]")
        if stage.script:
            cmd = command_display(self.disk, stage)
            lines.append(f"[dim on default]{escape(cmd)}[/dim on default]")
        else:
            lines.append("[dim](manual step — use Enter to open wizard / instructions)[/dim]")

        # Previous runs
        if stage.scan_run_key:
            runs = self.disk.all_runs(stage.scan_run_key)
            if runs:
                lines.append("")
                lines.append(f"[bold]Run history ({len(runs)}):[/bold]")
                for r in runs[-3:]:
                    ended = r.ended_at or "…"
                    lines.append(
                        f"  [{_run_style(r.status)}]{r.status}[/{_run_style(r.status)}]"
                        f"  {r.started_at[:16]} → {ended[:16]}"
                    )
                    if r.notes:
                        lines.append(f"  [dim]{escape(r.notes)}[/dim]")
                    if r.log_path:
                        lines.append(f"  log: [dim]{escape(r.log_path)}[/dim]")

        latest = self.disk.latest_run(stage.scan_run_key) if stage.scan_run_key else None
        if latest and latest.log_path:
            lines.append("")
            lines.append(f"[bold]Log:[/bold] {escape(latest.log_path)}")
        if latest and latest.output_dir:
            lines.append(f"[bold]Output:[/bold] {escape(latest.output_dir)}")

        if stage.warning:
            lines.append("")
            lines.append(f"[bold red]⚠ {escape(stage.warning)}[/bold red]")

        panel.update(clean_markup("\n".join(lines)))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_run_stage()

    def _selected_stage(self) -> StageDef | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        idx = table.cursor_row
        if 0 <= idx < len(STAGES):
            return STAGES[idx]
        return None

    def action_run_stage(self) -> None:
        stage = self._selected_stage()
        if not stage:
            return
        st = self._statuses.get(stage.key, StageStatus.PENDING)

        # Create-job-config → open wizard instead of manual instructions
        if stage.key == "create-job-config":
            from screens.wizard import WizardScreen
            self.app.push_screen(WizardScreen())
            return

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

    def action_open_notes(self) -> None:
        from screens.notes import NotesScreen
        self.app.push_screen(NotesScreen(self.disk))

    def action_write_cmd(self) -> None:
        """Write the selected stage's command to /tmp/hdd_stage_cmd.sh."""
        stage = self._selected_stage()
        if not stage or not stage.script:
            self.app.notify("No runnable command for this stage.", severity="warning")
            return
        from executor import command_display
        import os
        cmd = command_display(self.disk, stage)
        out_path = "/tmp/hdd_stage_cmd.sh"
        try:
            with open(out_path, "w") as f:
                f.write(f"#!/usr/bin/env bash\n{cmd}\n")
            os.chmod(out_path, 0o755)
            self.app.notify(
                f"Saved to {out_path}\n\nRun in another terminal:\n  bash {out_path}",
                title=f"Stage {stage.number}: {stage.name}",
                timeout=10,
            )
        except OSError as e:
            self.app.notify(f"Failed to write {out_path}: {e}", severity="error")

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
