"""Modal para editar textos de un `WorkflowStep` antes de reintentar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Label, Static, TextArea

from ...domain.errors import WorkflowValidationError
from ...domain.models import WorkflowStep
from ...domain.policies import validate_workflow_step
from ._workflow_step_fields import editable_step_text_widgets


@dataclass(frozen=True, slots=True)
class WorkflowStepEditResult:
    """Textos editados para persistir en el step."""

    scene_name: str
    scene_description: str
    prompt: str
    product_prompt: str | None
    text: str | None


class EditWorkflowStepScreen(ModalScreen[WorkflowStepEditResult | None]):
    """Formulario local para editar un step persistido."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancelar", show=False),
    ]

    def __init__(self, step: WorkflowStep) -> None:
        super().__init__()
        self._step = step

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="edit-step-dialog"):
            yield Label(
                f"[b]Editar step {self._step.step} — {self._step.scene_name}[/b]",
                id="edit-step-title",
            )
            yield Static(
                "[dim]Guardá cambios para dejar este step listo para reintento. "
                "No se reencola automáticamente ni gasta créditos hasta usar Reintentar.[/dim]",
                id="edit-step-subtitle",
            )
            with VerticalScroll(id="edit-step-body"):
                yield from editable_step_text_widgets(
                    self._step,
                    id_prefix="edit-step",
                    include_scene_name=True,
                )
                yield Static("", id="edit-step-error")
            with Horizontal(id="edit-step-footer"):
                yield Button("Cancelar", id="edit-step-cancel", variant="default")
                yield Button("Guardar cambios", id="edit-step-save", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#edit-step-prompt", TextArea).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "edit-step-cancel":
            self.action_cancel()
            return
        if button_id == "edit-step-save":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        result = WorkflowStepEditResult(
            scene_name=self.query_one("#edit-step-scene-name", TextArea).text.strip(),
            scene_description=self.query_one("#edit-step-scene-description", TextArea).text.strip(),
            prompt=self.query_one("#edit-step-prompt", TextArea).text.strip(),
            product_prompt=self._product_prompt_text(),
            text=self.query_one("#edit-step-text", TextArea).text.strip(),
        )
        edited = self._step.model_copy(
            update={
                "scene_name": result.scene_name,
                "scene_description": result.scene_description,
                "prompt": result.prompt,
                "product_prompt": result.product_prompt or "",
                "text": result.text or "",
            }
        )
        try:
            validate_workflow_step(edited)
        except WorkflowValidationError as exc:
            self.query_one("#edit-step-error", Static).update(f"[red]{exc}[/red]")
            return
        self.dismiss(result)

    def _product_prompt_text(self) -> str | None:
        if not self._step.include_product:
            return None
        return self.query_one("#edit-step-product-prompt", TextArea).text.strip()


__all__ = ["EditWorkflowStepScreen", "WorkflowStepEditResult"]
