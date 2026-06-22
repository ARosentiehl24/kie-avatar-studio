"""`WorkflowLoader`: escanea `workflows/*.json` y los parsea a `WorkflowEntry`.

Mirror de `infra.batch_loader` pero para automatizaciones declarativas.
El loader es **puro filesystem**: no toca red, no toca DB, no encola.

Cada archivo `.json` del directorio se interpreta como UN workflow
candidato a ejecutar. La validación estructural (shape Pydantic) se
hace acá; la validación semántica (archivo local existe, etc.) la hace
el `WorkflowController` antes de encolar.

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
    WorkflowEntry,
    WorkflowJob,
    WorkflowPreSettings,
    WorkflowStatus,
    WorkflowStep,
)
from ..domain.policies import slugify_workflow_name, validate_workflow
from ._workflow_step_parser import parse_workflow_steps

_WORKFLOWS_GLOB: Final[str] = "*.json"
# Archivos JSON que NO son workflows ejecutables (docs, schemas, etc.).
# Se omiten del escaneo para evitar mostrar errores spam en la UI.
_RESERVED_JSON_NAMES: Final[frozenset[str]] = frozenset({"SCHEMA.json"})
_WARNING_VEO_DEFAULTS: Final[str] = "pre_settings.veo no está configurado; se usarán los defaults"
_WARNING_AUDIO_LANGUAGE_DEPRECATED: Final[str] = (
    "pre_settings.audio_language está deprecated; se mantiene por backward compat"
)
_WARNING_I2V_DURATION_DEPRECATED: Final[str] = (
    "pre_settings.i2v_duration_seconds está deprecated; se mantiene por backward compat"
)
_ERROR_VOICE_PRESET_UNSUPPORTED: Final[str] = (
    "pre_settings inválido: voice_preset/voice_preset_id ya no está soportado; usá pre_settings.voice_changer"
)
_ERROR_VOICE_CHANGER_VOICE_ID_EMPTY: Final[str] = (
    "pre_settings inválido: voice_changer.voice_id no puede estar vacío"
)


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


def _validate_payload(  # Any: JSON crudo sin esquema estático antes de Pydantic.
    name: str, path: Path, payload: dict[str, Any]
) -> WorkflowEntry:
    """Aplica validación Pydantic + de dominio sobre el payload."""
    workflow, pre_warnings, errors = _parse_workflow_payload(
        name=name,
        path=path,
        payload=payload,
        workflow_id="wf_preview",
        output_dir="",
    )
    if workflow is None:
        return _build_invalid_entry(name, path, payload, errors)

    try:
        warnings = validate_workflow(workflow)
    except (WorkflowValidationError, WorkflowStepValidationError) as exc:
        return _build_invalid_entry(name, path, payload, [str(exc)])

    return WorkflowEntry(
        name=name,
        path=path,
        workflow_payload=payload,
        warnings=[*pre_warnings, *warnings],
    )


def _parse_pre_settings(
    raw_pre_settings: Any,  # Any: JSON crudo; puede ser dict, null u otro literal JSON.
) -> tuple[WorkflowPreSettings | None, list[str], list[str]]:
    """Parsea `pre_settings`, agregando warnings y errores del schema v2."""
    if not isinstance(raw_pre_settings, dict):
        try:
            pre = WorkflowPreSettings.model_validate(raw_pre_settings or {})
        except ValidationError as exc:
            return None, [], [f"pre_settings inválido: {_first_validation_msg(exc)}"]
        return pre, [], []

    warnings = _collect_pre_settings_warnings(raw_pre_settings)
    if "voice_preset_id" in raw_pre_settings or "voice_preset" in raw_pre_settings:
        return None, warnings, [_ERROR_VOICE_PRESET_UNSUPPORTED]
    voice_changer = raw_pre_settings.get("voice_changer")
    if voice_changer is not None and _voice_changer_voice_id_is_empty(voice_changer):
        return None, warnings, [_ERROR_VOICE_CHANGER_VOICE_ID_EMPTY]

    try:
        pre = WorkflowPreSettings.model_validate(raw_pre_settings)
    except ValidationError as exc:
        return None, warnings, [f"pre_settings inválido: {_first_validation_msg(exc)}"]
    return pre, warnings, []


def _collect_pre_settings_warnings(raw_pre_settings: dict[str, Any]) -> list[str]:
    """Devuelve warnings no bloqueantes derivados del payload crudo."""
    warnings: list[str] = []
    if "veo" not in raw_pre_settings:
        warnings.append(_WARNING_VEO_DEFAULTS)
    if "audio_language" in raw_pre_settings:
        warnings.append(_WARNING_AUDIO_LANGUAGE_DEPRECATED)
    if "i2v_duration_seconds" in raw_pre_settings:
        warnings.append(_WARNING_I2V_DURATION_DEPRECATED)
    return warnings


def _voice_changer_voice_id_is_empty(raw_voice_changer: Any) -> bool:
    """Indica si `voice_changer` vino presente pero sin `voice_id` usable."""
    if not isinstance(raw_voice_changer, dict):
        return False
    voice_id = raw_voice_changer.get("voice_id")
    return not isinstance(voice_id, str) or not voice_id.strip()


def _parse_workflow_payload(  # Any: JSON crudo sin esquema estático antes de Pydantic.
    *,
    name: str,
    path: Path,
    payload: dict[str, Any],
    workflow_id: str,
    output_dir: str,
) -> tuple[WorkflowJob | None, list[str], list[str]]:
    """Materializa un `WorkflowJob` provisional desde el payload crudo."""
    pre, pre_warnings, pre_errors = _parse_pre_settings(payload.get("pre_settings", {}))
    if pre is None or pre_errors:
        return None, pre_warnings, pre_errors

    steps_payload = payload.get("run", [])
    if not isinstance(steps_payload, list):
        return None, pre_warnings, ["'run' debe ser una lista de steps"]

    steps, step_errors = parse_workflow_steps(steps_payload)
    if step_errors:
        return None, pre_warnings, step_errors

    workflow_name = str(payload.get("workflow") or name)
    return (
        _build_workflow_job(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            source_json_path=str(path),
            output_dir=output_dir,
            pre_settings=pre,
            steps=steps,
        ),
        pre_warnings,
        [],
    )


def _build_invalid_entry(
    name: str,
    path: Path,
    payload: dict[str, Any],  # Any: JSON crudo sin esquema estático antes de Pydantic.
    errors: list[str],
) -> WorkflowEntry:
    """Construye una `WorkflowEntry` inválida conservando el payload original."""
    return WorkflowEntry(name=name, path=path, workflow_payload=payload, errors=errors)


def _build_workflow_job(
    *,
    workflow_id: str,
    workflow_name: str,
    source_json_path: str,
    output_dir: str,
    pre_settings: WorkflowPreSettings,
    steps: list[WorkflowStep],
) -> WorkflowJob:
    """Crea el `WorkflowJob` a partir de campos ya parseados."""
    return WorkflowJob(
        id=workflow_id,
        name=workflow_name,
        slug=slugify_workflow_name(workflow_name),
        source_json_path=source_json_path,
        output_dir=output_dir,
        pre_settings=pre_settings,
        steps=steps,
        status=WorkflowStatus.QUEUED,
    )


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
    workflow, _, errors = _parse_workflow_payload(
        name=entry.name,
        path=entry.path,
        payload=entry.workflow_payload,
        workflow_id=workflow_id,
        output_dir=str(output_dir),
    )
    if workflow is None or errors:
        raise WorkflowValidationError("; ".join(errors) if errors else "workflow inválido")
    return workflow


def resolve_model_creation_from_payload(  # Any: JSON crudo sin esquema estático antes de Pydantic.
    payload: dict[str, Any],
) -> ModelCreation:
    """Devuelve el `ModelCreation` parseado desde el `pre_settings.model_creation`."""
    pre, _, errors = _parse_pre_settings(payload.get("pre_settings", {}))
    if pre is None or errors:
        raise WorkflowValidationError("; ".join(errors) if errors else "pre_settings inválido")
    return pre.model_creation
