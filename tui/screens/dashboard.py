"""Multi-disk overview dashboard."""
from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from monitor import SystemBar
from state import (
    DiskInfo, StageStatus, ICON,
    count_done, discover_disks, fmt_bytes, get_stage_status, next_pending_stage,
)
from stages import TOTAL_STAGES


class DashboardScreen(Screen):
    BINDINGS = [
        Binding("r",      "refresh",     "Refresh"),
        Binding("enter",  "open_disk",   "Open disk"),
        Binding("n",      "new_disk",    "New disk"),
        Binding("w",      "web_server",  "Web server"),
        Binding("k",      "backup",      "Backup"),
        Binding("q",      "app.quit",    "Quit"),
    ]

    CSS = """
    DashboardScreen {
        align: center middle;
    }
    DataTable {
        height: 1fr;
    }
    #subtitle {
        dock: top;
        padding: 0 2;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._disks: list[DiskInfo] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("Loading…", id="subtitle")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield SystemBar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "Disk / Job", "Image", "Map", "Progress", "Next step")
        self.load_disks()
        self.set_interval(15, self.action_refresh)

    @work(thread=True)
    def load_disks(self) -> None:
        disks = discover_disks()
        self.app.call_from_thread(self._update_table, disks)

    def _update_table(self, disks: list[DiskInfo]) -> None:
        self._disks = disks
        self.query_one(SystemBar).update_disks(disks)
        table = self.query_one(DataTable)
        table.clear()

        from screens.webserver import server_pid, display_url, _DEFAULT_HOST, _DEFAULT_PORT
        subtitle = self.query_one("#subtitle", Label)
        pid = server_pid()
        web_hint = (f"  │  [green]Web UI ●[/green] {escape(display_url(_DEFAULT_HOST, _DEFAULT_PORT))}"
                    if pid else "")
        if not disks:
            subtitle.update(f"No disks found — N to add, W web server, K backup{web_hint}")
            return
        subtitle.update(f"{len(disks)} disk(s) — Enter open  N new  W web  K backup{web_hint}")

        for i, disk in enumerate(disks):
            done = count_done(disk)
            nxt = next_pending_stage(disk)

            # Overall status icon: running > failed > partial > done > pending
            from stages import STAGES
            from datetime import datetime, timezone
            statuses = [get_stage_status(disk, s) for s in STAGES]
            if StageStatus.RUNNING in statuses:
                overall = StageStatus.RUNNING
            elif StageStatus.FAILED in statuses:
                overall = StageStatus.FAILED
            elif StageStatus.PARTIAL in statuses:
                overall = StageStatus.PARTIAL
            elif done == TOTAL_STAGES:
                overall = StageStatus.DONE
            else:
                overall = StageStatus.PENDING

            char, style = ICON[overall]
            icon_cell = Text(char, style=style)

            image_cell = (
                Text(fmt_bytes(disk.image_size_bytes), style="green")
                if disk.image_exists
                else Text("✗ missing", style="red")
            )

            if disk.map_coverage_pct is not None:
                pct = disk.map_coverage_pct
                map_cell = Text(
                    f"{pct:.1f}%",
                    style="green" if pct >= 99.9 else "yellow",
                )
            elif disk.map_exists:
                map_cell = Text("exists", style="dim")
            else:
                map_cell = Text("—", style="dim")

            n_partial = sum(1 for s in statuses if s == StageStatus.PARTIAL)
            n_failed  = sum(1 for s in statuses if s == StageStatus.FAILED)
            progress_text = f"{done}/{TOTAL_STAGES}"
            if n_partial:
                progress_text += f"  ~{n_partial}"
            if n_failed:
                progress_text += f"  ✗{n_failed}"
            progress_cell = Text(
                progress_text,
                style="green" if done == TOTAL_STAGES else "default",
            )

            # "Next step" cell: prefer the running stage with elapsed time over
            # the next pending stage — tells the operator what is happening NOW.
            next_cell_text = "All done ✓"
            next_cell_style = "green"
            running_stage = next(
                (STAGES[j] for j, s in enumerate(statuses) if s == StageStatus.RUNNING),
                None,
            )
            if running_stage:
                run = disk.latest_run(running_stage.scan_run_key) if running_stage.scan_run_key else None
                elapsed = ""
                if run and run.started_at:
                    try:
                        dt = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
                        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
                        elapsed = f"  {fmt_elapsed(secs)}"
                    except Exception:
                        pass
                next_cell_text  = f"⟳ {running_stage.number}. {running_stage.name}{elapsed}"
                next_cell_style = "bold cyan"
            elif nxt:
                next_cell_text  = f"{nxt.number}. {nxt.name}"
                next_cell_style = "cyan"
            next_cell = Text(next_cell_text, style=next_cell_style)

            table.add_row(
                icon_cell,
                Text(disk.display_name),
                image_cell,
                map_cell,
                progress_cell,
                next_cell,
                key=str(i),
            )

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_disk()

    def action_open_disk(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return
        row_key = table.cursor_row
        if 0 <= row_key < len(self._disks):
            from screens.disk_detail import DiskDetailScreen
            self.app.push_screen(DiskDetailScreen(self._disks[row_key]))

    def action_refresh(self) -> None:
        self.query_one("#subtitle", Label).update("Refreshing…")
        self.load_disks()

    def action_new_disk(self) -> None:
        from screens.wizard import WizardScreen
        self.app.push_screen(WizardScreen())

    def action_web_server(self) -> None:
        from screens.webserver import WebServerScreen
        self.app.push_screen(WebServerScreen())

    def action_backup(self) -> None:
        from screens.backup import BackupScreen
        self.app.push_screen(BackupScreen())
