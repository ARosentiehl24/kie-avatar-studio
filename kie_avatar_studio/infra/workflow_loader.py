"""`WorkflowLoader`: escanea `workflows/*.json` y los parsea a `WorkflowEntry`.

Mirror de `infra.batch_loader` pero para automatizaciones declarativas.
El loader es **puro filesystem**: no toca red, no toca DB, no encola.

Cada archivo `.json` del directorio se interpreta como UN workflow
candidato a ejecutar. La validación estructural (shape Pydantic) se
hace acá; la validación semántica (preset existe, archivo local
existe, etc.) la hace el `WorkflowController` antes de encolar.

Errores se devuelven en `WorkflowEntry.errors` (no se levantan): un
directorio con 10 JSONs no debe fallar por uno malformado.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from ..domain.errors import WorkflowStepValidationError, WorkflowValidationError
from ..domain.models import (
    ModelCreation,
    StepType,
    WorkflowEntry,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
)
from ..domain.policies import parse_optional_int_field, slugify_workflow_name, validate_workflow

_WORKFLOWS_GLOB: Final[str] = "*.json"
# Archivos JSON que NO son workflows ejecutables (docs, schemas, etc.).
# Se omiten del escaneo para evitar mostrar errores spam en la UI.
_RESERVED_JSON_NAMES: Final[frozenset[str]] = frozenset({"SCHEMA.json"})


async def scan_workflows_dir(directory: Path) -> list[WorkflowEntry]:
    """Escanea `directory` y devuelve un `WorkflowEntry` por archivo `.json`.

    El orden es alfabético por nombre de archivo. Las entries inválidas
    se devuelven igual (con `errors` poblado). Si `directory` no existe
    o no es un directorio, devuelve `[]` (no es error: simplemente no hay
    workflows).
    """
    if not await asyncio.to_thread(directory.is_dir):
        return []
    paths = await asyncio.to_thread(_list_json_files, directory)
    entries: list[WorkflowEntry] = []
    for path in paths:
        entry = await asyncio.to_thread(_build_entry, path)
        entries.append(entry)
    return entries


def _list_json_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob(_WORKFLOWS_GLOB) if p.name not in _RESERVED_JSON_NAMES)


def _build_entry(path: Path) -> WorkflowEntry:
    """Construye un `WorkflowEntry` desde un archivo JSON (síncrono)."""
    name = path.stem
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except OSError as exc:
        return WorkflowEntry(name=name, path=path, errors=[f"no se pudo leer el archivo: {exc}"])
    except json.JSONDecodeError as exc:
        return WorkflowEntry(name=name, path=path, errors=[f"JSON inválido: {exc}"])

    if not isinstance(payload, dict):
        return WorkflowEntry(
            name=name,
            path=path,
            errors=["el JSON debe ser un objeto en la raíz"],
        )

    return _validate_payload(name, path, payload)


def _validate_payload(name: str, path: Path, payload: dict[str, Any]) -> WorkflowEntry:
    """Aplica validación Pydantic + de dominio sobre el payload."""
    try:
        pre = WorkflowPreSettings.model_validate(payload.get("pre_settings", {}))
    except ValidationError as exc:
        return WorkflowEntry(
            name=name,
            path=path,
            workflow_payload=payload,
            errors=[f"pre_settings inválido: {_first_validation_msg(exc)}"],
        )

    steps_payload = payload.get("run", [])
    if not isinstance(steps_payload, list):
        return WorkflowEntry(
            name=name,
            path=path,
            workflow_payload=payload,
            errors=["'run' debe ser una lista de steps"],
        )

    steps, step_errors = _parse_steps(steps_payload)
    if step_errors:
        return WorkflowEntry(
            name=name,
            path=path,
            workflow_payload=payload,
            errors=step_errors,
        )

    workflow_name = str(payload.get("workflow") or name)
    workflow = WorkflowJob(
        id="wf_preview",  # placeholder; el controller asigna el id real al enqueue.
        name=workflow_name,
        slug=slugify_workflow_name(workflow_name),
        source_json_path=str(path),
        output_dir="",  # placeholder; el controller lo arma con el id real.
        pre_settings=pre,
        steps=steps,
        status=WorkflowStatus.QUEUED,
    )

    try:
        warnings = validate_workflow(workflow)
    except (WorkflowValidationError, WorkflowStepValidationError) as exc:
        return WorkflowEntry(
            name=name,
            path=path,
            workflow_payload=payload,
            errors=[str(exc)],
        )

    return WorkflowEntry(
        name=name,
        path=path,
        workflow_payload=payload,
        warnings=warnings,
    )


def _parse_steps(steps_payload: list[Any]) -> tuple[list[WorkflowStep], list[str]]:
    steps: list[WorkflowStep] = []
    errors: list[str] = []
    for idx, raw_step in enumerate(steps_payload, start=1):
        if not isinstance(raw_step, dict):
            errors.append(f"step #{idx}: debe ser un objeto JSON")
            continue
        scene_name = str(raw_step.get("scene_name") or f"Escena {idx}")
        try:
            step = WorkflowStep(
                step=int(raw_step.get("step", idx)),
                scene_name=scene_name,
                scene_slug=slugify_workflow_name(scene_name),
                type=StepType(raw_step.get("type", "a-roll")),
                # Aceptamos ambos nombres (nuevo + legacy) por compat con
                # JSONs viejos. Pydantic AliasChoices del modelo cubre el
                # camino formal, pero como acá construimos kwargs explícitos
                # tenemos que hacer el fallback manual.
                change_scene=bool(
                    raw_step.get("change_scene", raw_step.get("change_background", True))
                ),
                scene_description=str(
                    raw_step.get("scene_description", raw_step.get("background_description", ""))
                ),
                prompt=str(raw_step.get("prompt", "")),
                text=str(raw_step.get("text", "")),
                duration_seconds=parse_optional_int_field(raw_step.get("duration_seconds")),
                voiceover=bool(raw_step.get("voiceover", True)),
                include_product=bool(raw_step.get("include_product", False)),
                include_model=bool(raw_step.get("include_model", True)),
                product_prompt=str(raw_step.get("product_prompt", "")),
                image_aspect_ratio=(
                    str(raw_step["image_aspect_ratio"])
                    if raw_step.get("image_aspect_ratio") is not None
                    else None
                ),
            )
        except (ValueError, ValidationError) as exc:
            errors.append(f"step #{idx}: {exc}")
            continue
        steps.append(step)
    return steps, errors


def _first_validation_msg(exc: ValidationError) -> str:
    """Devuelve el primer mensaje legible de `ValidationError` sin tracebacks gigantes."""
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    location = ".".join(str(p) for p in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'inválido')}"


def build_workflow_from_entry(
    entry: WorkflowEntry,
    *,
    workflow_id: str,
    output_dir: Path,
) -> WorkflowJob:
    """Materializa un `WorkflowJob` listo para encolar desde un `WorkflowEntry` válido.

    El loader devuelve entries con `id`/`output_dir` placeholders. El
    `WorkflowController` llama esto cuando el usuario aprueba el enqueue
    para asignar el id real y el output_dir derivado.
    """
    if not entry.valid or entry.workflow_payload is None:
        raise WorkflowValidationError(
            f"entry '{entry.name}' no es válido: {'; '.join(entry.errors)}"
        )
    payload = entry.workflow_payload
    pre = WorkflowPreSettings.model_validate(payload.get("pre_settings", {}))
    steps_payload = payload.get("run", [])
    steps, _ = _parse_steps(steps_payload)
    workflow_name = str(payload.get("workflow") or entry.name)
    return WorkflowJob(
        id=workflow_id,
        name=workflow_name,
        slug=slugify_workflow_name(workflow_name),
        source_json_path=str(entry.path),
        output_dir=str(output_dir),
        pre_settings=pre,
        steps=steps,
        status=WorkflowStatus.QUEUED,
    )


def resolve_model_creation_from_payload(payload: dict[str, Any]) -> ModelCreation:
    """Devuelve el `ModelCreation` parseado desde el `pre_settings.model_creation`."""
    pre = WorkflowPreSettings.model_validate(payload.get("pre_settings", {}))
    return pre.model_creation
