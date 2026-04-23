"""Per-disk notes editor — read/add notes from the analysis DB."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from state import DiskInfo


class NotesScreen(Screen):
    """View and add timestamped notes for a disk (stored in notes table)."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("b", "go_back", "Back"),
    ]

    CSS = """
    NotesScreen { padding: 1 2; }
    #notes-list {
        height: 1fr;
        border: solid $surface;
        margin: 1 0;
    }
    #hint { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, disk: DiskInfo) -> None:
        super().__init__()
        self.disk = disk

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label(f"[bold]Notes — {self.disk.job_name}[/bold]")
        yield ListView(id="notes-list")
        yield Label("New note (Enter to save, Escape to go back):", id="hint")
        yield Input(placeholder="Type a note and press Enter…", id="note-input")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.disk.job_name
        self._load_notes()
        self.query_one("#note-input", Input).focus()

    def _load_notes(self) -> None:
        lv = self.query_one("#notes-list", ListView)
        lv.clear()
        if not self.disk.db_exists:
            lv.append(ListItem(Label("[yellow]DB not initialised yet — run init-db first.[/yellow]")))
            return
        notes = _read_notes(self.disk.db_path)
        if not notes:
            lv.append(ListItem(Label("[dim]No notes yet.[/dim]")))
            return
        for ts, text in notes:
            lv.append(ListItem(Label(f"[dim]{ts[:16]}[/dim]  {text}")))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if not self.disk.db_exists:
            self.app.notify("Cannot save — DB not initialised yet.", severity="warning")
            return
        _save_note(self.disk.db_path, text)
        event.input.value = ""
        self._load_notes()
        self.app.notify("Note saved.", severity="information")

    def action_go_back(self) -> None:
        self.app.pop_screen()


def _read_notes(db_path: Path) -> list[tuple[str, str]]:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        rows = conn.execute(
            "SELECT created_at, note FROM notes ORDER BY id"
        ).fetchall()
        conn.close()
        return [(str(r[0]), str(r[1])) for r in rows]
    except Exception:
        return []


def _save_note(db_path: Path, text: str) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("INSERT INTO notes (created_at, note) VALUES (?, ?)", (now, text))
    conn.commit()
    conn.close()
