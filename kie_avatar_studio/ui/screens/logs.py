"""Pantalla `Logs`: muestra las últimas líneas del archivo de log.

Solo dispatch + render (CR-10.1). Recibe un `LogReader` inyectado y refresca
el contenido bajo demanda (botón "Recargar") o automáticamente cada N segundos.
"""

from __future__ import annotations

from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, Footer, Header, RichLog, Static

from ...app_layer.log_reader import LogReader

_REFRESH_INTERVAL_SECONDS: Final[float] = 2.0
_MAX_LINES: Final[int] = 500


class LogsScreen(Screen[None]):
    """Tail del archivo de log con auto-refresh."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
        Binding("r", "reload", "Recargar"),
        Binding("space", "toggle_auto", "Auto"),
    ]

    def __init__(self, log_reader: LogReader) -> None:
        super().__init__()
        self._reader = log_reader
        self._auto_refresh = True
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="logs-box"):
            yield Static(
                f"[b]Logs[/b]  [dim]{self._reader.path}[/dim]",
                id="logs-title",
            )
            yield RichLog(id="logs-view", highlight=False, markup=False, wrap=False)
            with Horizontal(classes="actions-row"):
                yield Button("Recargar (R)", id="logs-reload", variant="primary")
                yield Button("Pausar auto (espacio)", id="logs-toggle", classes="btn-warning")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()
        self._timer = self.set_interval(_REFRESH_INTERVAL_SECONDS, self._auto_refresh_tick)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "logs-reload":
            await self.action_reload()
        elif event.button.id == "logs-toggle":
            self.action_toggle_auto()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    async def action_reload(self) -> None:
        await self._refresh()

    def action_toggle_auto(self) -> None:
        self._auto_refresh = not self._auto_refresh
        status = "ON" if self._auto_refresh else "OFF"
        self.notify(f"Auto-refresh: {status}", timeout=2)

    async def _auto_refresh_tick(self) -> None:
        if self._auto_refresh:
            await self._refresh()

    async def _refresh(self) -> None:
        lines = await self._reader.tail(_MAX_LINES)
        view = self.query_one("#logs-view", RichLog)
        view.clear()
        if not lines:
            view.write("(archivo vacío o no creado todavía)")
            return
        for line in lines:
            view.write(line)
