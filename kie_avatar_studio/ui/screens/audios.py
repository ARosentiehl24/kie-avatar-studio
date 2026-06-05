"""Pantalla `Audios`: cola estructurada + galería de audios TTS generados.

Solo dispatch + render (CR-10.1). Recibe `AudiosController` para la cola y
la galería y `AudioPlayer` (singleton compartido con el modal Generar) para
reproducir, detener y consultar estado. Opcionalmente recibe un
`check_credits` callable para mostrar el saldo de Kie arriba.

Refactor de Etapa 4 del refactor de cola estructurada:

- La tabla principal ahora es **una sola tabla unificada de `AudioJob`** que
  incluye todos los estados (queued / creating / polling / completed /
  failed / cancelled). Permite ver en vivo qué se está procesando y qué
  ya está listo.
- Panel arriba con **contadores en vivo** `[🔁 generando · ⏳ en cola · ✅
  listos · ❌ fallidos]`.
- La pantalla **se suscribe al stream de eventos** del `audio_queue` en
  `on_mount` y desuscribe en `on_unmount`. Los eventos del runner se
  reciben sin polling: cada transición de estado refresca la fila.
- Botón nuevo **Cancelar job** (para queued/creating/polling) y
  **Reintentar** (para failed/cancelled).
- Las acciones de Escuchar / Copiar URL siguen exigiendo que el job esté
  `COMPLETED`; las otras (cancel/retry/quitar) tienen sus propios chequeos.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.audio_player import AudioPlayer
from ...app_layer.audios_controller import AudiosController
from ...app_layer.presets_controller import VoicePresetsController
from ...domain.errors import (
    AudioExpiredError,
    AudioNotFoundError,
    AudioValidationError,
    UrlValidationError,
)
from ...domain.events import AudioJobUpdated
from ...domain.kie_voice_catalog import get_builtin_voice
from ...domain.models import AudioJob, AudioJobStatus
from .._clipboard_feedback import copy_url_with_feedback
from .._counters import format_full_counters
from .._icons import ERROR, OK, RETRY, WARNING
from .._status_badges import AUDIO_STATUS_BADGES, BASE_STATUS_BADGES
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate as _truncate
from .generate_audio import (
    GenerateAudioFormDefaults,
    GenerateAudioFormResult,
    GenerateAudioFormScreen,
)

CheckCredits = Callable[[], Awaitable[float | None]]

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_SECONDS_PER_MINUTE: Final[int] = 60
_SECONDS_PER_HOUR: Final[int] = 60 * _SECONDS_PER_MINUTE
_SECONDS_PER_DAY: Final[int] = 24 * _SECONDS_PER_HOUR
_SCRIPT_PREVIEW_LEN: Final[int] = 36
_PATH_PREVIEW_LEN: Final[int] = 28
# Umbral debajo del cual el saldo se muestra en rojo (avisa antes de
# arrancar un job que va a fallar con 402). Coherente con SettingsScreen.
_LOW_CREDITS_THRESHOLD: Final[float] = 5.0
# Cantidad máxima de jobs mostrados en la tabla (no se descarta nada del
# disco; la cola y el repo guardan todo igual, esto es solo de render).
_LIST_LIMIT: Final[int] = 100

_AUDIO_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Estado",
    "Label",
    "Voz",
    "Script",
    "Path Kie / Task",
    "Creado",
    "Expira",
)

# Cómo renderizar cada estado en la columna `Estado` de la tabla.
# Combinamos BASE (queued/validating/completed/failed/cancelled) +
# AUDIO_STATUS_BADGES (creating/polling específicos de AudioJob).
_STATUS_BADGES: Final[dict[str, str]] = {**BASE_STATUS_BADGES, **AUDIO_STATUS_BADGES}

# Subconjuntos lógicos para los contadores del panel superior.
_ACTIVE_STATUSES: Final[frozenset[AudioJobStatus]] = frozenset(
    {AudioJobStatus.VALIDATING, AudioJobStatus.CREATING, AudioJobStatus.POLLING}
)
_QUEUED_STATUSES: Final[frozenset[AudioJobStatus]] = frozenset({AudioJobStatus.QUEUED})
_DONE_STATUSES: Final[frozenset[AudioJobStatus]] = frozenset({AudioJobStatus.COMPLETED})
_FAILED_STATUSES: Final[frozenset[AudioJobStatus]] = frozenset(
    {AudioJobStatus.FAILED, AudioJobStatus.CANCELLED}
)


class AudiosScreen(Screen[None]):
    """Cola + galería de audios TTS generados con Kie."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    class _AudioRefreshRequested(Message):
        """Mensaje interno: el queue avisó cambio de algún job → refrescar UI.

        Lo posteamos desde el listener sync registrado en `audio_queue`
        para evitar re-entrada (el listener corre dentro del propio
        `_notify` del queue). El handler del Message corre en su propio
        turno del event loop, seguro de ejecutar awaits y queries.
        """

        def __init__(self, job: AudioJob) -> None:
            super().__init__()
            self.job = job

    def __init__(
        self,
        controller: AudiosController,
        audio_player: AudioPlayer,
        presets_controller: VoicePresetsController | None = None,
        check_credits: CheckCredits | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._audio_player = audio_player
        self._presets_controller = presets_controller
        self._check_credits = check_credits
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="audios-box"):
            yield Static("[b]Cola de audios TTS (Kie)[/b]", id="audios-title")
            yield Static("[dim]Saldo Kie: consultando…[/dim]", id="audios-credits")
            yield Static(_format_counters(0, 0, 0, 0, 0), id="audios-counters")
            table: DataTable[str] = DataTable(
                id="audios-table", cursor_type="row", zebra_stripes=True
            )
            for column in _AUDIO_TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Generar", id="aud-generate", variant="primary")
                yield Button("Escuchar", id="aud-listen", classes="btn-info")
                yield Button("Detener", id="aud-stop", classes="btn-warning")
                yield Button("Copiar URL", id="aud-copy-url", classes="btn-info")
                yield Button("Cancelar job", id="aud-cancel-job", classes="btn-warning")
                yield Button("Reintentar", id="aud-retry", classes="btn-warning")
                yield Button("Quitar", id="aud-delete", variant="error")
            yield Static(
                f"[dim]Los jobs en cola se procesan en background; podés cerrar y volver. "
                f"'Cancelar job' aborta uno en curso; 'Reintentar' reencola uno fallido. "
                f"Los completados se conservan {self._controller.retention_days} días en "
                f"Kie y luego se auto-borran (los expirados desaparecen al reiniciar).[/dim]",
                id="audios-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        # Suscripción al stream de eventos del queue ANTES del primer
        # refresh: si llega un evento mientras refrescamos por primera
        # vez, igual lo veremos al volver al loop.
        self._unsubscribe = self._controller.subscribe(self._on_queue_event)
        await self._refresh_table_and_counters()
        # El chequeo de saldo es best-effort y se hace en background para no
        # bloquear el render de la tabla si la red está lenta o la key está
        # mal configurada.
        if self._check_credits is not None:
            self.app.run_worker(self._refresh_credits(), exclusive=False)
        else:
            self.query_one("#audios-credits", Static).update("")

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

    def _on_queue_event(self, event: AudioJobUpdated) -> None:
        """Listener registrado en `audio_queue`. Se ejecuta en el mismo
        event loop que la pantalla, pero como callback dentro de `_notify`
        no debe hacer queries/awaits directamente (riesgo de re-entrada).
        Posteamos un Message para que el refresh corra en su propio turno.
        """
        self.post_message(self._AudioRefreshRequested(event.job))

    async def on_audios_screen__audio_refresh_requested(
        self, _event: _AudioRefreshRequested
    ) -> None:
        await self._refresh_table_and_counters()

    # --- handlers ---------------------------------------------------------

    async def _handle_generate(self) -> None:
        await self._open_generate_form_async(defaults=None)

    def _open_generate_form(self, defaults: GenerateAudioFormDefaults | None) -> None:
        """Versión sync (sin presets). Solo se usa como fallback si el
        controller no está cableado. Casi siempre conviene usar
        `_open_generate_form_async` que carga presets."""
        self.app.push_screen(
            GenerateAudioFormScreen(audio_player=self._audio_player, defaults=defaults),
            self._on_generate_form_dismissed,
        )

    async def _open_generate_form_async(self, defaults: GenerateAudioFormDefaults | None) -> None:
        """Carga la lista de presets y abre el modal con ellos disponibles.

        Si `presets_controller` no está cableado o falla, el modal abre
        sin presets (los `Select` y botón "Guardar preset" no aparecen).
        """
        presets = []
        if self._presets_controller is not None:
            try:
                presets = await self._presets_controller.list_all()
            except Exception:
                presets = []
        self.app.push_screen(
            GenerateAudioFormScreen(
                audio_player=self._audio_player,
                defaults=defaults,
                presets=presets,
            ),
            self._on_generate_form_dismissed,
        )

    def _on_generate_form_dismissed(self, result: GenerateAudioFormResult | None) -> None:
        if result is None:
            return
        # Caso 1: el usuario apretó "Guardar preset" en lugar de "Generar".
        # No encolamos audio: persistimos el preset y reabrimos el modal
        # para que pueda usarlo inmediatamente.
        if result.save_as_preset_label is not None:
            self.app.run_worker(self._save_preset(result), exclusive=False)
            return
        # Caso 2: generación normal.
        self.app.run_worker(self._enqueue_from_form(result), exclusive=False)
        # "Generar y otro": reabrir inmediatamente el modal con la misma
        # voz + settings. La generación corre en background y el usuario
        # puede ir armando el siguiente audio en paralelo.
        if result.keep_open:
            self.app.run_worker(
                self._open_generate_form_async(
                    defaults=GenerateAudioFormDefaults(
                        voice_id=result.voice_id,
                        voice_settings=result.voice_settings,
                    )
                ),
                exclusive=False,
            )

    async def _save_preset(self, payload: GenerateAudioFormResult) -> None:
        """Persiste un preset desde el modal y reabre con el preset disponible."""
        if self._presets_controller is None:
            self._set_status(f"{ERROR} Presets no disponibles", error=True)
            return
        if payload.save_as_preset_label is None:
            return
        try:
            preset = await self._presets_controller.create(
                label=payload.save_as_preset_label,
                voice_id=payload.voice_id,
                voice_settings=payload.voice_settings,
            )
        except Exception as exc:
            self._set_status(f"{ERROR} no pude guardar el preset: {exc}", error=True)
            return
        self._set_status(f"{OK} preset '{preset.label}' guardado")
        # Reabrir el modal precargando la voz/settings del preset recién
        # creado para que el usuario pueda generar inmediatamente con él.
        await self._open_generate_form_async(
            defaults=GenerateAudioFormDefaults(
                voice_id=preset.voice_id,
                voice_settings=preset.voice_settings,
            )
        )

    async def _enqueue_from_form(self, payload: GenerateAudioFormResult) -> None:
        """Encola el job y reporta encolado. El refresh de la tabla y los
        contadores lo hace el listener del queue automáticamente — acá NO
        esperamos `wait_for_job`, porque la cola es visible.
        """
        try:
            job = await self._controller.enqueue_generation(
                payload.label,
                payload.script,
                payload.voice_id,
                payload.voice_settings,
            )
        except AudioValidationError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return
        self._set_status(f"{OK} '{job.label}' encolado — mirá la tabla para ver el progreso")

    async def _refresh_credits(self) -> None:
        """Best-effort: consulta saldo y actualiza el indicador, nunca lanza.

        Si la screen se desmontó mientras esperábamos la respuesta de
        red, `query_one` lanza `NoMatches`. Lo atrapamos silenciosamente:
        no hay UI que actualizar, no hay nada que hacer.
        """
        if self._check_credits is None:
            return
        try:
            balance = await self._check_credits()
        except Exception:
            balance = None
        try:
            widget = self.query_one("#audios-credits", Static)
        except Exception:
            # La screen se cerró entre el await y el query. Best-effort: nada.
            return
        if balance is None:
            widget.update("[dim]Saldo Kie: no disponible (sin key activa o sin red)[/dim]")
            return
        formatted = (
            f"[red]Saldo Kie: {balance:.2f} cr {WARNING} bajo[/red]"
            if balance <= _LOW_CREDITS_THRESHOLD
            else f"[dim]Saldo Kie: {balance:.2f} cr[/dim]"
        )
        widget.update(formatted)

    async def _handle_listen(self) -> None:
        """Resuelve el audio y lo reproduce con el handler de audio del SO."""
        audio_url = await self._resolve_selected_completed_url()
        if audio_url is None:
            return
        # Copiamos al clipboard ANTES de intentar reproducir: mismo patrón
        # que ImagesScreen para que la URL siempre quede disponible aún si
        # el launcher o la descarga fallan.
        clip_msg, _ = await copy_url_with_feedback(
            audio_url, osc52_fallback=self.app.copy_to_clipboard
        )
        try:
            await self._audio_player.play_audio(audio_url)
        except (OSError, UrlValidationError) as exc:
            self._set_status(
                f"{ERROR} no pude reproducir el audio ({exc})\n{clip_msg}",
                error=True,
            )
            return
        self._set_status(f"{OK} reproduciendo (Detener para cancelar)\n{clip_msg}")

    async def _handle_stop(self) -> None:
        """Detiene cualquier audio en reproducción. Idempotente."""
        was_playing = self._audio_player.is_playing()
        await self._audio_player.stop()
        if was_playing:
            self._set_status("reproducción detenida")
        else:
            self._set_status("No había audio reproduciéndose")

    async def _handle_copy_url(self) -> None:
        audio_url = await self._resolve_selected_completed_url()
        if audio_url is None:
            return
        message, is_error = await copy_url_with_feedback(
            audio_url, osc52_fallback=self.app.copy_to_clipboard
        )
        self._set_status(message, error=is_error)

    async def _handle_cancel_job(self) -> None:
        job = await self._selected_job()
        if job is None:
            return
        if job.is_terminal():
            self._set_status(
                f"'{job.label}' ya está en estado terminal ({job.status.value})",
                error=True,
            )
            return
        cancelled = await self._controller.cancel(job.id)
        if cancelled:
            self._set_status(f"{ERROR} '{job.label}' cancelado")
        else:
            self._set_status(
                f"No pude cancelar '{job.label}' (estado actual: {job.status.value})",
                error=True,
            )

    async def _handle_retry(self) -> None:
        job = await self._selected_job()
        if job is None:
            return
        if job.status not in (AudioJobStatus.FAILED, AudioJobStatus.CANCELLED):
            self._set_status(
                f"Reintentar solo aplica a fallidos o cancelados (estado: {job.status.value})",
                error=True,
            )
            return
        success = await self._controller.retry(job.id)
        if success:
            self._set_status(f"{RETRY} '{job.label}' reencolado")
        else:
            self._set_status(f"No pude reencolar '{job.label}'", error=True)

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
        # Borramos tanto el AudioJob como (si existe) el GeneratedAudio
        # con el mismo id. Ambos comparten id por idempotencia.
        await self._controller.delete_job(job.id)
        self._set_status(
            f"{OK} '{job.label}' quitado del registro local "
            f"(Kie conserva el audio ~{self._controller.retention_days}d si existía)"
        )

    # --- helpers ----------------------------------------------------------

    async def _resolve_selected_completed_url(self) -> str | None:
        """Devuelve la URL escuchable del job seleccionado o muestra error.

        Acepta solo jobs en COMPLETED. Verifica expiración consultando el
        store: si el `GeneratedAudio` correspondiente expiró, avisa.
        """
        job = await self._selected_job()
        if job is None:
            return None
        if job.status != AudioJobStatus.COMPLETED or not job.kie_url:
            self._set_status(
                f"'{job.label}' no está listo todavía (estado: {job.status.value})",
                error=True,
            )
            return None
        try:
            audio = await self._controller.get_for_use(job.id)
        except AudioExpiredError as exc:
            self._set_status(f"{ERROR} {exc}", error=True)
            return None
        except AudioNotFoundError:
            # Fallback: el GeneratedAudio fue borrado pero el job tiene la
            # URL → la usamos directo (puede estar expirada, no podemos
            # validar, pero no perjudica al usuario).
            return job.kie_url
        return audio.kie_url

    async def _refresh_table_and_counters(self) -> None:
        jobs = await self._controller.list_audio_jobs(limit=_LIST_LIMIT)
        retention = self._controller.retention_days

        table = self.query_one("#audios-table", DataTable)
        # Preservamos la fila seleccionada por id para que un refresh no
        # haga "saltar" la selección del usuario.
        previous_id = get_selected_row_key(table)
        table.clear()
        for job in jobs:
            table.add_row(
                _STATUS_BADGES.get(job.status.value, job.status.value),
                job.label,
                _format_voice(job.voice_id),
                _truncate(job.script, _SCRIPT_PREVIEW_LEN),
                _format_path_or_task(job),
                job.created_at.strftime("%Y-%m-%d %H:%M"),
                _format_expires(job, retention),
                key=job.id,
            )
        if previous_id is not None:
            select_row_by_key(table, previous_id)

        counters = _compute_counters(jobs)
        self.query_one("#audios-counters", Static).update(_format_counters(*counters))

    async def _selected_job(self) -> AudioJob | None:
        """Devuelve el `AudioJob` correspondiente a la fila seleccionada."""
        table = self.query_one("#audios-table", DataTable)
        job_id = get_selected_row_key(table)
        if job_id is None:
            self._set_status("Seleccioná un audio en la tabla primero", error=True)
            return None
        job = await self._controller.get_audio_job(job_id)
        if job is None:
            self._set_status("Ese audio ya no existe", error=True)
            return None
        return job

    def _set_status(self, message: str, *, error: bool = False) -> None:
        bar = self.query_one("#status-bar", Static)
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


def _format_voice(voice_id: str) -> str:
    voice = get_builtin_voice(voice_id)
    return voice.label if voice is not None else voice_id


def _format_path_or_task(job: AudioJob) -> str:
    """En la columna 'Path Kie / Task' mostramos contexto según el estado."""
    if job.status == AudioJobStatus.COMPLETED and job.kie_file_path:
        return _truncate(job.kie_file_path, _PATH_PREVIEW_LEN)
    if job.task_id:
        return f"[dim]task: {_truncate(job.task_id, _PATH_PREVIEW_LEN - 6)}[/dim]"
    if job.status == AudioJobStatus.FAILED and job.error:
        return f"[red]{_truncate(job.error, _PATH_PREVIEW_LEN)}[/red]"
    return "—"


def _format_expires(job: AudioJob, retention_days: int) -> str:
    if job.status != AudioJobStatus.COMPLETED:
        return "—"
    elapsed = datetime.now(job.created_at.tzinfo) - job.created_at
    remaining = timedelta(days=retention_days) - elapsed
    return _format_time_left(remaining)


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


def _compute_counters(jobs: list[AudioJob]) -> tuple[int, int, int, int, int]:
    """Cuenta (total, active, queued, done, failed) para el panel superior."""
    active = sum(1 for j in jobs if j.status in _ACTIVE_STATUSES)
    queued = sum(1 for j in jobs if j.status in _QUEUED_STATUSES)
    done = sum(1 for j in jobs if j.status in _DONE_STATUSES)
    failed = sum(1 for j in jobs if j.status in _FAILED_STATUSES)
    return len(jobs), active, queued, done, failed


def _format_counters(total: int, active: int, queued: int, done: int, failed: int) -> str:
    """Wrapper sobre `ui._counters.format_full_counters` con label semántico."""
    return format_full_counters(total, active, queued, done, failed, active_label="generando")


_BUTTON_HANDLERS: dict[str, Callable[[AudiosScreen], Awaitable[None]]] = {
    "aud-generate": AudiosScreen._handle_generate,
    "aud-listen": AudiosScreen._handle_listen,
    "aud-stop": AudiosScreen._handle_stop,
    "aud-copy-url": AudiosScreen._handle_copy_url,
    "aud-cancel-job": AudiosScreen._handle_cancel_job,
    "aud-retry": AudiosScreen._handle_retry,
    "aud-delete": AudiosScreen._handle_delete,
}
