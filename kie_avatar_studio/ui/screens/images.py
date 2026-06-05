"""Pantalla `Imágenes`: galería de fotos ya subidas a Kie.

Solo dispatch + render (CR-10.1). Recibe `ImagesController` y dos callables
para abrir paths locales / URLs en el visor del sistema (inyectados para
mockear en tests).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.images_controller import ImagesController
from ...domain.errors import (
    ImageExpiredError,
    ImageNotFoundError,
    ImageValidationError,
    KieError,
    UrlValidationError,
)
from ...domain.models import UploadedImage
from .._clipboard_feedback import copy_url_with_feedback
from .upload_image import UploadImageFormResult, UploadImageFormScreen

OpenLocalPath = Callable[[Path], Awaitable[None]]
OpenUrl = Callable[[str], Awaitable[None]]
CheckCredits = Callable[[], Awaitable[float | None]]

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_SECONDS_PER_MINUTE: Final[int] = 60
_SECONDS_PER_HOUR: Final[int] = 60 * _SECONDS_PER_MINUTE
_SECONDS_PER_DAY: Final[int] = 24 * _SECONDS_PER_HOUR
_IMAGE_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "ID",
    "Label",
    "Tamaño",
    "MIME",
    "Path Kie",
    "Local",
    "Subida",
    "Expira",
)
_BYTES_PER_MB: Final[float] = 1024 * 1024
_TABLE_PATH_MAX_LEN: Final[int] = 36
# Coherente con `ui/screens/audios.py` y `ui/screens/settings.py`: si el
# saldo es menor o igual a esto, lo mostramos en rojo.
_LOW_CREDITS_THRESHOLD: Final[float] = 5.0


class ImagesScreen(Screen[None]):
    """Galería de imágenes subidas a Kie."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    def __init__(
        self,
        controller: ImagesController,
        open_local_path: OpenLocalPath,
        open_url: OpenUrl,
        *,
        default_input_dir: Path | None = None,
        check_credits: CheckCredits | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._open_local_path = open_local_path
        self._open_url = open_url
        self._default_input_dir = default_input_dir
        self._check_credits = check_credits

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="images-box"):
            yield Static("[b]Imágenes subidas a Kie[/b]", id="images-title")
            yield Static("[dim]Saldo Kie: consultando…[/dim]", id="images-credits")
            table: DataTable[str] = DataTable(
                id="images-table", cursor_type="row", zebra_stripes=True
            )
            for column in _IMAGE_TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Cargar", id="img-upload", variant="primary")
                yield Button("Ver", id="img-view", classes="btn-info")
                yield Button("Copiar URL", id="img-copy-url", classes="btn-info")
                yield Button("Quitar", id="img-delete", variant="error")
            yield Static(
                f"[dim]Path Kie = ubicación interna. Usá 'Copiar URL' para "
                f"obtener la URL descargable completa. Quitar elimina solo "
                f"el registro local — Kie retiene el archivo "
                f"{self._controller.retention_hours}h y lo borra "
                f"automático. Los expirados se quitan solos al arrancar la app.[/dim]",
                id="images-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh_table()
        if self._check_credits is not None:
            self.app.run_worker(self._refresh_credits(), exclusive=False)
        else:
            self.query_one("#images-credits", Static).update("")

    async def _refresh_credits(self) -> None:
        """Best-effort: consulta saldo y actualiza el indicador, nunca lanza."""
        if self._check_credits is None:
            return
        try:
            balance = await self._check_credits()
        except Exception:
            balance = None
        widget = self.query_one("#images-credits", Static)
        if balance is None:
            widget.update("[dim]Saldo Kie: no disponible (sin key activa o sin red)[/dim]")
            return
        formatted = (
            f"[red]Saldo Kie: {balance:.2f} cr ⚠ bajo[/red]"
            if balance <= _LOW_CREDITS_THRESHOLD
            else f"[dim]Saldo Kie: {balance:.2f} cr[/dim]"
        )
        widget.update(formatted)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handler = _BUTTON_HANDLERS.get(button_id)
        if handler is None:
            return
        await handler(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # --- handlers ---------------------------------------------------------

    async def _handle_upload(self) -> None:
        self.app.push_screen(
            UploadImageFormScreen(default_dir=self._default_input_dir),
            self._on_upload_form_dismissed,
        )

    def _on_upload_form_dismissed(self, result: UploadImageFormResult | None) -> None:
        if result is None:
            return
        self.app.run_worker(self._persist_upload(result), exclusive=False)

    async def _persist_upload(self, payload: UploadImageFormResult) -> None:
        self._set_status(f"… subiendo '{payload.label}' a Kie")
        try:
            image = await self._controller.upload(payload.local_path, payload.label)
        except ImageValidationError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        except KieError as exc:
            self._set_status(f"✖ upload falló: {exc}", error=True)
            return
        self._set_status(f"✓ imagen '{image.label}' subida ({image.kie_url})")
        await self._refresh_table()

    async def _handle_view(self) -> None:
        """Resuelve la imagen y la abre con el visor más apropiado."""
        image_id = self._selected_id()
        if image_id is None:
            self._set_status("Selecciona una imagen en la tabla primero", error=True)
            return
        try:
            image = await self._controller.get_for_use(image_id)
        except ImageExpiredError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        except ImageNotFoundError:
            self._set_status("La imagen ya no existe", error=True)
            return
        local = Path(image.local_path)
        if await asyncio.to_thread(local.is_file):
            await self._open_local_with_status(local)
        else:
            await self._open_url_with_clipboard_fallback(image)

    async def _open_local_with_status(self, local: Path) -> None:
        try:
            await self._open_local_path(local)
        except OSError as exc:
            self._set_status(f"✖ no pude abrir el visor: {exc}", error=True)
            return
        self._set_status(f"✓ abriendo {local} en visor del sistema")

    async def _open_url_with_clipboard_fallback(self, image: UploadedImage) -> None:
        # Copiamos al clipboard ANTES de intentar abrir el navegador: el toast
        # de Textual trunca URLs largas, así que dejarla en el clipboard es la
        # única forma confiable de que el usuario tenga la URL completa aun
        # si el browser falla.
        clip_msg, _ = await copy_url_with_feedback(
            image.kie_url, osc52_fallback=self.app.copy_to_clipboard
        )
        try:
            await self._open_url(image.kie_url)
        except (OSError, UrlValidationError) as exc:
            self._set_status(
                f"✖ no pude abrir el navegador ({exc})\n{clip_msg}",
                error=True,
            )
            return
        self._set_status(f"✓ archivo local no encontrado; abriendo URL en navegador\n{clip_msg}")

    async def _handle_copy_url(self) -> None:
        image_id = self._selected_id()
        if image_id is None:
            self._set_status("Selecciona una imagen en la tabla primero", error=True)
            return
        try:
            image = await self._controller.get_for_use(image_id)
        except ImageExpiredError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        except ImageNotFoundError:
            self._set_status("La imagen ya no existe", error=True)
            return
        message, is_error = await copy_url_with_feedback(
            image.kie_url, osc52_fallback=self.app.copy_to_clipboard
        )
        self._set_status(message, error=is_error)

    async def _handle_delete(self) -> None:
        image_id = self._selected_id()
        if image_id is None:
            self._set_status("Selecciona una imagen en la tabla primero", error=True)
            return
        try:
            await self._controller.delete(image_id)
        except ImageNotFoundError:
            self._set_status("La imagen ya no existe", error=True)
            return
        self._set_status(
            f"✓ '{image_id}' quitada del registro local "
            f"(Kie la conserva ~{self._controller.retention_hours}h hasta auto-borrado)"
        )
        await self._refresh_table()

    # --- helpers ---------------------------------------------------------

    async def _refresh_table(self) -> None:
        table = self.query_one("#images-table", DataTable)
        table.clear()
        retention = self._controller.retention_hours
        for image in await self._controller.list_uploaded():
            table.add_row(
                image.id,
                image.label,
                _format_size(image.file_size),
                image.mime_type,
                # Mostramos el path interno de Kie, NO la URL completa: el
                # DataTable de Textual auto-detecta cualquier "https://..." y
                # lo convierte en link clickable; al estar truncado con "…",
                # clickear genera URLs inválidas con %E2%80%A6 al final.
                # Para obtener la URL descargable están los botones
                # "Copiar URL" y "Ver".
                _truncate(image.kie_file_path, _TABLE_PATH_MAX_LEN),
                "✓" if image.local_file_exists() else "✖",
                image.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                _format_time_left(image.time_left(retention)),
                key=image.id,
            )

    def _selected_id(self) -> str | None:
        table = self.query_one("#images-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:  # tabla sin selección — sin captura tipada en esta API
            return None
        return row_key.value

    def _set_status(self, message: str, *, error: bool = False) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


def _format_size(size_bytes: int) -> str:
    if size_bytes >= _BYTES_PER_MB:
        return f"{size_bytes / _BYTES_PER_MB:.1f} MB"
    return f"{size_bytes / 1024:.1f} KB"


def _format_time_left(delta: timedelta) -> str:
    """Formatea un `timedelta` como 'Xd Yh' o 'EXPIRADO' si es negativo."""
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return "EXPIRADO"
    days = int(total_seconds // _SECONDS_PER_DAY)
    hours = int((total_seconds % _SECONDS_PER_DAY) // _SECONDS_PER_HOUR)
    if days > 0:
        return f"{days}d {hours}h"
    minutes = int((total_seconds % _SECONDS_PER_HOUR) // _SECONDS_PER_MINUTE)
    return f"{hours}h {minutes}m"


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


_BUTTON_HANDLERS: dict[str, Callable[[ImagesScreen], Awaitable[None]]] = {
    "img-upload": ImagesScreen._handle_upload,
    "img-view": ImagesScreen._handle_view,
    "img-copy-url": ImagesScreen._handle_copy_url,
    "img-delete": ImagesScreen._handle_delete,
}
