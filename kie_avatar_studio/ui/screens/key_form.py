"""Modal para agregar (o renombrar) una `KieKey`.

Solo dispatch + render. Valida sintaxis localmente con `policies` y delega la
persistencia a quien la abrió devolviendo el resultado vía `dismiss(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ...app_layer.ids import sanitize_filename
from ...domain.errors import KeyValidationError
from ...domain.policies import validate_key_label, validate_kie_key

_PASSWORD_MASK: Final[bool] = True


@dataclass(frozen=True, slots=True)
class KeyFormResult:
    """Datos devueltos al cerrar el modal con guardar."""

    id: str
    label: str
    key: str


class KeyFormScreen(ModalScreen[KeyFormResult | None]):
    """Modal con dos inputs: label + key. Devuelve `KeyFormResult` o `None`."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(
        self,
        *,
        title: str = "Agregar API key",
        initial_label: str = "",
        initial_key: str = "",
    ) -> None:
        super().__init__()
        self._title = title
        self._initial_label = initial_label
        self._initial_key = initial_key

    def compose(self) -> ComposeResult:
        with Vertical(id="key-form-dialog"):
            yield Static(self._title, id="key-form-title")
            yield Label("Label legible (ej. 'cuenta personal')")
            yield Input(value=self._initial_label, placeholder="cuenta personal", id="label")
            yield Label("API key (se guarda local, nunca se loguea)")
            yield Input(
                value=self._initial_key,
                placeholder="sk-...",
                id="key",
                password=_PASSWORD_MASK,
            )
            yield Static("", id="key-form-error")
            with Horizontal(classes="actions-row"):
                yield Button("Cancelar", id="cancel", variant="default")
                yield Button("Guardar", id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#label", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "save":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        label = self.query_one("#label", Input).value
        key = self.query_one("#key", Input).value
        try:
            validate_key_label(label)
            validate_kie_key(key)
        except KeyValidationError as exc:
            self.query_one("#key-form-error", Static).update(str(exc))
            return
        key_id = sanitize_filename(label.strip()).lower()
        self.dismiss(KeyFormResult(id=key_id, label=label.strip(), key=key))
