from __future__ import annotations

from typing import Any

from ...domain.models import ModelCreation, ModelCreationMethod, StepType

JsonObject = dict[str, Any]  # Any: payload JSON externo del workflow.
_SUPPORT_ROLL_TYPES = {StepType.B_ROLL.value, StepType.C_ROLL.value}


def fallback_model_creation() -> ModelCreation:
    return ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A photorealistic person")


def payload_has_b_rolls(payload: JsonObject | None) -> bool:
    if not isinstance(payload, dict):
        return True
    steps = payload.get("run", [])
    if not isinstance(steps, list):
        return True
    return any(
        isinstance(step, dict) and step.get("type", "") in _SUPPORT_ROLL_TYPES for step in steps
    )


def payload_has_change_scene_b_rolls(payload: JsonObject | None) -> bool:
    if not isinstance(payload, dict):
        return True
    steps = payload.get("run", [])
    if not isinstance(steps, list):
        return True
    for step in steps:
        if not isinstance(step, dict) or step.get("type", "") not in _SUPPORT_ROLL_TYPES:
            continue
        change_scene = step.get("change_scene", step.get("change_background", True))
        include_product = step.get("include_product", False)
        if bool(change_scene) or bool(include_product):
            return True
    return False
