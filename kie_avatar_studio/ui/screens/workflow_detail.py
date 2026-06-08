"""Pantalla de detalle por workflow: lista los steps + progress granular.

Solo dispatch + render (CR-10.1). Se suscribe al stream para refrescar
cuando llega un `WorkflowJobUpdated` del workflow visible.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ...app_layer.workflow_controller import WorkflowController
from ...domain.events import WorkflowJobUpdated
from ...domain.models import WorkflowJob
from .._text_format import truncate
from ._workflow_format import (
    format_outputs,
    format_progress,
    format_step_status,
    format_workflow_status_label,
)

_NOTIFICATION_TIMEOUT: Final[int] = 4

_STEP_TABLE_COLUMNS: Final[tuple[str, ...]] = (
    "#",
    "Escena",
    "Tipo",
    "Estado",
    "Progreso",
    "Outputs",
    "Error",
)


class WorkflowDetailScreen(Screen[None]):
    """Detalle de un workflow específico con la lista de steps."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "go_back", "Volver"),
        Binding("r", "refresh", "Refrescar"),
    ]

    def __init__(self, *, controller: WorkflowController, workflow_id: str) -> None:
        super().__init__()
        self._controller = controller
        self._workflow_id = workflow_id
        self._unsubscribe: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="workflow-detail-box"):
            yield Static("", id="workflow-detail-title")
            yield Static("", id="workflow-detail-meta")
            table: DataTable[str] = DataTable(
                id="workflow-detail-table", cursor_type="row", zebra_stripes=True
            )
            for column in _STEP_TABLE_COLUMNS:
                table.add_column(column, key=column)
            yield table
            with Horizontal(classes="actions-row actions-row-keys"):
                yield Button("Refrescar", id="workflow-detail-refresh", classes="btn-info")
            yield Static("", id="workflow-detail-status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()
        self._unsubscribe = self._controller.subscribe(self._on_workflow_event)

    def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if (event.button.id or "") == "workflow-detail-refresh":
            await self._refresh()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    async def action_refresh(self) -> None:
        await self._refresh()

    def _on_workflow_event(self, event: WorkflowJobUpdated) -> None:
        if event.job.id != self._workflow_id:
            return
        self.app.call_later(self._refresh)

    async def _refresh(self) -> None:
        workflow = await self._controller.get_workflow(self._workflow_id)
        if workflow is None:
            self._set_status(f"workflow '{self._workflow_id}' no existe en la DB", error=True)
            return
        self._update_header(workflow)
        self._refresh_steps_table(workflow)

    def _update_header(self, workflow: WorkflowJob) -> None:
        self.query_one("#workflow-detail-title", Static).update(
            f"[b]{workflow.name}[/b]  ·  [dim]id={workflow.id}[/dim]"
        )
        manifest_note = ""
        if workflow.manifest_write_failed:
            manifest_note = "  ·  [yellow]manifest write FAILED (revisar logs)[/yellow]"
        self.query_one("#workflow-detail-meta", Static).update(
            f"[b]Status:[/b] {format_workflow_status_label(workflow)}  ·  "
            f"[b]Output:[/b] [dim]{workflow.output_dir}[/dim]{manifest_note}"
        )

    def _refresh_steps_table(self, workflow: WorkflowJob) -> None:
        table = self.query_one("#workflow-detail-table", DataTable)
        table.clear()
        for step in workflow.steps:
            table.add_row(
                str(step.step),
                truncate(step.scene_name, 28),
                step.type.value,
                format_step_status(step),
                format_progress(step),
                format_outputs(step),
                truncate(step.error or "—", 40),
                key=str(step.step),
            )

    def _set_status(self, message: str, *, error: bool = False) -> None:
        try:
            bar = self.query_one("#workflow-detail-status-bar", Static)
        except Exception:
            return
        bar.update(f"[red]{message}[/red]" if error else message)
        self.notify(
            message,
            severity="error" if error else "information",
            timeout=_NOTIFICATION_TIMEOUT,
        )
