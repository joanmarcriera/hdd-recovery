"""Config modal for LLM photo tagging — builds the image-tag-photos.py command."""
from __future__ import annotations

import os
import shlex
from functools import partial

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from config import BIN_DIR
from ollama import HostStatus, any_reachable, probe_hosts
from stages import StageDef
from state import DiskInfo

_DEFAULT_OLLAMA = os.environ.get("OLLAMA_HOSTS") or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_DEFAULT_MODEL = "llava:7b"
_SCOPES = [("real — JPEG ≥ 20 KB (default)", "real"), ("all — any image/* ≥ 10 KB", "all")]


class TagPhotosScreen(ModalScreen[list[str] | None]):
    """Configure and launch image-tag-photos.py."""

    BINDINGS = [
        Binding("y",      "confirm", "Run"),
        Binding("n",      "cancel",  "Cancel"),
        Binding("c",      "check",   "Check hosts"),
        Binding("escape", "cancel",  "Cancel"),
    ]

    CSS = """
    TagPhotosScreen {
        align: center middle;
    }
    #dialog {
        width: 84;
        max-width: 94%;
        max-height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #title { text-style: bold; margin-bottom: 1; }
    .row { height: 3; margin-bottom: 1; }
    .lbl { width: 14; padding-top: 1; }
    #cmd-box {
        background: $panel;
        border: solid $surface-lighten-1;
        padding: 0 1;
        margin: 1 0;
        overflow-x: auto;
    }
    #warning-box {
        background: $error 15%;
        border: solid $error;
        padding: 0 1;
        margin-bottom: 1;
        color: $error;
    }
    #buttons {
        layout: horizontal;
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    Button { margin: 0 1; }
    """

    def __init__(self, disk: DiskInfo, stage: StageDef) -> None:
        super().__init__()
        self.disk = disk
        self.stage = stage

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Stage {self.stage.number}: {self.stage.name}", id="title")

            with Horizontal(classes="row"):
                yield Label("Ollama URLs", classes="lbl")
                yield Input(value=_DEFAULT_OLLAMA, id="ollama")
            yield Static("", id="ollama-status")
            with Horizontal(classes="row"):
                yield Label("Workers", classes="lbl")
                yield Input(value="", placeholder="auto", id="workers")
            with Horizontal(classes="row"):
                yield Label("Model", classes="lbl")
                yield Input(value=_DEFAULT_MODEL, id="model")
            with Horizontal(classes="row"):
                yield Label("Scope", classes="lbl")
                yield Select(_SCOPES, value="real", id="scope")

            yield Checkbox("Force re-tag already-tagged images", value=False, id="force")
            yield Checkbox("Dry run (list candidates, no Ollama calls)", value=False, id="dry-run")

            yield Static("[bold]Command:[/bold]")
            yield Static("", id="cmd-box")

            yield Static(
                f"⚠  {escape(self.stage.warning)}", id="warning-box"
            )

            with Center(id="buttons"):
                yield Button("Run  [Y]", variant="success", id="btn-run")
                yield Button("Check  [C]", variant="primary", id="btn-check")
                yield Button("Cancel  [N]", variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        # Latest probe result; empty until the first probe completes. The run
        # gate only blocks when a completed probe shows every host down, so a
        # slow/failed probe never permanently locks the operator out.
        self._statuses: list[HostStatus] = []
        self._refresh_cmd()
        self.action_check()

    def action_check(self) -> None:
        raw = self.query_one("#ollama", Input).value.strip()
        status = self.query_one("#ollama-status", Static)
        if not raw:
            self._statuses = []
            status.update("[dim]enter an Ollama URL to check[/dim]")
            return
        status.update("[dim]checking Ollama hosts…[/dim]")
        # Network I/O off the UI thread; results applied back on the UI thread.
        self.run_worker(partial(self._probe_blocking, raw),
                        thread=True, exclusive=True, group="ollama-probe")

    def _probe_blocking(self, raw: str) -> None:
        statuses = probe_hosts(raw)
        self.app.call_from_thread(self._show_statuses, statuses)

    def _show_statuses(self, statuses: list[HostStatus]) -> None:
        self._statuses = statuses
        parts = []
        for s in statuses:
            if s.ok:
                parts.append(f"[green]✓ {escape(s.url)} ({escape(s.detail)})[/green]")
            else:
                parts.append(f"[red]✗ {escape(s.url)} ({escape(s.detail)})[/red]")
        self.query_one("#ollama-status", Static).update(
            "  ".join(parts) if parts else "[red]no Ollama hosts configured[/red]")

    def _build_cmd(self) -> list[str]:
        db = str(self.disk.db_path)
        ollama = self.query_one("#ollama", Input).value.strip()
        model = self.query_one("#model", Input).value.strip()
        workers = self.query_one("#workers", Input).value.strip()
        scope = self.query_one("#scope", Select).value
        force = self.query_one("#force", Checkbox).value
        dry_run = self.query_one("#dry-run", Checkbox).value

        cmd = ["python3", str(BIN_DIR / "image-tag-photos.py"), db]
        if ollama:
            cmd += ["--ollama", ollama]
        if model and model != _DEFAULT_MODEL:
            cmd += ["--model", model]
        if workers:
            cmd += ["--workers", workers]
        if scope and scope != "real":
            cmd += ["--scope", str(scope)]
        if force:
            cmd.append("--force")
        if dry_run:
            cmd.append("--dry-run")
        return cmd

    def _refresh_cmd(self) -> None:
        cmd = self._build_cmd()
        self.query_one("#cmd-box", Static).update(
            f"[dim on default]{escape(shlex.join(cmd))}[/dim on default]"
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_cmd()
        if event.input.id == "ollama":
            # URL edited — prior probe result is stale; require a re-check.
            self._statuses = []
            self.query_one("#ollama-status", Static).update(
                "[dim]press C to check host availability[/dim]")

    def on_select_changed(self, _event: Select.Changed) -> None:
        self._refresh_cmd()

    def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
        self._refresh_cmd()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-run":
            self.action_confirm()
        elif event.button.id == "btn-check":
            self.action_check()
        else:
            self.action_cancel()

    def action_confirm(self) -> None:
        ollama = self.query_one("#ollama", Input).value.strip()
        if not ollama:
            self.app.notify("Ollama URL is required.", severity="error")
            return
        # Block only when a completed probe proves every host is down — avoids
        # opening a scan_runs record that immediately fails on a dead host (#14).
        if self._statuses and not any_reachable(self._statuses):
            self.app.notify(
                "No Ollama host is reachable. Start Ollama or fix the URL, "
                "then press C to re-check.", severity="error")
            return
        self.dismiss(self._build_cmd())

    def action_cancel(self) -> None:
        self.dismiss(None)
