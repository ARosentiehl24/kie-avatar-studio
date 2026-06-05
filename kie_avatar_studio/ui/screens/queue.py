"""Pantalla `Cola de trabajos`: vista operativa de jobs activos + acciones bulk.

Diferencia con `HistoryScreen`:
- HistoryScreen muestra TODO (completados incluidos) ordenado por fecha.
- QueueScreen muestra SOLO no-completados (en cola, procesando, fallidos
  recientes) con foco operativo: cancelar / reintentar / cancelar todos.

Reusa toda la infra existente:
- `HistoryController.list_recent_entries` para listar entries unificados
  video+audio, después filtra a no-completados.
- `HistoryController.subscribe` para refresh en vivo.
- `AudiosController.cancel/retry` y `VideosController.cancel/retry` para
  las acciones (despachadas según `entry.kind`).
- `_status_badges`, `_table_helpers`, `_text_format` compartidos.

Solo dispatch + render (CR-10.1).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.audios_controller import AudiosController
from ...app_layer.generated_images_controller import GeneratedImagesController
from ...app_layer.history_controller import HistoryController
from ...app_layer.videos_controller import VideosController
from ...domain.events import (
    TERMINAL_HISTORY_STATUS_VALUES,
    HistoryEntry,
    JobKind,
)
from ...domain.models import AudioJobStatus, ImageJobStatus, JobStatus
from .._counters import format_queue_summary
from .._icons import ERROR, RETRY
from .._status_badges import (
    AUDIO_STATUS_BADGES,
    BASE_STATUS_BADGES,
    IMAGE_STATUS_BADGES,
    KIND_BADGES,
    VIDEO_STATUS_BADGES,
)
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate

_LIST_LIMIT: Final[int] = 200
_DETAIL_PREVIEW_LEN: Final[int] = 36

_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Tipo",
    "Estado",
    "Label",
    "Detalle",
    "Antiguedad",
)

# Mismo set que HistoryScreen: combina badges de los 4 grupos.
_STATUS_BADGES: Final[dict[str, str]] = {
    **BASE_STATUS_BADGES,
    **VIDEO_STATUS_BADGES,
    **AUDIO_STATUS_BADGES,
    **IMAGE_STATUS_BADGES,
}

# Filtros por tipo (mismo patrón que HistoryScreen).
_KIND_FILTERS: Final[dict[str, frozenset[JobKind]]] = {
    "queue-filter-all": frozenset({"video", "audio", "image"}),
    "queue-filter-video": frozenset({"video"}),
    "queue-filter-audio": frozenset({"audio"}),
    "queue-filter-image": frozenset({"image"}),
}

# Status considerados "queued" para el conteo + bulk cancel.
_QUEUED_VALUES: Final[frozenset[str]] = frozenset(
    {JobStatus.QUEUED.value, AudioJobStatus.QUEUED.value, ImageJobStatus.QUEUED.value}
)
# Status considerados "fallido" para el bulk retry.
_FAILED_VALUES: Final[frozenset[str]] = frozenset(
    {
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
        AudioJobStatus.FAILED.value,
        AudioJobStatus.CANCELLED.value,
        ImageJobStatus.FAILED.value,
        ImageJobStatus.CANCELLED.value,
    }
)


class QueueScreen(Screen[None]):
    """Cola operativa de jobs no-completados con acciones bulk + por fila."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    class _QueueRefreshRequested(Message):
        """Listener del queue pidió refrescar la tabla. Posteamos para
        evitar re-entrada dentro de `QueueManager._notify`."""

        def __init__(self, entry: HistoryEntry) -> None:
            super().__init__()
            self.entry = entry

    def __init__(
        self,
        history_controller: HistoryController,
        audios_controller: AudiosController,
        videos_controller: VideosController,
        generated_images_controller: GeneratedImagesController,
    ) -> None:
        super().__init__()
        self._history = history_controller
        self._audios = audios_controller
        self._videos = videos_controller
        self._generated_images = generated_images_controller
        self._unsubscribe: Callable[[], None] | None = None
        self._kind_filter: frozenset[JobKind] = frozenset({"video", "audio", "image"})

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="queue-box"):
            yield Static("[b]Cola de trabajos — activos & fallidos[/b]", id="queue-title")
            yield Static(_format_summary(0, 0, 0, 0), id="queue-summary")
            with Horizontal(classes="actions-row actions-row-keys", id="queue-filters"):
                yield Button(
                    "Todos",
                    id="queue-filter-all",
                    variant="primary",
                    classes="btn-filter",
                )
                yield Button("Solo video", id="queue-filter-video", classes="btn-filter")
                yield Button("Solo audio", id="queue-filter-audio", classes="btn-filter")
                yield Button("Solo imagen", id="queue-filter-image", classes="btn-filter")
                yield Button("Refrescar", id="queue-refresh", classes="btn-info")
            table: DataTable[str] = DataTable(
                id="queue-table", cursor_type="row", zebra_stripes=True
            )
            for column in _TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys", id="queue-actions"):
                yield Button("Cancelar", id="queue-cancel", classes="btn-warning")
                yield Button("Reintentar", id="queue-retry", classes="btn-warning")
                yield Button("Cancelar cola", id="queue-cancel-all", variant="error")
                yield Button("Reintentar fallidos", id="queue-retry-all", variant="primary")
            yield Static(
                "[dim]Esta pantalla muestra solo jobs no-completados. Para ver el "
                "historial completo (incluyendo finalizados), usá Historial (H). "
                "Las acciones bulk afectan a TODOS los jobs del tipo/estado "
                "indicado — confirmá visualmente la tabla antes de apretar.[/dim]",
                id="queue-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        self._unsubscribe = self._history.subscribe(self._on_queue_event)
        await self._refresh()

    async def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id in _KIND_FILTERS:
            self._kind_filter = _KIND_FILTERS[button_id]
            self._highlight_filter(button_id)
            await self._refresh()
            return
        if button_id == "queue-refresh":
            await self._refresh()
            return
        if button_id == "queue-cancel":
            await self._handle_cancel_selected()
            return
        if button_id == "queue-retry":
            await self._handle_retry_selected()
            return
        if button_id == "queue-cancel-all":
            await self._handle_cancel_all_queued()
            return
        if button_id == "queue-retry-all":
            await self._handle_retry_all_failed()
            return

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # --- listener bridge --------------------------------------------------

    def _on_queue_event(self, entry: HistoryEntry) -> None:
        self.post_message(self._QueueRefreshRequested(entry))

    async def on_queue_screen__queue_refresh_requested(
        self, _event: _QueueRefreshRequested
    ) -> None:
        await self._refresh()

    # --- acciones por fila ------------------------------------------------

    async def _handle_cancel_selected(self) -> None:
        entry = await self._selected_entry()
        if entry is None:
            return
        if entry.status_value in TERMINAL_HISTORY_STATUS_VALUES:
            self._set_status(
                f"'{entry.label}' ya está en estado terminal ({entry.status_value})",
                error=True,
            )
            return
        ok = await self._cancel_entry(entry)
        if ok:
            self._set_status(f"{ERROR} '{entry.label}' cancelado")
        else:
            self._set_status(f"No pude cancelar '{entry.label}'", error=True)

    async def _handle_retry_selected(self) -> None:
        entry = await self._selected_entry()
        if entry is None:
            return
        if entry.status_value not in _FAILED_VALUES:
            self._set_status(
                f"Reintentar solo aplica a fallidos/cancelados (estado: {entry.status_value})",
                error=True,
            )
            return
        ok = await self._retry_entry(entry)
        if ok:
            self._set_status(f"{RETRY} '{entry.label}' reencolado")
        else:
            self._set_status(f"No pude reencolar '{entry.label}'", error=True)

    # --- acciones bulk ----------------------------------------------------

    async def _handle_cancel_all_queued(self) -> None:
        """Cancela TODOS los jobs en estado QUEUED (respetando el filtro de tipo).

        No cancela los que ya están procesando: esos requieren cancelación
        explícita por fila para no abortar trabajo en curso a ciegas.
        """
        entries = await self._all_filtered_entries()
        targets = [e for e in entries if e.status_value in _QUEUED_VALUES]
        if not targets:
            self._set_status("No hay jobs en cola para cancelar")
            return
        cancelled = 0
        for entry in targets:
            if await self._cancel_entry(entry):
                cancelled += 1
        self._set_status(
            f"{ERROR} Cancelados {cancelled} de {len(targets)} jobs en cola"
            + ("" if cancelled == len(targets) else " (algunos fallaron)"),
            error=cancelled < len(targets),
        )

    async def _handle_retry_all_failed(self) -> None:
        """Reencola TODOS los jobs FAILED o CANCELLED (respetando filtro)."""
        entries = await self._all_filtered_entries()
        targets = [e for e in entries if e.status_value in _FAILED_VALUES]
        if not targets:
            self._set_status("No hay jobs fallidos para reintentar")
            return
        retried = 0
        for entry in targets:
            if await self._retry_entry(entry):
                retried += 1
        self._set_status(
            f"{RETRY} Reencolados {retried} de {len(targets)} jobs fallidos"
            + ("" if retried == len(targets) else " (algunos fallaron)"),
            error=retried < len(targets),
        )

    # --- helpers de cancel/retry por tipo ---------------------------------

    async def _cancel_entry(self, entry: HistoryEntry) -> bool:
        if entry.kind == "audio":
            return await self._audios.cancel(entry.id)
        if entry.kind == "image":
            return await self._generated_images.cancel(entry.id)
        return await self._videos.cancel(entry.id)

    async def _retry_entry(self, entry: HistoryEntry) -> bool:
        if entry.kind == "audio":
            return await self._audios.retry(entry.id)
        if entry.kind == "image":
            return await self._generated_images.retry(entry.id)
        return await self._videos.retry(entry.id)

    # --- queries ----------------------------------------------------------

    async def _all_filtered_entries(self) -> list[HistoryEntry]:
        """Devuelve TODOS los entries activos (no-terminales) según filtro."""
        entries = await self._history.list_recent_entries(limit=_LIST_LIMIT)
        return [
            e
            for e in entries
            if e.kind in self._kind_filter
            and (
                e.status_value not in TERMINAL_HISTORY_STATUS_VALUES
                # COMPLETED es terminal "OK", lo excluimos. FAILED y
                # CANCELLED también son terminales pero los queremos
                # mostrar para reintentar.
                or e.status_value in _FAILED_VALUES
            )
            and e.status_value
            not in {
                JobStatus.COMPLETED.value,
                AudioJobStatus.COMPLETED.value,
                ImageJobStatus.COMPLETED.value,
            }
        ]

    async def _refresh(self) -> None:
        entries = await self._all_filtered_entries()

        table = self.query_one("#queue-table", DataTable)
        previous_id = get_selected_row_key(table)
        table.clear()
        for entry in entries:
            table.add_row(
                KIND_BADGES[entry.kind],
                _STATUS_BADGES.get(entry.status_value, entry.status_value),
                truncate(entry.label, _DETAIL_PREVIEW_LEN),
                truncate(entry.detail, _DETAIL_PREVIEW_LEN),
                _format_age(entry),
                key=f"{entry.kind}:{entry.id}",
            )
        if previous_id is not None:
            select_row_by_key(table, previous_id)

        summary = _compute_summary(entries)
        self.query_one("#queue-summary", Static).update(_format_summary(*summary))

    def _highlight_filter(self, active_button_id: str) -> None:
        for btn_id in _KIND_FILTERS:
            try:
                btn = self.query_one(f"#{btn_id}", Button)
            except Exception:  # noqa: S112 — botón opcional en el DOM
                continue
            btn.variant = "primary" if btn_id == active_button_id else "default"

    async def _selected_entry(self) -> HistoryEntry | None:
        """Devuelve el `HistoryEntry` de la fila seleccionada (re-lookup
        para garantizar estado fresco)."""
        table = self.query_one("#queue-table", DataTable)
        row_key = get_selected_row_key(table)
        if row_key is None:
            self._set_status("Seleccioná un job en la tabla primero", error=True)
            return None
        # Key compuesta "tipo:id" → la desarmamos.
        kind, _, job_id = row_key.partition(":")
        # Buscamos el entry fresco listando todo y filtrando.
        entries = await self._history.list_recent_entries(limit=_LIST_LIMIT)
        for e in entries:
            if e.kind == kind and e.id == job_id:
                return e
        self._set_status("Ese job ya no existe", error=True)
        return None

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        self.notify(message, severity="error" if error else "information", timeout=4)


_SECONDS_PER_MINUTE: Final[int] = 60
_SECONDS_PER_HOUR: Final[int] = 60 * 60
_SECONDS_PER_DAY: Final[int] = 24 * 60 * 60


def _format_age(entry: HistoryEntry) -> str:
    """Antigüedad relativa del job (cuándo se creó)."""
    from datetime import datetime

    delta = datetime.now(entry.created_at.tzinfo) - entry.created_at
    total = int(delta.total_seconds())
    if total < _SECONDS_PER_MINUTE:
        return f"{total}s"
    if total < _SECONDS_PER_HOUR:
        return f"{total // _SECONDS_PER_MINUTE}m"
    if total < _SECONDS_PER_DAY:
        return (
            f"{total // _SECONDS_PER_HOUR}h {(total % _SECONDS_PER_HOUR) // _SECONDS_PER_MINUTE}m"
        )
    return f"{total // _SECONDS_PER_DAY}d {(total % _SECONDS_PER_DAY) // _SECONDS_PER_HOUR}h"


def _compute_summary(entries: list[HistoryEntry]) -> tuple[int, int, int, int]:
    """Cuenta (total, queued, en_progreso, fallidos)."""
    queued = sum(1 for e in entries if e.status_value in _QUEUED_VALUES)
    failed = sum(1 for e in entries if e.status_value in _FAILED_VALUES)
    in_progress = len(entries) - queued - failed
    return len(entries), queued, in_progress, failed


def _format_summary(total: int, queued: int, in_progress: int, failed: int) -> str:
    """Wrapper sobre `ui._counters.format_queue_summary`."""
    return format_queue_summary(total, queued, in_progress, failed)
