"""Pantalla principal del menú. Solo dispatch + render, sin lógica de dominio."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from ..menu import MAIN_MENU, MenuItem

_SUBTITLE_MARKUP: Final[str] = (
    "[dim]Generación de avatares con lip-sync · Upload + ElevenLabs + Kling Avatar Pro[/dim]"
)
_HINT_TEXT: Final[str] = "↑/↓ moverse · Enter seleccionar · letra = atajo directo · ? ayuda"
_HELP_TIMEOUT_SECONDS: Final[int] = 6

OnSelect = Callable[[str], None]


def _format_option(item: MenuItem) -> str:
    """Renderiza un item del menú con atajo + icono + label + sufijo dim si pendiente.

    Patrón visual: `[N]  🎬  Nuevo video  (pronto)` donde:
    - `[N]` = atajo de teclado, bracketed para escanear rápido.
    - `🎬`  = icono temático (emoji o glyph Unicode).
    - Label = nombre legible.
    - `(pronto)` en `dim` = sufijo solo para items con `pending_message` (placeholder).
    """
    base = f"  [b]\\[{item.hotkey}][/b]  {item.icon}  {item.label}"
    if item.pending_message:
        return f"[dim]{base}  (pronto)[/dim]"
    return base


class MainMenuScreen(Screen[None]):
    """Pantalla raíz. Delega la acción al `on_select` que inyecta la `App`.

    La pantalla solo emite el `id` del item elegido; resolver el `MenuItem`
    completo es responsabilidad de la `App` (SRP / CR-2.1). Así evitamos
    duplicar el lookup que ya vive en `ui.menu.MENU_BY_ID` (CR-3.7).
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("question_mark", "show_help", "Ayuda", key_display="?"),
    ]

    def __init__(self, on_select: OnSelect) -> None:
        super().__init__()
        self._on_select = on_select

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="menu-box"):
            yield Static(_SUBTITLE_MARKUP, id="title")
            yield OptionList(
                *[Option(_format_option(item), id=item.id) for item in MAIN_MENU],
                id="menu",
            )
            yield Static(_HINT_TEXT, id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#menu", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._on_select(event.option.id)

    def action_select_by_id(self, item_id: str) -> None:
        """Action expuesta a los bindings registrados a nivel App (atajos N/B/G/...)."""
        self._on_select(item_id)

    def action_show_help(self) -> None:
        shortcuts = " · ".join(f"{item.hotkey} {item.label}" for item in MAIN_MENU)
        self.notify(
            f"↑/↓ moverse · Enter seleccionar · {shortcuts}",
            title="Ayuda",
            timeout=_HELP_TIMEOUT_SECONDS,
        )
