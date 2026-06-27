"""Modal `SceneImageApprovalScreen` — revisión manual de escena generada.

Cuando un workflow corre en `SceneApprovalMode.MANUAL` y un step B/C-roll que
genera scene nueva (`change_scene=true` o `include_product=true`, ver
`needs_scene_generation`) genera la scene_image con Nano Banana 2, el workflow
se pausa en `WorkflowStatus.AWAITING_APPROVAL` y el step queda en
`WorkflowStepStatus.AWAITING_APPROVAL`. El usuario abre este modal desde la
pantalla Automatización (botón "Revisar escena") y decide:

- **Usar escena** → controller.approve_scene → el workflow se re-encola; el step
  runner detecta `scene_image_approved_at` y reusa la imagen sin gastar
  otra Nano Banana.
- **Editar y regenerar** → controller.regenerate_scene → persiste prompts nuevos,
  resetea el step y gasta otra imagen; vuelve a pausar.
- **Omitir escena** → controller.cancel_step → el step queda CANCELLED, el
  workflow continúa con los demás steps.
- **Cerrar (Esc)** → no hace nada; el workflow sigue esperando.

Sigue el patrón canónico del repo: `on_button_pressed` sync + `run_worker`
para evitar bloquear el message pump.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import ClassVar, Final

from loguru import logger
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Label, LoadingIndicator, Static, TextArea

from ...app_layer.workflow_controller import WorkflowController
from ...domain.errors import WorkflowValidationError
from ...domain.models import WorkflowJob, WorkflowStep
from .._icons import ERROR, OK

_PROMPT_PREVIEW_LIMIT: Final[int] = 300


class SceneImageApprovalScreen(ModalScreen[bool | None]):
    """Modal de revisión manual de escena. Devuelve True si hubo acción."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cerrar (sin acción)"),
    ]

    def __init__(
        self,
        *,
        controller: WorkflowController,
        workflow: WorkflowJob,
        step: WorkflowStep,
        open_local_path: Callable[[Path], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._workflow = workflow
        self._step = step
        self._open_local_path = open_local_path
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="scene-approval-box"):
            yield Label(
                f"[b]Revisión manual de escena — step {self._step.step} ({self._step.scene_name})[/b]",
                id="scene-approval-title",
            )
            yield Static(
                "[dim]El workflow está pausado porque el modo manual está activo. "
                "Revisá la imagen de referencia generada para este B/C-roll. "
                "Usar escena = continuar a VEO 3.1 con esta imagen. "
                "Editar y regenerar = guardar prompts nuevos y crear otra imagen. "
                "Omitir escena = saltar este step y seguir con los demás.[/]",
                id="scene-approval-subtitle",
            )
            with VerticalScroll(id="scene-approval-body"):
                yield Static(self._render_step_info(), id="scene-approval-info")
                yield Static(self._render_path_info(), id="scene-approval-path")
                yield Label("[b]Descripción de escena[/b] (se usa al regenerar)")
                yield TextArea(
                    self._step.scene_description,
                    id="scene-approval-scene-description",
                    language=None,
                )
                yield Label("[b]Prompt visual[/b] (se usa al regenerar)")
                yield TextArea(self._step.prompt, id="scene-approval-prompt", language=None)
                if self._step.include_product:
                    yield Label("[b]Prompt de producto[/b] (se usa al regenerar)")
                    yield TextArea(
                        self._step.product_prompt,
                        id="scene-approval-product-prompt",
                        language=None,
                    )
                yield Label("[b]Texto / notas[/b] (B/C-roll no genera voz en off)")
                yield TextArea(self._step.text, id="scene-approval-text", language=None)
                yield Static("", id="scene-approval-status")
            yield LoadingIndicator(id="scene-approval-loader")
            with Horizontal(id="scene-approval-actions"):
                yield Button(
                    "Usar esta escena",
                    id="scene-approval-approve",
                    classes="btn-success",
                )
                yield Button(
                    "Editar y regenerar",
                    id="scene-approval-regenerate",
                    classes="btn-warning",
                )
                yield Button(
                    "Omitir escena",
                    id="scene-approval-skip",
                    variant="default",
                )
                yield Button(
                    "Abrir en visor",
                    id="scene-approval-open",
                    classes="btn-info",
                )
                yield Button("Cerrar", id="scene-approval-close", variant="default")
        yield Footer()

    def _render_step_info(self) -> str:
        prompt_truncated = self._step.prompt[:_PROMPT_PREVIEW_LIMIT]
        ellipsis = "…" if len(self._step.prompt) > _PROMPT_PREVIEW_LIMIT else ""
        return (
            f"[b]Workflow:[/b] {self._workflow.name}\n"
            f"[b]Step:[/b] {self._step.step} — {self._step.scene_name}\n"
            f"[b]Scene description:[/b] {self._step.scene_description or '[dim](vacío)[/dim]'}\n"
            f"[b]Prompt del step:[/b] {prompt_truncated}{ellipsis}\n"
        )

    def _render_path_info(self) -> str:
        path = self._step.scene_image_path or "[dim](sin path local)[/dim]"
        return f"[b]Scene image local:[/b] [b]{path}[/b]"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "scene-approval-approve":
            self.app.run_worker(self._run_action("approve"), exclusive=False)
        elif bid == "scene-approval-regenerate":
            self.app.run_worker(self._run_action("regenerate"), exclusive=False)
        elif bid == "scene-approval-skip":
            self.app.run_worker(self._run_action("skip"), exclusive=False)
        elif bid == "scene-approval-open":
            self.app.run_worker(self._open_in_viewer(), exclusive=False)
        elif bid == "scene-approval-close":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    async def _run_action(self, action: str) -> None:
        if self._busy:
            return
        self._set_busy(True, f"[yellow]Procesando {action}…[/]")
        try:
            if action == "approve":
                await self._controller.approve_scene(self._workflow.id, self._step.step)
                msg = f"{OK} escena aprobada, workflow re-encolado"
            elif action == "regenerate":
                await self._controller.regenerate_scene(
                    self._workflow.id,
                    self._step.step,
                    scene_description=self.query_one(
                        "#scene-approval-scene-description", TextArea
                    ).text,
                    prompt=self.query_one("#scene-approval-prompt", TextArea).text,
                    product_prompt=self._product_prompt_text(),
                    text=self.query_one("#scene-approval-text", TextArea).text,
                )
                msg = f"{OK} regenerando imagen de escena con prompts editados"
            elif action == "skip":
                await self._controller.cancel_step(self._workflow.id, self._step.step)
                msg = f"{OK} escena omitida, workflow continúa con los demás"
            else:
                msg = f"{ERROR} acción desconocida: {action}"
        except WorkflowValidationError as exc:
            self._set_busy(False, f"[red]{ERROR} no se pudo: {exc}[/]")
            return
        except Exception as exc:
            logger.exception("scene_approval.action_failed action={}", action)
            self._set_busy(False, f"[red]{ERROR} error inesperado: {exc}[/]")
            return
        self._set_busy(False, msg)
        # Dismiss con True para que el caller sepa que hubo acción.
        self.dismiss(True)

    async def _open_in_viewer(self) -> None:
        if self._step.scene_image_path is None or self._open_local_path is None:
            return
        try:
            await self._open_local_path(Path(self._step.scene_image_path))
        except Exception as exc:
            logger.exception("scene_approval.open_viewer failed")
            with contextlib.suppress(Exception):
                self.query_one("#scene-approval-status", Static).update(
                    f"[red]No pude abrir el visor: {exc}[/red]"
                )

    def _set_busy(self, busy: bool, message: str) -> None:
        self._busy = busy
        for bid in (
            "scene-approval-approve",
            "scene-approval-regenerate",
            "scene-approval-skip",
            "scene-approval-open",
            "scene-approval-close",
        ):
            self.query_one(f"#{bid}", Button).disabled = busy
        self.query_one("#scene-approval-loader", LoadingIndicator).display = busy
        self.query_one("#scene-approval-status", Static).update(message)

    def _product_prompt_text(self) -> str | None:
        if not self._step.include_product:
            return None
        return self.query_one("#scene-approval-product-prompt", TextArea).text


__all__ = ["SceneImageApprovalScreen"]
