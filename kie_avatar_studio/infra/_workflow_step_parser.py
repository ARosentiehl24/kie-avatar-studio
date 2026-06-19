"""Helpers de parsing de steps para `workflow_loader`.

Mantener este bloque en un módulo privado deja a `workflow_loader.py`
enfocado en el ciclo de carga/validación de archivos JSON y evita que el
archivo principal crezca más allá del límite de tamaño del proyecto.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..domain.models import StepType, WorkflowStep
from ..domain.policies import parse_optional_int_field, slugify_workflow_name


def parse_workflow_steps(
    steps_payload: list[Any],  # Any: cada elemento puede ser cualquier valor JSON.
) -> tuple[list[WorkflowStep], list[str]]:
    """Parsea la lista cruda de steps del workflow."""
    steps: list[WorkflowStep] = []
    errors: list[str] = []
    for idx, raw_step in enumerate(steps_payload, start=1):
        if not isinstance(raw_step, dict):
            errors.append(f"step #{idx}: debe ser un objeto JSON")
            continue
        try:
            step = WorkflowStep(**_build_step_kwargs(idx, raw_step))
        except (ValueError, ValidationError) as exc:
            errors.append(f"step #{idx}: {exc}")
            continue
        steps.append(step)
    return steps, errors


def _build_step_kwargs(idx: int, raw_step: dict[str, Any]) -> dict[str, Any]:
    """Normaliza el dict crudo de un step al constructor de `WorkflowStep`."""
    scene_name = str(raw_step.get("scene_name") or f"Escena {idx}")
    return {
        "step": int(raw_step.get("step", idx)),
        "scene_name": scene_name,
        "scene_slug": slugify_workflow_name(scene_name),
        "type": StepType(raw_step.get("type", "a-roll")),
        "attached": raw_step.get("attached", True),
        # Aceptamos ambos nombres (nuevo + legacy) por compat con JSONs viejos.
        "change_scene": bool(raw_step.get("change_scene", raw_step.get("change_background", True))),
        "scene_description": str(
            raw_step.get("scene_description", raw_step.get("background_description", ""))
        ),
        "prompt": str(raw_step.get("prompt", "")),
        "text": str(raw_step.get("text", "")),
        "duration_seconds": parse_optional_int_field(raw_step.get("duration_seconds")),
        "voiceover": bool(raw_step.get("voiceover", True)),
        "include_product": bool(raw_step.get("include_product", False)),
        "include_model": bool(raw_step.get("include_model", True)),
        "product_prompt": str(raw_step.get("product_prompt", "")),
        "image_aspect_ratio": (
            str(raw_step["image_aspect_ratio"])
            if raw_step.get("image_aspect_ratio") is not None
            else None
        ),
    }
