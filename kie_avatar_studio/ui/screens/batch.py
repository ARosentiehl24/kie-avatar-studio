"""Pantalla `Procesar lote`: encolar VideoJobs desde `batch_jobs/<name>/`.

Solo dispatch + render (CR-10.1). Tabla con todos los lotes detectados
en disco + acciones para refrescar, encolar uno o encolar todos los
válidos. Los lotes inválidos se muestran con su lista de errores para
que el usuario sepa qué arreglar (falta `script.txt`, etc.).

El loader (`BatchController.list_entries`) es puro filesystem y no
toca red. Cuando el usuario encola, los jobs van a la misma cola
estructurada de video que usa la pantalla "Nuevo video", así aparecen
en `Cola de trabajos` (G) con el mismo tratamiento (paralelismo,
retries, persistencia).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.batch_controller import BatchController
from ...domain.kie_voice_catalog import get_builtin_voice
from ...domain.models import BatchEntry
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_SCRIPT_PREVIEW_LEN: Final[int] = 32
_PROMPT_PREVIEW_LEN: Final[int] = 28
_ERRORS_PREVIEW_LEN: Final[int] = 60

_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Lote",
    "Estado",
    "Voz",
    "Script",
    "Prompt",
    "Imagen",
    "Detalles",
)


class BatchScreen(Screen[None]):
    """Listado de lotes en `batch_jobs/` con acciones para encolar."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
        Binding("r", "refresh", "Refrescar"),
    ]

    def __init__(self, controller: BatchController, *, batch_dir: str) -> None:
        super().__init__()
        self._controller = controller
        self._batch_dir = batch_dir

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="batch-box"):
            yield Static("[b]Procesar lote — carpetas en batch_jobs/[/b]", id="batch-title")
            yield Static(
                f"[dim]Directorio: {self._batch_dir}/  ·  "
                "cada subcarpeta = 1 video. Formato: "
                "script.txt + modelo.png|jpg (+ prompt.txt, voice.txt o meta.json).[/dim]",
                id="batch-subtitle",
            )
            yield Static("", id="batch-counters")
            table: DataTable[str] = DataTable(
                id="batch-table", cursor_type="row", zebra_stripes=True
            )
            for column in _TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Encolar válidos", id="batch-enqueue-all", variant="primary")
                yield Button("Encolar seleccionado", id="batch-enqueue-one", classes="btn-info")
                yield Button("Refrescar", id="batch-refresh", classes="btn-info")
            yield Static(
                "[dim]Los lotes inválidos se muestran con su error y NO se encolan al usar "
                "'Encolar válidos'. Editá la carpeta y volvé a refrescar (R) para revalidar. "
                "Los jobs encolados aparecen en Cola de trabajos (G).[/dim]",
                id="batch-hint",
            )
            yield Static("", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh_table(refresh_from_fs=True)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        handler = _BUTTON_HANDLERS.get(button_id)
        if handler is None:
            return
        await handler(self)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    async def action_refresh(self) -> None:
        await self._refresh_table(refresh_from_fs=True)
        self._set_status("Listado refrescado")

    # --- handlers ---------------------------------------------------------

    async def _handle_refresh(self) -> None:
        await self.action_refresh()

    async def _handle_enqueue_all(self) -> None:
        result = await self._controller.enqueue_all_valid()
        if result.total_attempted == 0 and result.skipped_invalid == 0:
            self._set_status("No hay lotes en batch_jobs/", error=True)
            return
        parts: list[str] = []
        if result.enqueued_ids:
            parts.append(f"{len(result.enqueued_ids)} encolados")
        if result.errors:
            parts.append(f"{len(result.errors)} con error")
        if result.skipped_invalid:
            parts.append(f"{result.skipped_invalid} inválidos omitidos")
        summary = " · ".join(parts) if parts else "nada por hacer"
        self._set_status(f"✅ Lote procesado: {summary}", error=bool(result.errors))
        if result.errors:
            preview = "; ".join(f"{name}: {err}" for name, err in result.errors[:3])
            self.notify(
                f"Errores encolando: {preview}",
                severity="warning",
                timeout=_LONG_NOTIFICATION_TIMEOUT,
            )
        await self._refresh_table(refresh_from_fs=False)

    async def _handle_enqueue_one(self) -> None:
        entry = await self._selected_entry()
        if entry is None:
            return
        if not entry.valid:
            self._set_status(
                f"❌ '{entry.name}' tiene errores: {'; '.join(entry.errors)}",
                error=True,
            )
            return
        try:
            job = await self._controller.enqueue_entry(entry)
        except Exception as exc:
            self._set_status(f"❌ no pude encolar '{entry.name}': {exc}", error=True)
            return
        self._set_status(f"✅ '{entry.name}' encolado (job {job.id[:8]})")

    # --- helpers ----------------------------------------------------------

    async def _refresh_table(self, *, refresh_from_fs: bool) -> None:
        entries = await self._controller.list_entries(refresh=refresh_from_fs)
        table = self.query_one("#batch-table", DataTable)
        previous_id = get_selected_row_key(table)
        table.clear()
        valid_count = 0
        invalid_count = 0
        for entry in entries:
            if entry.valid:
                valid_count += 1
                status_cell = "[green]✅ listo[/green]"
                details_cell = "—"
            else:
                invalid_count += 1
                status_cell = "[red]❌ error[/red]"
                details_cell = (
                    f"[red]{truncate('; '.join(entry.errors), _ERRORS_PREVIEW_LEN)}[/red]"
                )
            table.add_row(
                entry.name,
                status_cell,
                _format_voice(entry.voice),
                truncate(entry.script or "—", _SCRIPT_PREVIEW_LEN),
                truncate(entry.prompt or "—", _PROMPT_PREVIEW_LEN),
                entry.image_path.name if entry.image_path is not None else "—",
                details_cell,
                key=entry.name,
            )
        self._update_counters(valid_count, invalid_count)
        if previous_id is not None:
            select_row_by_key(table, previous_id)

    def _update_counters(self, valid: int, invalid: int) -> None:
        total = valid + invalid
        if total == 0:
            text = "[dim]Sin lotes detectados. Creá una carpeta en batch_jobs/.[/dim]"
        else:
            text = (
                f"[b]{total}[/b] lotes  ·  "
                f"[green]{valid} listos[/green]  ·  "
                f"[red]{invalid} con error[/red]"
            )
        self.query_one("#batch-counters", Static).update(text)

    async def _selected_entry(self) -> BatchEntry | None:
        table = self.query_one("#batch-table", DataTable)
        key = get_selected_row_key(table)
        if key is None:
            self._set_status("Seleccioná un lote en la tabla primero", error=True)
            return None
        entries = await self._controller.list_entries()
        for entry in entries:
            if entry.name == key:
                return entry
        self._set_status("Ese lote ya no está en disco — refrescá", error=True)
        return None

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


def _format_voice(voice_id: str) -> str:
    voice = get_builtin_voice(voice_id)
    return voice.label if voice is not None else truncate(voice_id, 18)


_BUTTON_HANDLERS: dict[str, Callable[[BatchScreen], Awaitable[None]]] = {
    "batch-enqueue-all": BatchScreen._handle_enqueue_all,
    "batch-enqueue-one": BatchScreen._handle_enqueue_one,
    "batch-refresh": BatchScreen._handle_refresh,
}
