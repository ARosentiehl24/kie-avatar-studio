"""Pantalla principal `Automatización`: workflows JSON declarativos.

Lista los archivos detectados en `workflows/` (merge con DB de runs
históricos) y ofrece acciones para encolar, ver progreso, reintentar y
cancelar. Solo dispatch + render (CR-10.1).

Para el detalle por workflow (steps individuales, progress granular)
abre `WorkflowDetailScreen` desde el botón "Ver detalle".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.workflow_controller import WorkflowController
from ...domain.errors import (
    KieError,
    WorkflowStepError,
    WorkflowValidationError,
)
from ...domain.events import WorkflowJobUpdated
from ...domain.models import (
    WorkflowEntry,
    WorkflowJob,
    WorkflowStatus,
)
from .._counters import format_full_counters
from .._icons import ERROR, OK
from .._table_helpers import get_selected_row_key, select_row_by_key
from .._text_format import truncate
from ._workflow_format import (
    build_workflow_run_summary,
    format_warnings,
    format_workflow_status_cell,
)
from .configure_workflow import ConfigureWorkflowScreen
from .workflow_detail import WorkflowDetailScreen

_NOTIFICATION_TIMEOUT: Final[int] = 4
_LONG_NOTIFICATION_TIMEOUT: Final[int] = 6
_NAME_PREVIEW_LEN: Final[int] = 32

_FS_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "Archivo",
    "Estado",
    "Workflow",
    "Steps",
    "Errores / warnings",
)

_DB_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "ID",
    "Nombre",
    "Estado",
    "Steps",
    "Resumen",
)


class AutomationScreen(Screen[None]):
    """Listado de workflows del filesystem + historial de runs en DB."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
        Binding("r", "refresh", "Refrescar"),
    ]

    def __init__(
        self,
        controller: WorkflowController,
        *,
        workflows_dir: str,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._workflows_dir = workflows_dir
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="automation-box"):
            yield Static(
                "[b]Automatización — workflows JSON declarativos[/b]",
                id="automation-title",
            )
            yield Static(
                f"[dim]Directorio: {self._workflows_dir}/  ·  "
                "cada archivo .json = 1 workflow. La pantalla escanea al abrir y al "
                "presionar Refrescar (R).[/dim]",
                id="automation-subtitle",
            )
            yield Static("", id="automation-counters")
            yield Static("[b]Archivos detectados[/b]", classes="section-title")
            fs_table: DataTable[str] = DataTable(
                id="automation-fs-table", cursor_type="row", zebra_stripes=True
            )
            for column in _FS_TABLE_COLUMNS:
                fs_table.add_column(column, key=column)
            yield fs_table
            yield Static("[b]Historial de ejecuciones[/b]", classes="section-title")
            db_table: DataTable[str] = DataTable(
                id="automation-db-table", cursor_type="row", zebra_stripes=True
            )
            for column in _DB_TABLE_COLUMNS:
                db_table.add_column(column, key=column)
            yield db_table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button(
                    "Configurar y ejecutar", id="automation-configure", variant="primary"
                )
                yield Button("Ver detalle", id="automation-detail", classes="btn-info")
                yield Button("Reintentar", id="automation-retry", classes="btn-info")
                yield Button("Cancelar", id="automation-cancel", classes="btn-info")
                yield Button("Refrescar", id="automation-refresh", classes="btn-info")
            yield Static(
                "[dim]Seleccioná un archivo de la tabla superior para configurarlo y "
                "ejecutarlo. Para ver el progreso paso a paso, seleccioná una ejecución "
                "en la tabla inferior y presioná 'Ver detalle'.[/dim]",
                id="automation-hint",
            )
            yield Static("", id="automation-status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh_all(refresh_fs=True)
        self._unsubscribe = self._controller.subscribe(self._on_workflow_event)

    def on_unmount(self) -> None:
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

    async def action_refresh(self) -> None:
        await self._refresh_all(refresh_fs=True)
        self._set_status("Listado refrescado")

    # --- event listener ---------------------------------------------------

    def _on_workflow_event(self, _event: WorkflowJobUpdated) -> None:
        """Refresca la tabla de DB cuando llega un evento. No hace FS rescan."""
        self.app.call_later(self._refresh_db_table)

    # --- handlers ---------------------------------------------------------

    async def _handle_refresh(self) -> None:
        await self.action_refresh()

    async def _handle_configure(self) -> None:
        entry = await self._selected_fs_entry()
        if entry is None:
            return
        if not entry.valid:
            self._set_status(
                f"{ERROR} '{entry.name}' tiene errores: {'; '.join(entry.errors)}",
                error=True,
            )
            return

        async def _on_confirmed(voice_preset_id: str | None, audio_language: str | None) -> None:
            try:
                workflow = await self._controller.enqueue_entry(
                    entry,
                    voice_preset_id=voice_preset_id,
                    audio_language=audio_language,
                )
            except (WorkflowValidationError, WorkflowStepError, KieError) as exc:
                self._set_status(f"{ERROR} no pude encolar '{entry.name}': {exc}", error=True)
                return
            self._set_status(
                f"{OK} workflow '{workflow.name}' encolado (id={workflow.id[:14]}…)"
            )
            await self._refresh_db_table()

        await self.app.push_screen(
            ConfigureWorkflowScreen(
                entry=entry,
                on_confirm=_on_confirmed,
            )
        )

    async def _handle_detail(self) -> None:
        workflow = await self._selected_db_workflow()
        if workflow is None:
            return
        await self.app.push_screen(
            WorkflowDetailScreen(controller=self._controller, workflow_id=workflow.id)
        )

    async def _handle_retry(self) -> None:
        workflow = await self._selected_db_workflow()
        if workflow is None:
            return
        ok = await self._controller.retry(workflow.id)
        if ok:
            self._set_status(f"{OK} workflow '{workflow.name}' reencolado")
        else:
            self._set_status(
                f"{ERROR} workflow '{workflow.name}' no es reintentable (status={workflow.status.value})",
                error=True,
            )
        await self._refresh_db_table()

    async def _handle_cancel(self) -> None:
        workflow = await self._selected_db_workflow()
        if workflow is None:
            return
        ok = await self._controller.cancel(workflow.id)
        if ok:
            self._set_status(f"{OK} workflow '{workflow.name}' cancelado")
        else:
            self._set_status(
                f"{ERROR} workflow '{workflow.name}' no es cancelable", error=True
            )
        await self._refresh_db_table()

    # --- table refresh ----------------------------------------------------

    async def _refresh_all(self, *, refresh_fs: bool) -> None:
        entries = await self._controller.list_entries(refresh=refresh_fs)
        workflows = await self._controller.list_workflows()
        self._refresh_fs_table(entries)
        self._refresh_db_table_with(workflows)
        self._update_counters(workflows)

    async def _refresh_db_table(self) -> None:
        workflows = await self._controller.list_workflows()
        self._refresh_db_table_with(workflows)
        self._update_counters(workflows)

    def _refresh_fs_table(self, entries: list[WorkflowEntry]) -> None:
        table = self.query_one("#automation-fs-table", DataTable)
        previous = get_selected_row_key(table)
        table.clear()
        for entry in entries:
            payload = entry.workflow_payload or {}
            wf_name = str(payload.get("workflow", entry.name))
            steps = len(payload.get("run", []) if isinstance(payload.get("run"), list) else [])
            if entry.valid:
                status_cell = f"[green]{OK} listo[/green]"
                detail_cell = format_warnings(entry.warnings)
            else:
                status_cell = f"[red]{ERROR} error[/red]"
                detail_cell = (
                    f"[red]{truncate('; '.join(entry.errors), 60)}[/red]"
                )
            table.add_row(
                truncate(entry.name, _NAME_PREVIEW_LEN),
                status_cell,
                truncate(wf_name, _NAME_PREVIEW_LEN),
                str(steps),
                detail_cell,
                key=entry.name,
            )
        if previous is not None:
            select_row_by_key(table, previous)

    def _refresh_db_table_with(self, workflows: list[WorkflowJob]) -> None:
        table = self.query_one("#automation-db-table", DataTable)
        previous = get_selected_row_key(table)
        table.clear()
        for workflow in workflows:
            status_cell = format_workflow_status_cell(workflow.status)
            summary = build_workflow_run_summary(workflow)
            table.add_row(
                truncate(workflow.id, 22),
                truncate(workflow.name, _NAME_PREVIEW_LEN),
                status_cell,
                str(len(workflow.steps)),
                summary,
                key=workflow.id,
            )
        if previous is not None:
            select_row_by_key(table, previous)

    def _update_counters(self, workflows: list[WorkflowJob]) -> None:
        active = sum(
            1
            for w in workflows
            if w.status
            in {
                WorkflowStatus.PREPARING_BASE,
                WorkflowStatus.RUNNING,
            }
        )
        queued = sum(1 for w in workflows if w.status == WorkflowStatus.QUEUED)
        done = sum(1 for w in workflows if w.status == WorkflowStatus.COMPLETED)
        failed = sum(
            1
            for w in workflows
            if w.status in {WorkflowStatus.FAILED, WorkflowStatus.PARTIALLY_FAILED}
        )
        text = format_full_counters(
            len(workflows), active, queued, done, failed, active_label="activos"
        )
        self.query_one("#automation-counters", Static).update(text)

    # --- selection helpers ------------------------------------------------

    async def _selected_fs_entry(self) -> WorkflowEntry | None:
        table = self.query_one("#automation-fs-table", DataTable)
        key = get_selected_row_key(table)
        if key is None:
            self._set_status("Seleccioná un archivo en la tabla superior", error=True)
            return None
        entries = await self._controller.list_entries()
        for entry in entries:
            if entry.name == key:
                return entry
        self._set_status("Ese archivo ya no está en disco — refrescá", error=True)
        return None

    async def _selected_db_workflow(self) -> WorkflowJob | None:
        table = self.query_one("#automation-db-table", DataTable)
        key = get_selected_row_key(table)
        if key is None:
            self._set_status(
                "Seleccioná una ejecución en la tabla inferior primero", error=True
            )
            return None
        workflow = await self._controller.get_workflow(key)
        if workflow is None:
            self._set_status("Esa ejecución ya no existe en la DB", error=True)
        return workflow

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#automation-status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        timeout = _LONG_NOTIFICATION_TIMEOUT if error else _NOTIFICATION_TIMEOUT
        self.notify(message, severity="error" if error else "information", timeout=timeout)


_BUTTON_HANDLERS: dict[str, Callable[[AutomationScreen], Awaitable[None]]] = {
    "automation-configure": AutomationScreen._handle_configure,
    "automation-detail": AutomationScreen._handle_detail,
    "automation-retry": AutomationScreen._handle_retry,
    "automation-cancel": AutomationScreen._handle_cancel,
    "automation-refresh": AutomationScreen._handle_refresh,
}
