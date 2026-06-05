"""Pantalla `Videos`: cola estructurada + galería de video jobs.

Espejo simétrico de `AudiosScreen` pero para `VideoJob`. La pantalla
muestra TODOS los jobs (en cola, generando, listos, fallidos) en una
sola tabla unificada que se refresca en vivo con cada evento del
runner. Solo dispatch + render (CR-10.1).

Acciones contextuales por estado del job seleccionado:

- COMPLETED: Abrir mp4 (lanza el reproductor del SO sobre
  `job.output_path`) y Copiar URL (la `kie_url` del video).
- QUEUED/CREATING/POLLING: Cancelar job.
- FAILED/CANCELLED: Reintentar (reusa los assets ya resueltos del
  job: image_url + audio_url + prompt → no se vuelve a pedir nada).
- Terminales: Quitar (borra del registro local; el mp4 en disco no
  se toca).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.audio_player import AudioPlayer
from ...app_layer.audios_controller import AudiosController
from ...app_layer.images_controller import ImagesController
from ...app_layer.videos_controller import VideosController
from ...domain.errors import JobValidationError
from ...domain.events import JobUpdated
from ...domain.models import JobStatus, VideoJob
from .._clipboard_feedback import copy_url_with_feedback
from .._status_badges import BASE_STATUS_BADGES, VIDEO_STATUS_BADGES
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate
from .new_video import NewVideoFormResult, NewVideoFormScreen

OpenLocalPath = Callable[[Path], Awaitable[None]]
OpenUrl = Callable[[str], Awaitable[None]]

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_PROMPT_PREVIEW_LEN: Final[int] = 40
_PATH_PREVIEW_LEN: Final[int] = 28
_LIST_LIMIT: Final[int] = 100

_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Estado",
    "Prompt",
    "Imagen / Audio",
    "Output / Task",
    "Creado",
)

_STATUS_BADGES: Final[dict[str, str]] = {**BASE_STATUS_BADGES, **VIDEO_STATUS_BADGES}

# Subconjuntos lógicos para los contadores del panel superior.
_ACTIVE_STATUSES: Final[frozenset[JobStatus]] = frozenset(
    {
        JobStatus.VALIDATING,
        JobStatus.UPLOADING_IMAGE,
        JobStatus.CREATING_AUDIO,
        JobStatus.WAITING_AUDIO,
        JobStatus.CREATING_AVATAR,
        JobStatus.WAITING_VIDEO,
        JobStatus.DOWNLOADING,
    }
)
_QUEUED_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.QUEUED})
_DONE_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.COMPLETED})
_FAILED_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.FAILED, JobStatus.CANCELLED})


class VideosScreen(Screen[None]):
    """Cola + galería de video jobs (lip-sync con Kling AI Avatar Pro)."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    class _VideoRefreshRequested(Message):
        """Mensaje interno: el queue avisó cambio de algún job → refrescar UI."""

        def __init__(self, job: VideoJob) -> None:
            super().__init__()
            self.job = job

    def __init__(
        self,
        videos_controller: VideosController,
        images_controller: ImagesController,
        audios_controller: AudiosController,
        audio_player: AudioPlayer,
        open_local_path: OpenLocalPath,
        open_url: OpenUrl,
    ) -> None:
        super().__init__()
        self._controller = videos_controller
        self._images = images_controller
        self._audios = audios_controller
        self._audio_player = audio_player
        self._open_local_path = open_local_path
        self._open_url = open_url
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="videos-box"):
            yield Static("[b]Cola de videos (Kling AI Avatar Pro)[/b]", id="videos-title")
            yield Static(_format_counters(0, 0, 0, 0), id="videos-counters")
            table: DataTable[str] = DataTable(
                id="videos-table", cursor_type="row", zebra_stripes=True
            )
            for column in _TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Nuevo video", id="vid-new", variant="primary")
                yield Button("▶ Abrir mp4", id="vid-open", classes="btn-info")
                yield Button("Copiar URL", id="vid-copy-url", classes="btn-info")
                yield Button("Cancelar job", id="vid-cancel-job", classes="btn-warning")
                yield Button("Reintentar", id="vid-retry", classes="btn-warning")
                yield Button("Quitar", id="vid-delete", variant="error")
            yield Static(
                "[dim]Los jobs corren en background; podés cerrar la pantalla "
                "y volver. 'Nuevo video' usa imágenes ya subidas + audios ya "
                "generados. El mp4 final se guarda en outputs/<id>/final.mp4 "
                "y NO se borra al 'Quitar'.[/dim]",
                id="videos-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        self._unsubscribe = self._controller.subscribe(self._on_queue_event)
        await self._refresh_table_and_counters()

    async def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handler = _BUTTON_HANDLERS.get(button_id)
        if handler is None:
            return
        await handler(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # --- queue events → UI refresh ----------------------------------------

    def _on_queue_event(self, event: JobUpdated) -> None:
        self.post_message(self._VideoRefreshRequested(event.job))

    async def on_videos_screen__video_refresh_requested(
        self, _event: _VideoRefreshRequested
    ) -> None:
        await self._refresh_table_and_counters()

    # --- handlers ---------------------------------------------------------

    async def _handle_new(self) -> None:
        """Abre el modal de creación con los assets disponibles cargados."""
        images = await self._images.list_uploaded()
        audios = await self._audios.list_generated()
        self.app.push_screen(
            NewVideoFormScreen(
                images=images,
                audios=audios,
                audio_player=self._audio_player,
            ),
            self._on_form_dismissed,
        )

    def _on_form_dismissed(self, result: NewVideoFormResult | None) -> None:
        if result is None:
            return
        self.app.run_worker(self._enqueue(result), exclusive=False)

    async def _enqueue(self, payload: NewVideoFormResult) -> None:
        try:
            job = await self._controller.enqueue_from_assets(
                payload.image_id, payload.audio_id, payload.prompt
            )
        except JobValidationError as exc:
            self._set_status(f"✖ {exc}", error=True)
            return
        except Exception as exc:
            self._set_status(f"✖ no pude encolar el video: {exc}", error=True)
            return
        self._set_status(f"✓ video encolado (id={job.id}) — mirá el progreso en la tabla")

    async def _handle_open(self) -> None:
        job = await self._selected_job()
        if job is None:
            return
        if job.status != JobStatus.COMPLETED:
            self._set_status(
                f"'{job.id}' no está listo todavía (estado: {job.status.value})",
                error=True,
            )
            return
        if not job.output_path:
            self._set_status("El job no tiene output_path persistido", error=True)
            return
        path = Path(job.output_path)
        # `Path.is_file()` es sync pero rápido (un stat); aceptable acá.
        if not path.is_file():
            self._set_status(
                f"El mp4 no existe en disco ({path}). ¿Lo borraste?",
                error=True,
            )
            return
        try:
            await self._open_local_path(path)
        except OSError as exc:
            self._set_status(
                f"✖ no pude abrir el mp4 ({exc}); ruta: {path}",
                error=True,
            )
            return
        self._set_status(f"✓ abriendo {path}")

    async def _handle_copy_url(self) -> None:
        job = await self._selected_job()
        if job is None:
            return
        if job.status != JobStatus.COMPLETED or not job.video_url:
            self._set_status(
                f"'{job.id}' aún no tiene URL final (estado: {job.status.value})",
                error=True,
            )
            return
        message, is_error = await copy_url_with_feedback(
            job.video_url, osc52_fallback=self.app.copy_to_clipboard
        )
        self._set_status(message, error=is_error)

    async def _handle_cancel_job(self) -> None:
        job = await self._selected_job()
        if job is None:
            return
        if job.is_terminal():
            self._set_status(
                f"'{job.id}' ya está en estado terminal ({job.status.value})",
                error=True,
            )
            return
        cancelled = await self._controller.cancel(job.id)
        if cancelled:
            self._set_status(f"✖ '{job.id}' cancelado")
        else:
            self._set_status(
                f"No pude cancelar '{job.id}' (estado actual: {job.status.value})",
                error=True,
            )

    async def _handle_retry(self) -> None:
        job = await self._selected_job()
        if job is None:
            return
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            self._set_status(
                f"Reintentar solo aplica a fallidos o cancelados (estado: {job.status.value})",
                error=True,
            )
            return
        success = await self._controller.retry(job.id)
        if success:
            self._set_status(f"🔄 '{job.id}' reencolado")
        else:
            self._set_status(f"No pude reencolar '{job.id}'", error=True)

    async def _handle_delete(self) -> None:
        job = await self._selected_job()
        if job is None:
            return
        if not job.is_terminal():
            self._set_status(
                "No podés quitar un job en progreso. Cancelalo primero.",
                error=True,
            )
            return
        await self._controller.delete_job(job.id)
        self._set_status(
            f"✓ '{job.id}' quitado del registro local (el mp4 en outputs/ se conserva)"
        )

    # --- helpers ----------------------------------------------------------

    async def _refresh_table_and_counters(self) -> None:
        jobs = await self._controller.list_video_jobs(limit=_LIST_LIMIT)

        table = self.query_one("#videos-table", DataTable)
        previous_id = get_selected_row_key(table)
        table.clear()
        for job in jobs:
            table.add_row(
                _STATUS_BADGES.get(job.status.value, job.status.value),
                truncate(job.prompt, _PROMPT_PREVIEW_LEN),
                _format_assets(job),
                _format_output(job),
                job.created_at.strftime("%Y-%m-%d %H:%M"),
                key=job.id,
            )
        if previous_id is not None:
            select_row_by_key(table, previous_id)

        counters = _compute_counters(jobs)
        self.query_one("#videos-counters", Static).update(_format_counters(*counters))

    async def _selected_job(self) -> VideoJob | None:
        table = self.query_one("#videos-table", DataTable)
        job_id = get_selected_row_key(table)
        if job_id is None:
            self._set_status("Seleccioná un video en la tabla primero", error=True)
            return None
        job = await self._controller.get_video_job(job_id)
        if job is None:
            self._set_status("Ese video ya no existe", error=True)
            return None
        return job

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


def _format_assets(job: VideoJob) -> str:
    """Resumen breve de los assets del job para la tabla."""
    parts: list[str] = []
    if job.image_url:
        parts.append("🖼 ✓")
    elif job.image_path:
        parts.append(f"🖼 {truncate(job.image_path, 14)}")
    if job.audio_url:
        parts.append("🔊 ✓")
    elif job.voice:
        parts.append(f"🔊 {truncate(job.voice, 10)}")
    return "  ".join(parts) if parts else "—"


def _format_output(job: VideoJob) -> str:
    """Columna 'Output / Task': output_path si COMPLETED, task_id si en progreso."""
    if job.status == JobStatus.COMPLETED and job.output_path:
        return truncate(job.output_path, _PATH_PREVIEW_LEN)
    if job.video_task_id:
        return f"[dim]video: {truncate(job.video_task_id, _PATH_PREVIEW_LEN - 7)}[/dim]"
    if job.audio_task_id:
        return f"[dim]audio: {truncate(job.audio_task_id, _PATH_PREVIEW_LEN - 7)}[/dim]"
    if job.status == JobStatus.FAILED and job.error:
        return f"[red]{truncate(job.error, _PATH_PREVIEW_LEN)}[/red]"
    return "—"


def _compute_counters(jobs: list[VideoJob]) -> tuple[int, int, int, int]:
    """Cuenta (activos, en_cola, listos, fallidos) para el panel superior."""
    active = sum(1 for j in jobs if j.status in _ACTIVE_STATUSES)
    queued = sum(1 for j in jobs if j.status in _QUEUED_STATUSES)
    done = sum(1 for j in jobs if j.status in _DONE_STATUSES)
    failed = sum(1 for j in jobs if j.status in _FAILED_STATUSES)
    return active, queued, done, failed


def _format_counters(active: int, queued: int, done: int, failed: int) -> str:
    active_part = (
        f"[bold cyan]🔄 {active} generando[/bold cyan]"
        if active > 0
        else "[dim]🔄 0 generando[/dim]"
    )
    queued_part = (
        f"[bold yellow]⏳ {queued} en cola[/bold yellow]"
        if queued > 0
        else "[dim]⏳ 0 en cola[/dim]"
    )
    done_part = f"[bold green]✓ {done} listos[/bold green]" if done > 0 else "[dim]✓ 0 listos[/dim]"
    failed_part = (
        f"[bold red]✖ {failed} fallidos[/bold red]" if failed > 0 else "[dim]✖ 0 fallidos[/dim]"
    )
    return f"{active_part}  ·  {queued_part}  ·  {done_part}  ·  {failed_part}"


_BUTTON_HANDLERS: dict[str, Callable[[VideosScreen], Awaitable[None]]] = {
    "vid-new": VideosScreen._handle_new,
    "vid-open": VideosScreen._handle_open,
    "vid-copy-url": VideosScreen._handle_copy_url,
    "vid-cancel-job": VideosScreen._handle_cancel_job,
    "vid-retry": VideosScreen._handle_retry,
    "vid-delete": VideosScreen._handle_delete,
}
