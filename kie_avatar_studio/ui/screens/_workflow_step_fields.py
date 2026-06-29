"""Widgets compartidos para formularios editables de `WorkflowStep`."""

from __future__ import annotations

from collections.abc import Iterator

from textual.widget import Widget
from textual.widgets import Label, TextArea

from ...domain.models import WorkflowStep


def editable_step_text_widgets(
    step: WorkflowStep,
    *,
    id_prefix: str,
    include_scene_name: bool = False,
    regeneration_labels: bool = False,
) -> Iterator[Widget]:
    """Genera los campos textuales editables de un step."""
    suffix = " (se usa al regenerar)" if regeneration_labels else ""
    if include_scene_name:
        yield Label("[b]Nombre de escena[/b]")
        yield TextArea(step.scene_name, id=f"{id_prefix}-scene-name", language=None)
    yield Label(f"[b]Descripción de escena[/b]{suffix}")
    yield TextArea(step.scene_description, id=f"{id_prefix}-scene-description", language=None)
    prompt_label = "[b]Prompt visual[/b]" if regeneration_labels else "[b]Prompt visual / VEO[/b]"
    yield Label(f"{prompt_label}{suffix}")
    yield TextArea(step.prompt, id=f"{id_prefix}-prompt", language=None)
    if step.include_product:
        yield Label(f"[b]Prompt de producto[/b]{suffix}")
        yield TextArea(step.product_prompt, id=f"{id_prefix}-product-prompt", language=None)
    text_label = (
        "[b]Texto / notas[/b] (B/C-roll no genera voz en off)"
        if regeneration_labels
        else "[b]Texto / diálogo[/b]"
    )
    yield Label(text_label)
    yield TextArea(step.text, id=f"{id_prefix}-text", language=None)
