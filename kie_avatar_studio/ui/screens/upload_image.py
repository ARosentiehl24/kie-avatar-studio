"""Modal para subir una nueva imagen a Kie.

Solo dispatch + render (CR-10.1). Valida sintaxis localmente con `policies`
y devuelve `UploadImageFormResult` vía `dismiss(...)`. La persistencia +
upload corren en el caller, no acá.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from ...domain.errors import ImageValidationError, KeyValidationError
from ...domain.policies import validate_image_path, validate_key_label
from .file_picker import ImageFilePickerScreen


@dataclass(frozen=True, slots=True)
class UploadImageFormResult:
    label: str
    local_path: Path


class UploadImageFormScreen(ModalScreen[UploadImageFormResult | None]):
    """Modal con label + path. Devuelve `UploadImageFormResult` o `None`.

    El path se puede pegar a mano o elegir con el botón **Examinar…** que
    abre un file browser navegable (`ImageFilePickerScreen`).
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(self, *, default_dir: Path | None = None) -> None:
        super().__init__()
        self._default_dir = default_dir

    def compose(self) -> ComposeResult:
        with Vertical(id="image-form-dialog"):
            yield Static("Subir imagen a Kie", id="image-form-title")
            yield Label("Label legible (ej. 'modelo principal')")
            yield Input(placeholder="modelo principal", id="image-label")
            yield Label("Ruta local al archivo (png/jpg, máx 10 MB)")
            placeholder = (
                str(self._default_dir / "mi-imagen.png")
                if self._default_dir is not None
                else "/ruta/a/imagen.png"
            )
            with Horizontal(id="image-path-row"):
                yield Input(placeholder=placeholder, id="image-path")
                yield Button("Examinar…", id="browse", classes="btn-info")
            yield Static("", id="image-form-error")
            with Horizontal(classes="actions-row actions-row-save"):
                yield Button("Cancelar", id="cancel", variant="default")
                yield Button("Subir", id="upload", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#image-label", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "upload":
            self._submit()
        elif event.button.id == "browse":
            self._open_file_picker()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _open_file_picker(self) -> None:
        start = self._resolve_start_dir()
        self.app.push_screen(
            ImageFilePickerScreen(start_path=start),
            self._on_file_picked,
        )

    def _resolve_start_dir(self) -> Path | None:
        """Si el input ya tiene un path válido cuyo padre existe, abrimos ahí."""
        raw = self.query_one("#image-path", Input).value.strip()
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.is_file():
                return candidate.parent
            if candidate.is_dir():
                return candidate
            if candidate.parent.is_dir():
                return candidate.parent
        return self._default_dir

    def _on_file_picked(self, path: Path | None) -> None:
        if path is None:
            return
        self.query_one("#image-path", Input).value = str(path)

    def _submit(self) -> None:
        label = self.query_one("#image-label", Input).value
        raw_path = self.query_one("#image-path", Input).value
        try:
            validate_key_label(label)
        except KeyValidationError as exc:
            self._set_error(str(exc))
            return
        if not raw_path.strip():
            self._set_error("la ruta no puede estar vacía")
            return
        path = Path(raw_path).expanduser()
        try:
            validate_image_path(path)
        except ImageValidationError as exc:
            self._set_error(str(exc))
            return
        self.dismiss(UploadImageFormResult(label=label.strip(), local_path=path))

    def _set_error(self, message: str) -> None:
        self.query_one("#image-form-error", Static).update(message)
