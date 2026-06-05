"""Pantalla `Imágenes`: galería mixta uploaded + generated + cola de generación.

Solo dispatch + render (CR-10.1). Mirror del patrón de `AudiosScreen`:
una tabla unificada que muestra tres tipos de fila:

- **Subidas** (`UploadedImage`): imágenes que el usuario cargó. TTL 24h.
- **Generadas** (`GeneratedImage`): salidas de Nano Banana 2. TTL 14d.
- **Jobs en cola** (`ImageJob`): generación en curso o terminales sin
  resultado todavía (failed/cancelled).

El listener al `image_queue` refresca en vivo cuando cualquier job
cambia de estado.
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
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.generated_images_controller import GeneratedImagesController
from ...app_layer.image_catalog_controller import ImageCatalogController
from ...app_layer.images_controller import ImagesController
from ...domain.errors import (
    GeneratedImageExpiredError,
    GeneratedImageNotFoundError,
    ImageExpiredError,
    ImageGenerationValidationError,
    ImageNotFoundError,
    ImageValidationError,
    KieError,
    UrlValidationError,
)
from ...domain.events import ImageJobUpdated
from ...domain.models import GeneratedImage, ImageJob, ImageJobStatus, UploadedImage
from .._clipboard_feedback import copy_url_with_feedback
from .generate_image import (
    GenerateImageFormDefaults,
    GenerateImageFormResult,
    GenerateImageFormScreen,
)
from .upload_image import UploadImageFormResult, UploadImageFormScreen

OpenLocalPath = Callable[[Path], Awaitable[None]]
OpenUrl = Callable[[str], Awaitable[None]]
CheckCredits = Callable[[], Awaitable[float | None]]

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_SECONDS_PER_MINUTE: Final[int] = 60
_SECONDS_PER_HOUR: Final[int] = 60 * _SECONDS_PER_MINUTE
_SECONDS_PER_DAY: Final[int] = 24 * _SECONDS_PER_HOUR
_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Tipo",
    "ID",
    "Label",
    "Estado / Detalle",
    "Tamaño",
    "Creado",
    "Expira",
)
_BYTES_PER_MB: Final[float] = 1024 * 1024
_LOW_CREDITS_THRESHOLD: Final[float] = 5.0

# Prefijos para los row keys de la tabla — distinguen origen para los
# selected handlers sin tener que mantener un dict paralelo.
_KEY_PREFIX_UPLOADED: Final[str] = "U:"
_KEY_PREFIX_GENERATED: Final[str] = "G:"
_KEY_PREFIX_JOB: Final[str] = "J:"


class ImagesScreen(Screen[None]):
    """Galería mixta de imágenes en Kie (uploaded + generated + cola)."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    class _ImageRefreshRequested(Message):
        """Mensaje interno: el queue avisó cambio de algún job → refrescar UI."""

        def __init__(self, job: ImageJob) -> None:
            super().__init__()
            self.job = job

    def __init__(
        self,
        uploads_controller: ImagesController,
        generated_controller: GeneratedImagesController,
        image_catalog: ImageCatalogController,
        open_local_path: OpenLocalPath,
        open_url: OpenUrl,
        *,
        default_input_dir: Path | None = None,
        check_credits: CheckCredits | None = None,
    ) -> None:
        super().__init__()
        self._uploads = uploads_controller
        self._generated = generated_controller
        self._catalog = image_catalog
        self._open_local_path = open_local_path
        self._open_url = open_url
        self._default_input_dir = default_input_dir
        self._check_credits = check_credits
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="images-box"):
            yield Static("[b]Imágenes en Kie (subidas + generadas + cola)[/b]", id="images-title")
            yield Static("[dim]Saldo Kie: consultando…[/dim]", id="images-credits")
            table: DataTable[str] = DataTable(
                id="images-table", cursor_type="row", zebra_stripes=True
            )
            for column in _TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Cargar", id="img-upload", variant="primary")
                yield Button("Generar", id="img-generate", variant="primary")
                yield Button("Ver", id="img-view", classes="btn-info")
                yield Button("Copiar URL", id="img-copy-url", classes="btn-info")
                yield Button("Cancelar job", id="img-cancel-job", classes="btn-warning")
                yield Button("Reintentar", id="img-retry", classes="btn-warning")
                yield Button("Quitar", id="img-delete", variant="error")
            yield Static(
                f"[dim]Subidas: TTL {self._uploads.retention_hours}h en Kie. "
                f"Generadas: TTL {self._generated.retention_days}d. "
                "'Quitar' borra solo el registro local; Kie las auto-expira.[/dim]",
                id="images-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        self._unsubscribe = self._generated.subscribe(self._on_queue_event)
        await self._refresh_table()
        if self._check_credits is not None:
            self.app.run_worker(self._refresh_credits(), exclusive=False)
        else:
            self.query_one("#images-credits", Static).update("")

    async def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def _refresh_credits(self) -> None:
        if self._check_credits is None:
            return
        try:
            balance = await self._check_credits()
        except Exception:
            balance = None
        try:
            widget = self.query_one("#images-credits", Static)
        except Exception:
            return
        if balance is None:
            widget.update("[dim]Saldo Kie: no disponible (sin key activa o sin red)[/dim]")
            return
        formatted = (
            f"[red]Saldo Kie: {balance:.2f} cr ❗ bajo[/red]"
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

    # --- queue events → UI refresh ----------------------------------------

    def _on_queue_event(self, event: ImageJobUpdated) -> None:
        self.post_message(self._ImageRefreshRequested(event.job))

    async def on_images_screen__image_refresh_requested(
        self, _event: _ImageRefreshRequested
    ) -> None:
        await self._refresh_table()

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
            image = await self._uploads.upload(payload.local_path, payload.label)
        except ImageValidationError as exc:
            self._set_status(f"❌ {exc}", error=True)
            return
        except KieError as exc:
            self._set_status(f"❌ upload falló: {exc}", error=True)
            return
        self._set_status(f"✅ imagen '{image.label}' subida ({image.kie_url})")
        await self._refresh_table()

    async def _handle_generate(self) -> None:
        """Abre el modal de generación con las refs disponibles cargadas."""
        refs = await self._catalog.list_usable_assets()
        self.app.push_screen(
            GenerateImageFormScreen(available_refs=refs),
            self._on_generate_form_dismissed,
        )

    def _on_generate_form_dismissed(self, result: GenerateImageFormResult | None) -> None:
        if result is None:
            return
        self.app.run_worker(self._enqueue_generation(result), exclusive=False)
        if result.keep_open:
            self.app.run_worker(
                self._reopen_generate_form(GenerateImageFormDefaults(settings=result.settings)),
                exclusive=False,
            )

    async def _reopen_generate_form(self, defaults: GenerateImageFormDefaults) -> None:
        refs = await self._catalog.list_usable_assets()
        self.app.push_screen(
            GenerateImageFormScreen(available_refs=refs, defaults=defaults),
            self._on_generate_form_dismissed,
        )

    async def _enqueue_generation(self, payload: GenerateImageFormResult) -> None:
        try:
            job = await self._generated.enqueue_generation(
                payload.label, payload.prompt, payload.settings, payload.refs
            )
        except ImageGenerationValidationError as exc:
            self._set_status(f"❌ {exc}", error=True)
            return
        self._set_status(f"✅ '{job.label}' encolado — mirá el progreso en la tabla")

    async def _handle_view(self) -> None:
        kind, asset_id = self._selected_kind_and_id()
        if kind is None or asset_id is None:
            self._set_status("Seleccioná una imagen o job en la tabla", error=True)
            return
        if kind == _KEY_PREFIX_UPLOADED:
            await self._view_uploaded(asset_id)
        elif kind == _KEY_PREFIX_GENERATED:
            await self._view_generated(asset_id)
        else:
            self._set_status("Los jobs en cola no se pueden ver hasta completar", error=True)

    async def _view_uploaded(self, image_id: str) -> None:
        try:
            image = await self._uploads.get_for_use(image_id)
        except ImageExpiredError as exc:
            self._set_status(f"❌ {exc}", error=True)
            return
        except ImageNotFoundError:
            self._set_status("La imagen ya no existe", error=True)
            return
        local = Path(image.local_path)
        if await asyncio.to_thread(local.is_file):
            await self._open_local_with_status(local)
        else:
            await self._open_url_with_clipboard_fallback(image.kie_url)

    async def _view_generated(self, image_id: str) -> None:
        try:
            image = await self._generated.get_for_use(image_id)
        except GeneratedImageExpiredError as exc:
            self._set_status(f"❌ {exc}", error=True)
            return
        except GeneratedImageNotFoundError:
            self._set_status("La imagen generada ya no existe", error=True)
            return
        # Generadas no tienen archivo local (no descargamos eager). Abrimos URL.
        await self._open_url_with_clipboard_fallback(image.kie_url)

    async def _open_local_with_status(self, local: Path) -> None:
        try:
            await self._open_local_path(local)
        except OSError as exc:
            self._set_status(f"❌ no pude abrir el visor: {exc}", error=True)
            return
        self._set_status(f"✅ abriendo {local} en visor del sistema")

    async def _open_url_with_clipboard_fallback(self, url: str) -> None:
        clip_msg, _ = await copy_url_with_feedback(url, osc52_fallback=self.app.copy_to_clipboard)
        try:
            await self._open_url(url)
        except (OSError, UrlValidationError) as exc:
            self._set_status(
                f"❌ no pude abrir el navegador ({exc})\n{clip_msg}",
                error=True,
            )
            return
        self._set_status(f"✅ abriendo URL en navegador\n{clip_msg}")

    async def _handle_copy_url(self) -> None:
        kind, asset_id = self._selected_kind_and_id()
        if kind is None or asset_id is None:
            self._set_status("Seleccioná una imagen o job en la tabla", error=True)
            return
        url = await self._resolve_url(kind, asset_id)
        if url is None:
            return
        message, is_error = await copy_url_with_feedback(
            url, osc52_fallback=self.app.copy_to_clipboard
        )
        self._set_status(message, error=is_error)

    async def _resolve_url(self, kind: str, asset_id: str) -> str | None:
        try:
            if kind == _KEY_PREFIX_UPLOADED:
                return (await self._uploads.get_for_use(asset_id)).kie_url
            if kind == _KEY_PREFIX_GENERATED:
                return (await self._generated.get_for_use(asset_id)).kie_url
            job = await self._generated.get_image_job(asset_id)
            if job is None or not job.kie_url:
                self._set_status("Ese job todavía no tiene URL", error=True)
                return None
            return job.kie_url
        except (ImageExpiredError, GeneratedImageExpiredError) as exc:
            self._set_status(f"❌ {exc}", error=True)
            return None
        except (ImageNotFoundError, GeneratedImageNotFoundError):
            self._set_status("Ya no existe", error=True)
            return None

    async def _handle_cancel_job(self) -> None:
        kind, asset_id = self._selected_kind_and_id()
        if kind != _KEY_PREFIX_JOB or asset_id is None:
            self._set_status("Solo se cancelan jobs en curso", error=True)
            return
        cancelled = await self._generated.cancel(asset_id)
        if cancelled:
            self._set_status(f"❌ job '{asset_id}' cancelado")
        else:
            self._set_status("No pude cancelar (job ya terminó o no existe)", error=True)

    async def _handle_retry(self) -> None:
        kind, asset_id = self._selected_kind_and_id()
        if kind != _KEY_PREFIX_JOB or asset_id is None:
            self._set_status("Solo se reintentan jobs fallidos/cancelados", error=True)
            return
        success = await self._generated.retry(asset_id)
        if success:
            self._set_status(f"🔁 job '{asset_id}' reencolado")
        else:
            self._set_status("No pude reencolar (estado no aplica)", error=True)

    async def _handle_delete(self) -> None:
        kind, asset_id = self._selected_kind_and_id()
        if kind is None or asset_id is None:
            self._set_status("Seleccioná una imagen o job en la tabla", error=True)
            return
        if kind == _KEY_PREFIX_UPLOADED:
            try:
                await self._uploads.delete(asset_id)
            except ImageNotFoundError:
                self._set_status("La imagen ya no existe", error=True)
                return
            self._set_status(f"✅ '{asset_id}' quitado del registro local")
        elif kind == _KEY_PREFIX_GENERATED:
            try:
                await self._generated.delete(asset_id)
            except GeneratedImageNotFoundError:
                self._set_status("La imagen generada ya no existe", error=True)
                return
            self._set_status(f"✅ generada '{asset_id}' quitada del registro local")
        else:
            await self._generated.delete_job(asset_id)
            self._set_status(f"✅ job '{asset_id}' quitado")
        await self._refresh_table()

    # --- helpers ---------------------------------------------------------

    async def _refresh_table(self) -> None:
        table = self.query_one("#images-table", DataTable)
        table.clear()
        # Mostramos primero jobs en curso (más volátiles), luego generadas
        # (TTL más largo), luego uploaded (TTL más corto).
        for job in await self._generated.list_image_jobs():
            # Si el job está COMPLETED y existe la generated, lo omitimos
            # de la fila "job" (se verá en la fila "generated") para no
            # duplicar visualmente.
            if job.status == ImageJobStatus.COMPLETED:
                continue
            table.add_row(*_row_for_job(job), key=f"{_KEY_PREFIX_JOB}{job.id}")
        retention_days = self._generated.retention_days
        for generated in await self._generated.list_generated():
            table.add_row(
                *_row_for_generated(generated, retention_days),
                key=f"{_KEY_PREFIX_GENERATED}{generated.id}",
            )
        retention_hours = self._uploads.retention_hours
        for uploaded in await self._uploads.list_uploaded():
            table.add_row(
                *_row_for_uploaded(uploaded, retention_hours),
                key=f"{_KEY_PREFIX_UPLOADED}{uploaded.id}",
            )

    def _selected_kind_and_id(self) -> tuple[str | None, str | None]:
        table = self.query_one("#images-table", DataTable)
        if table.row_count == 0:
            return None, None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None, None
        value = row_key.value
        if not value:
            return None, None
        for prefix in (_KEY_PREFIX_UPLOADED, _KEY_PREFIX_GENERATED, _KEY_PREFIX_JOB):
            if value.startswith(prefix):
                return prefix, value[len(prefix) :]
        return None, None

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


# ---------------------------------------------------------------------------
# Helpers de formato — extraerlos a `_image_format.py` si el archivo crece.
# ---------------------------------------------------------------------------


def _row_for_uploaded(image: UploadedImage, retention_hours: int) -> tuple[str, ...]:
    return (
        "subida",
        image.id,
        image.label,
        f"{image.mime_type} ({'local ✅' if image.local_file_exists() else 'local ❌'})",
        _format_size(image.file_size),
        image.uploaded_at.strftime("%Y-%m-%d %H:%M"),
        _format_time_left(image.time_left(retention_hours)),
    )


def _row_for_generated(image: GeneratedImage, retention_days: int) -> tuple[str, ...]:
    detail_parts = [f"refs: {image.refs_count}"]
    if image.settings is not None:
        detail_parts.append(
            f"{image.settings.aspect_ratio} {image.settings.resolution} "
            f"{image.settings.output_format}"
        )
    size = _format_size(image.file_size) if image.file_size is not None else "—"
    return (
        "generada",
        image.id,
        image.label,
        " · ".join(detail_parts),
        size,
        image.generated_at.strftime("%Y-%m-%d %H:%M"),
        _format_time_left(image.time_left(retention_days)),
    )


def _row_for_job(job: ImageJob) -> tuple[str, ...]:
    detail = job.error if job.error else f"task: {job.task_id or '—'}"
    return (
        f"job · {job.status.value}",
        job.id,
        job.label,
        detail,
        "—",
        job.created_at.strftime("%Y-%m-%d %H:%M"),
        "—",
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes >= _BYTES_PER_MB:
        return f"{size_bytes / _BYTES_PER_MB:.1f} MB"
    return f"{size_bytes / 1024:.1f} KB"


def _format_time_left(delta: timedelta) -> str:
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return "EXPIRADO"
    days = int(total_seconds // _SECONDS_PER_DAY)
    hours = int((total_seconds % _SECONDS_PER_DAY) // _SECONDS_PER_HOUR)
    if days > 0:
        return f"{days}d {hours}h"
    minutes = int((total_seconds % _SECONDS_PER_HOUR) // _SECONDS_PER_MINUTE)
    return f"{hours}h {minutes}m"


_BUTTON_HANDLERS: dict[str, Callable[[ImagesScreen], Awaitable[None]]] = {
    "img-upload": ImagesScreen._handle_upload,
    "img-generate": ImagesScreen._handle_generate,
    "img-view": ImagesScreen._handle_view,
    "img-copy-url": ImagesScreen._handle_copy_url,
    "img-cancel-job": ImagesScreen._handle_cancel_job,
    "img-retry": ImagesScreen._handle_retry,
    "img-delete": ImagesScreen._handle_delete,
}
