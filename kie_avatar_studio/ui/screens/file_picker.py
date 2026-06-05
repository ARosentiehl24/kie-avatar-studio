"""Modal para elegir un archivo navegando el filesystem.

Usa el widget `DirectoryTree` de Textual (sin deps externas). Filtramos a las
extensiones de imagen aceptadas por Kie. Devuelve `Path` o `None` vía dismiss.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Static

from ...domain.policies import IMAGE_EXTENSIONS

_INITIAL_PATH: Final[Path] = Path.home()


class _ImagesDirectoryTree(DirectoryTree):
    """`DirectoryTree` que oculta archivos que no son imágenes aceptadas por Kie."""

    def filter_paths(self, paths: Iterable[Path]) -> list[Path]:
        result: list[Path] = []
        for path in paths:
            name = path.name
            if name.startswith("."):
                # Ocultos: mantenemos como en cualquier file picker estándar.
                continue
            if path.is_dir():
                result.append(path)
                continue
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                result.append(path)
        return result


class ImageFilePickerScreen(ModalScreen[Path | None]):
    """Modal con file browser filtrado a imágenes."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(self, start_path: Path | None = None) -> None:
        super().__init__()
        # Si nos pasan un dir vacío o inexistente, caemos a HOME en lugar de fallar.
        candidate = start_path or _INITIAL_PATH
        self._start_path = candidate if candidate.is_dir() else _INITIAL_PATH
        self._selected: Path | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="file-picker-dialog"):
            yield Static(
                f"Elegí una imagen — [dim]{self._start_path}[/dim]",
                id="file-picker-title",
            )
            yield _ImagesDirectoryTree(str(self._start_path), id="file-picker-tree")
            yield Static("(seleccioná un archivo en el árbol)", id="file-picker-status")
            with Horizontal(classes="actions-row actions-row-save"):
                yield Button("Cancelar", id="cancel", variant="default")
                yield Button("Elegir", id="confirm", variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.query_one(_ImagesDirectoryTree).focus()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Doble-click / Enter en un archivo lo elige y cierra el modal."""
        self._selected = event.path
        self.dismiss(event.path)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        # Solo refrescamos el status para feedback; no auto-confirmamos directorios.
        self._selected = None
        self.query_one("#file-picker-status", Static).update(
            f"(directorio: {event.path} — abrí un archivo para elegirlo)"
        )
        self.query_one("#confirm", Button).disabled = True

    def on_directory_tree_node_highlighted(
        self,
        event: DirectoryTree.NodeHighlighted,  # type: ignore[type-arg] # Tree.NodeHighlighted[T] sin T expuesto fácilmente
    ) -> None:
        """Habilita el botón Elegir cuando el cursor está sobre un archivo."""
        data = event.node.data
        path: Path | None = getattr(data, "path", None) if data is not None else None
        if path is not None and path.is_file():
            self._selected = path
            self.query_one("#file-picker-status", Static).update(f"Archivo: {path}")
            self.query_one("#confirm", Button).disabled = False
        else:
            self._selected = None
            self.query_one("#confirm", Button).disabled = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
        elif event.button.id == "confirm" and self._selected is not None:
            self.dismiss(self._selected)

    def action_cancel(self) -> None:
        self.dismiss(None)
