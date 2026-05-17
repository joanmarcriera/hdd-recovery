"""Web UI server control — start/stop image-serve.py from the TUI."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from config import BIN_DIR, DB_ROOT

_PID_FILE = Path("/tmp/hdd-recovery-webui.pid")
_DEFAULT_ROOT = str(DB_ROOT) if str(DB_ROOT) else "/data/db"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = "17788"


def get_local_ip() -> str:
    """Return the machine's primary LAN IP for display (not the bind address)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def server_pid() -> int | None:
    """Return PID of a running web server process, or None."""
    try:
        pid = int(_PID_FILE.read_text().strip())
        os.kill(pid, 0)   # raises OSError if process does not exist
        return pid
    except Exception:
        return None


def server_url(host: str = _DEFAULT_HOST, port: str = _DEFAULT_PORT) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"http://{h}:{port}/"


def display_url(bind_host: str, port: str) -> str:
    """URL to show in the TUI — uses the real LAN IP even when bound to 0.0.0.0."""
    if bind_host in ("0.0.0.0", ""):
        host = get_local_ip()
    else:
        host = bind_host
    return server_url(host, port)


def start_server(host: str, port: str, root: str) -> int:
    proc = subprocess.Popen(
        [
            "python3",
            str(BIN_DIR / "image-serve.py"),
            "--host", host,
            "--port", port,
            "--root", root,
        ],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _PID_FILE.write_text(str(proc.pid))
    return proc.pid


def stop_server(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    _PID_FILE.unlink(missing_ok=True)


class WebServerScreen(Screen):
    """Configure and start/stop the read-only web query UI."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("b", "go_back", "Back"),
        Binding("r", "refresh_status", "Refresh"),
    ]

    CSS = """
    WebServerScreen { padding: 1 2; }
    #status {
        padding: 1;
        border: solid $surface;
        margin: 1 0;
        min-height: 3;
    }
    .row { height: 3; margin-bottom: 1; }
    .lbl { width: 12; padding-top: 1; }
    #lan-warn { color: yellow; margin-bottom: 1; }
    #btn-row { height: 3; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("[bold]Web UI Server[/bold] — read-only dashboard accessible on LAN")
        yield Static("", id="status")
        with Horizontal(classes="row"):
            yield Label("Host", classes="lbl")
            yield Input(value=_DEFAULT_HOST, id="host")
        with Horizontal(classes="row"):
            yield Label("Port", classes="lbl")
            yield Input(value=_DEFAULT_PORT, id="port")
        with Horizontal(classes="row"):
            yield Label("Root dir", classes="lbl")
            yield Input(value=_DEFAULT_ROOT, id="root")
        yield Static("", id="lan-warn")
        with Horizontal(id="btn-row"):
            yield Button("Start", variant="primary", id="start")
            yield Button("Stop", variant="error", id="stop")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        pid = server_pid()
        host = self.query_one("#host", Input).value.strip()
        port = self.query_one("#port", Input).value.strip()
        status = self.query_one("#status", Static)
        if pid:
            url = display_url(host, port)
            content = Text()
            content.append("● Running", style="green")
            content.append(f"  PID {pid}\nURL: ")
            content.append(url, style=f"link {url}")
            content.append("  (survives TUI exit)")
            status.update(content)
        else:
            status.update("[dim]○ Stopped[/dim]")

        warn = self.query_one("#lan-warn", Static)
        if host not in ("127.0.0.1", "::1", "localhost"):
            warn.update(
                "⚠ Listening on LAN — the analysis DB and SQL endpoint will be "
                "accessible to other hosts on the local network."
            )
        else:
            warn.update("")

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._refresh_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            pid = server_pid()
            if pid:
                self.app.notify(f"Already running (PID {pid})", severity="information")
                return
            host = self.query_one("#host", Input).value.strip()
            port = self.query_one("#port", Input).value.strip()
            root = self.query_one("#root", Input).value.strip()
            if not Path(root).is_dir():
                self.app.notify(f"Root dir not found: {root}", severity="error")
                return
            try:
                new_pid = start_server(host, port, root)
                self.app.notify(
                    f"Started (PID {new_pid})\n{display_url(host, port)}",
                    severity="information",
                )
            except Exception as exc:
                self.app.notify(f"Failed to start: {exc}", severity="error")
            self._refresh_status()

        elif event.button.id == "stop":
            pid = server_pid()
            if not pid:
                self.app.notify("Not running.", severity="information")
                return
            stop_server(pid)
            self.app.notify("Web server stopped.", severity="information")
            self._refresh_status()

    def action_refresh_status(self) -> None:
        self._refresh_status()

    def action_go_back(self) -> None:
        self.app.pop_screen()
