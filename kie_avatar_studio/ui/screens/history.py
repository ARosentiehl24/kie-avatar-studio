"""Pantalla `Historial`: vista unificada de video jobs + audio jobs.

Solo dispatch + render (CR-10.1). Recibe `HistoryController` que abstrae
ambos tipos de job detrás de `HistoryEntry`. La pantalla es **read-only**:
las acciones (cancel, retry, delete) viven en las pantallas específicas
(`AudiosScreen` y futura `VideoJobsScreen`).

Refresh en vivo: igual patrón que `AudiosScreen`. `on_mount` se suscribe
a los DOS queues a través del controller (que devuelve un único
unsubscribe), `on_unmount` desuscribe.

Filtros por tipo (Todos/Video/Audio): permiten reducir el ruido cuando
hay muchos jobs y solo interesa una clase.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.history_controller import HistoryController
from ...domain.events import TERMINAL_HISTORY_STATUS_VALUES, HistoryEntry, JobKind
from ...domain.models import AudioJobStatus, JobStatus
from .._status_badges import AUDIO_STATUS_BADGES, BASE_STATUS_BADGES, VIDEO_STATUS_BADGES
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate as _truncate

_LIST_LIMIT: Final[int] = 200
_DETAIL_PREVIEW_LEN: Final[int] = 40

_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Tipo",
    "Estado",
    "Label",
    "Detalle",
    "Creado",
)

# Iconos por tipo de job para escaneo rápido en la primera columna.
_KIND_ICONS: Final[dict[JobKind, str]] = {
    "video": "🎬 Video",
    "audio": "🔊 Audio",
}

# Renders de status: combinamos los compartidos (BASE) + los específicos
# de cada tipo. La pantalla de Historial muestra ambos kinds, así que
# necesita todos.
_STATUS_BADGES: Final[dict[str, str]] = {
    **BASE_STATUS_BADGES,
    **VIDEO_STATUS_BADGES,
    **AUDIO_STATUS_BADGES,
}

# Filtros de tipo: id → predicado sobre `HistoryEntry.kind`.
_KIND_FILTERS: Final[dict[str, frozenset[JobKind]]] = {
    "hist-filter-all": frozenset({"video", "audio"}),
    "hist-filter-video": frozenset({"video"}),
    "hist-filter-audio": frozenset({"audio"}),
}


class HistoryScreen(Screen[None]):
    """Tabla unificada de video + audio jobs con refresh en vivo."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
    ]

    class _HistoryRefreshRequested(Message):
        """Listener del queue pidió refrescar la tabla. Se postea para
        evitar re-entrada dentro de `QueueManager._notify`."""

        def __init__(self, entry: HistoryEntry) -> None:
            super().__init__()
            self.entry = entry

    def __init__(self, controller: HistoryController) -> None:
        super().__init__()
        self._controller = controller
        self._unsubscribe: Callable[[], None] | None = None
        self._kind_filter: frozenset[JobKind] = frozenset({"video", "audio"})

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="history-box"):
            yield Static("[b]Historial — video & audio[/b]", id="history-title")
            yield Static(_format_summary(0, 0, 0, 0, 0), id="history-summary")
            with Horizontal(classes="actions-row actions-row-keys", id="history-filters"):
                yield Button("Todos", id="hist-filter-all", variant="primary", classes="btn-filter")
                yield Button("🎬 Solo video", id="hist-filter-video", classes="btn-filter")
                yield Button("🔊 Solo audio", id="hist-filter-audio", classes="btn-filter")
                yield Button("Refrescar", id="hist-refresh", classes="btn-info")
            table: DataTable[str] = DataTable(
                id="history-table", cursor_type="row", zebra_stripes=True
            )
            for column in _TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            yield Static(
                "[dim]Vista de solo lectura. Para cancelar / reintentar / quitar "
                "un job, usá la pantalla específica (Audios para audio jobs).[/dim]",
                id="history-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        self._unsubscribe = self._controller.subscribe(self._on_history_event)
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
        elif button_id == "hist-refresh":
            await self._refresh()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # --- listener bridge --------------------------------------------------

    def _on_history_event(self, entry: HistoryEntry) -> None:
        """Listener sync registrado en ambos queues vía `HistoryController`.

        Posteamos un Message para que el refresh corra en su propio
        turno del event loop (evita re-entrada en `_notify`).
        """
        self.post_message(self._HistoryRefreshRequested(entry))

    async def on_history_screen__history_refresh_requested(
        self, _event: _HistoryRefreshRequested
    ) -> None:
        await self._refresh()

    # --- helpers ----------------------------------------------------------

    async def _refresh(self) -> None:
        entries = await self._controller.list_recent_entries(limit=_LIST_LIMIT)
        filtered = [e for e in entries if e.kind in self._kind_filter]

        table = self.query_one("#history-table", DataTable)
        previous_id = get_selected_row_key(table)
        table.clear()
        for entry in filtered:
            table.add_row(
                _KIND_ICONS[entry.kind],
                _STATUS_BADGES.get(entry.status_value, entry.status_value),
                _truncate(entry.label, _DETAIL_PREVIEW_LEN),
                _truncate(entry.detail, _DETAIL_PREVIEW_LEN),
                entry.created_at.strftime("%Y-%m-%d %H:%M"),
                # Key compuesta tipo:id para evitar colisiones si video y
                # audio comparten un id por accidente.
                key=f"{entry.kind}:{entry.id}",
            )
        if previous_id is not None:
            select_row_by_key(table, previous_id)

        summary = _compute_summary(entries)
        self.query_one("#history-summary", Static).update(_format_summary(*summary))

    def _highlight_filter(self, active_button_id: str) -> None:
        for btn_id in _KIND_FILTERS:
            try:
                btn = self.query_one(f"#{btn_id}", Button)
            except Exception:  # noqa: S112 — botón opcional en el DOM
                continue
            btn.variant = "primary" if btn_id == active_button_id else "default"


def _compute_summary(entries: list[HistoryEntry]) -> tuple[int, int, int, int, int]:
    """Cuenta (total, activos, en_cola, listos, fallidos)."""
    total = len(entries)
    active = sum(
        1
        for e in entries
        if e.status_value not in TERMINAL_HISTORY_STATUS_VALUES
        and e.status_value not in (JobStatus.QUEUED.value, AudioJobStatus.QUEUED.value)
    )
    queued = sum(
        1
        for e in entries
        if e.status_value in (JobStatus.QUEUED.value, AudioJobStatus.QUEUED.value)
    )
    done = sum(
        1
        for e in entries
        if e.status_value in (JobStatus.COMPLETED.value, AudioJobStatus.COMPLETED.value)
    )
    failed = sum(
        1
        for e in entries
        if e.status_value
        in (
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            AudioJobStatus.FAILED.value,
            AudioJobStatus.CANCELLED.value,
        )
    )
    return total, active, queued, done, failed


def _format_summary(total: int, active: int, queued: int, done: int, failed: int) -> str:
    """Render del header de contadores del historial."""
    return (
        f"[bold]Total {total}[/bold]  ·  "
        f"[cyan]🔄 {active} activos[/cyan]  ·  "
        f"[yellow]⏳ {queued} en cola[/yellow]  ·  "
        f"[green]✓ {done} listos[/green]  ·  "
        f"[red]✖ {failed} fallidos[/red]"
    )


# Helper para detectar timestamps de test (no usado en runtime, pero
# disponible si la screen necesita formatear timestamps relativos).
def _is_recent(when: datetime, max_age_seconds: int = 60) -> bool:
    delta = datetime.now(when.tzinfo) - when
    return delta.total_seconds() <= max_age_seconds
