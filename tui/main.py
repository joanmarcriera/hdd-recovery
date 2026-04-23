"""HDD Recovery TUI — entry point."""
from textual.app import App, ComposeResult

from screens.dashboard import DashboardScreen


class RecoveryApp(App):
    TITLE = "HDD Recovery"
    CSS = """
    Screen {
        background: $background;
    }
    Header {
        background: $primary-darken-2;
    }
    """

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())


def main() -> None:
    RecoveryApp().run()


if __name__ == "__main__":
    main()
