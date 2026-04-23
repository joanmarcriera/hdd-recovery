"""Confirmation modal — shows command verbatim, asks Y/N before running."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from executor import command_display, has_concurrent_db_writer
from stages import StageDef
from state import DiskInfo, StageStatus, ICON


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y",      "confirm",  "Run"),
        Binding("n",      "cancel",   "Cancel"),
        Binding("escape", "cancel",   "Cancel"),
    ]

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #dialog {
        width: 80;
        max-width: 90%;
        max-height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #title {
        text-style: bold;
        margin-bottom: 1;
    }
    #description {
        margin-bottom: 1;
        color: $text-muted;
    }
    #command-box {
        background: $panel;
        border: solid $surface-lighten-1;
        padding: 0 1;
        margin-bottom: 1;
        overflow-x: auto;
    }
    #warning-box {
        background: $error 15%;
        border: solid $error;
        padding: 0 1;
        margin-bottom: 1;
        color: $error;
    }
    #prev-run {
        margin-bottom: 1;
        color: $text-muted;
    }
    #blocker {
        color: $error;
        margin-bottom: 1;
    }
    #buttons {
        layout: horizontal;
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    Button {
        margin: 0 1;
    }
    """

    def __init__(self, disk: DiskInfo, stage: StageDef, current_status: StageStatus) -> None:
        super().__init__()
        self.disk = disk
        self.stage = stage
        self.current_status = current_status
        self._blocker: str | None = None

    def compose(self) -> ComposeResult:
        stage = self.stage
        disk = self.disk

        char, style = ICON[self.current_status]

        # Safety check
        if stage.requires_db_write:
            self._blocker = has_concurrent_db_writer(disk, exclude_stage_key=stage.key)

        with Vertical(id="dialog"):
            yield Label(f"Stage {stage.number}: {stage.name}", id="title")

            # Current status
            yield Static(f"Current status: [{style}]{char} {self.current_status.value}[/{style}]")

            yield Static("")

            # Description
            yield Static(stage.description, id="description")

            # Command
            yield Static("[bold]Command to run:[/bold]")
            cmd = command_display(disk, stage)
            yield Static(cmd, id="command-box")

            # Previous runs
            if stage.scan_run_key:
                runs = disk.all_runs(stage.scan_run_key)
                if runs:
                    latest = runs[-1]
                    ended = latest.ended_at or "still running"
                    note = f"  note: {latest.notes}" if latest.notes else ""
                    prev_text = (
                        f"Previous run: [{'green' if latest.status == 'ok' else 'yellow'}]"
                        f"{latest.status}[/{'green' if latest.status == 'ok' else 'yellow'}]  "
                        f"{latest.started_at[:16]} → {ended[:16]}{note}"
                    )
                    yield Static(prev_text, id="prev-run")

            # Warning
            if stage.warning:
                yield Static(f"⚠  {stage.warning}", id="warning-box")

            # Blocker
            if self._blocker:
                yield Static(
                    f"⛔  Cannot start: [{self._blocker}] is currently writing to the DB. "
                    f"Wait for it to finish first.",
                    id="blocker",
                )

            # Runtime
            yield Static(f"Estimated runtime: [dim]{stage.runtime_hint}[/dim]")
            if stage.rerunnable:
                yield Static("[dim]Safe to re-run: yes (backs up previous output)[/dim]")

            if stage.auto_stdin:
                yield Static("[dim]Note: the ddrescue --ask prompt will be auto-confirmed.[/dim]")

            with Center(id="buttons"):
                if self._blocker:
                    yield Button("Cancel [N]", variant="default", id="btn-cancel")
                else:
                    yield Button("Run  [Y]", variant="success", id="btn-run")
                    yield Button("Cancel  [N]", variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-run":
            self.action_confirm()
        else:
            self.action_cancel()

    def action_confirm(self) -> None:
        if self._blocker:
            return
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
