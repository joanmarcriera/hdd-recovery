"""Backup control screen — configure and trigger recovery-backup.sh from the TUI."""
from __future__ import annotations

import subprocess

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from config import BIN_DIR

_DEFAULT_SRC = "/mnt/recovery16tb/recovery"
_DEFAULT_DST = "/mnt/CryptoBackup/recovery"

# Any of these running → block real backup (they read/write image or DB)
_BUSY_PROCS = (
    "ddrescue", "safecopy", "bulk_extractor",
    "foremost", "scalpel", "photorec", "fiwalk",
)


def _busy_process() -> str | None:
    """Return the name of a running process that makes backup unsafe, or None."""
    try:
        r = subprocess.run(
            ["pgrep", "-af", "|".join(_BUSY_PROCS)],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            for proc in _BUSY_PROCS:
                if proc in line:
                    return line.split()[-1].split("/")[-1][:40]
    except Exception:
        pass
    return None


class BackupScreen(Screen):
    """Configure and run recovery-backup.sh; blocks while imaging is in progress."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("b", "go_back", "Back"),
    ]

    CSS = """
    BackupScreen { padding: 1 2; }
    .row { height: 3; margin-bottom: 1; }
    .lbl { width: 10; padding-top: 1; }
    #busy-warn { color: yellow; margin: 1 0; min-height: 2; }
    #out {
        height: 1fr;
        border: solid $surface;
        margin-top: 1;
        overflow-y: auto;
        padding: 0 1;
    }
    #btn-row { height: 3; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("[bold]Recovery Backup[/bold] — rsync to backup destination")
        with Horizontal(classes="row"):
            yield Label("Source", classes="lbl")
            yield Input(value=_DEFAULT_SRC, id="src")
        with Horizontal(classes="row"):
            yield Label("Dest", classes="lbl")
            yield Input(value=_DEFAULT_DST, id="dst")
        yield Static("", id="busy-warn")
        with Horizontal(id="btn-row"):
            yield Button("Dry run", variant="default", id="dryrun")
            yield Button("Run (DBs + logs)", variant="primary", id="run")
            yield Button("Run + exports", variant="primary", id="run-full")
            yield Button("Run + images", variant="warning", id="run-images")
        yield Static("[dim]Output appears here after the run completes.[/dim]", id="out")
        yield Footer()

    def on_mount(self) -> None:
        self._update_busy_warn()

    def _update_busy_warn(self) -> None:
        busy = _busy_process()
        warn = self.query_one("#busy-warn", Static)
        if busy:
            warn.update(
                f"⚠ [bold]{busy}[/bold] is currently running. "
                "Real backup is blocked until it finishes. Dry run is always safe."
            )
        else:
            warn.update("[green]No imaging processes detected — backup is safe to run.[/green]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        self._update_busy_warn()

        if btn != "dryrun":
            busy = _busy_process()
            if busy:
                self.app.notify(
                    f"Cannot start backup while {busy} is running.",
                    severity="warning",
                )
                return

        src = self.query_one("#src", Input).value.strip()
        dst = self.query_one("#dst", Input).value.strip()

        dry_run = (btn == "dryrun")
        extra: list[str] = []
        if btn in ("run-full", "run-images"):
            extra.append("--full")
        if btn == "run-images":
            extra.append("--images")

        self._run_backup(src, dst, dry_run, extra)

    @work(thread=True)
    def _run_backup(self, src: str, dst: str, dry_run: bool, extra: list[str]) -> None:
        cmd = [str(BIN_DIR / "recovery-backup.sh"), "--src", src, "--dst", dst]
        if not dry_run:
            cmd.append("--run")
        cmd.extend(extra)

        self.app.call_from_thread(
            self.query_one("#out", Static).update,
            f"[dim]$ {' '.join(cmd)}[/dim]\n\n[cyan]Running…[/cyan]",
        )
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=7200,
            )
            combined = (result.stdout + result.stderr).strip()
            # Keep last 4000 chars to avoid huge Static content
            if len(combined) > 4000:
                combined = "…(truncated)\n" + combined[-4000:]
            rc_note = "" if result.returncode == 0 else f"\n[bold red]Exit code: {result.returncode}[/bold red]"
            self.app.call_from_thread(
                self.query_one("#out", Static).update,
                (combined or "[dim](no output)[/dim]") + rc_note,
            )
        except subprocess.TimeoutExpired:
            self.app.call_from_thread(
                self.query_one("#out", Static).update,
                "[red]Timed out after 2 hours.[/red]",
            )
        except Exception as exc:
            self.app.call_from_thread(
                self.query_one("#out", Static).update,
                f"[red]Error: {exc}[/red]",
            )

    def action_go_back(self) -> None:
        self.app.pop_screen()
