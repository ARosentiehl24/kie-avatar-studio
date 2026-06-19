"""Escribe el manifest `workflow.json` atómicamente en el `output_dir`.

Implementa `domain.ports.WorkflowManifestWriter`. Single responsibility:
serializar un `WorkflowJob` (snapshot derivado desde la DB) y escribirlo
de forma atómica al filesystem.

### Atomicidad

Patrón estándar `tmp + replace`:
1. Escribir el payload completo a `workflow.json.<uuid>.tmp` en el MISMO
   directorio que el target. Es requisito para que el rename sea atómico
   en el FS (no se permite rename entre volúmenes).
2. `Path.replace(target)` → mapea a:
   - POSIX: `rename(2)` — atómico por contrato del kernel.
   - Windows NTFS: `MoveFileEx(MOVEFILE_REPLACE_EXISTING)` — atómico para
     paths en el mismo volumen NTFS.

Si el proceso crashea entre el `write_text` del tmp y el `replace`, el
`.json` previo sigue intacto. La DB tiene el estado correcto y al
próximo `_transition()` se regenera limpio.

### Robustez en Windows (importante)

`os.replace` en Windows puede fallar con `PermissionError` si el target
tiene un handle abierto por antivirus, OneDrive, indexador o un consumer
externo. Mitigación: hasta 4 intentos en total (1 inicial sin delay + 3
con backoff exponencial 50ms → 150ms → 500ms). Tras agotar todos los
intentos se devuelve `False` y el runner marca
`workflow.manifest_write_failed=True` (NO bloquea el workflow: la DB es
la fuente de verdad).

### No-locks

NO usamos file locking. Las únicas escrituras al target vienen del
`WorkflowRunner._transition()` que está serializado por un
`asyncio.Lock` per workflow_id. Entre workflows distintos, cada uno
escribe a su propio `output_dir/workflow.json` → no hay solapamiento.

### Tmp único por escritura

Cada `write()` genera un sufijo `.<uuid4>.tmp` para evitar colisiones
con escrituras concurrentes (no deberían pasar con el lock, pero por
defensa). Al final del workflow o al arrancar la app, los `.tmp` stale
se pueden limpiar best-effort (no crítico).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import Counter
from pathlib import Path
from typing import Any, Final

from loguru import logger

from ..domain.models import (
    WorkflowJob,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)

_MANIFEST_FILENAME: Final[str] = "workflow.json"
_TMP_SUFFIX_BYTES: Final[int] = 4  # 8 chars hex; suficiente para evitar colisiones.
_REPLACE_RETRY_DELAYS: Final[tuple[float, ...]] = (0.05, 0.15, 0.5)


class AtomicWorkflowManifestWriter:
    """Implementación concreta del `WorkflowManifestWriter` con atomic write."""

    async def write(self, workflow: WorkflowJob) -> bool:
        """Escribe `output_dir/workflow.json` atómicamente.

        Devuelve `True` si la escritura terminó OK, `False` si falló
        permanentemente tras los retries. El runner usa el resultado
        para setear `workflow.manifest_write_failed`.
        """
        output_dir = Path(workflow.output_dir)
        try:
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("No se pudo crear output_dir '{}' para manifest: {}", output_dir, exc)
            return False

        manifest = _build_manifest_payload(workflow)
        payload = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False)
        target = output_dir / _MANIFEST_FILENAME
        tmp = output_dir / f"{_MANIFEST_FILENAME}.{secrets.token_hex(_TMP_SUFFIX_BYTES)}.tmp"

        try:
            await asyncio.to_thread(tmp.write_text, payload, encoding="utf-8")
        except OSError as exc:
            logger.warning("Falló escribir manifest tmp '{}': {}", tmp, exc)
            await _best_effort_unlink(tmp)
            return False

        try:
            await _replace_with_retry(tmp, target)
        except OSError as exc:
            logger.warning("Falló rename atómico de manifest '{}' → '{}': {}", tmp, target, exc)
            await _best_effort_unlink(tmp)
            return False

        return True


async def _replace_with_retry(tmp: Path, target: Path) -> None:
    """Hace `tmp.replace(target)` con reintentos ante `PermissionError`.

    `PermissionError` en Windows es común si el archivo target está abierto
    por otro proceso (antivirus, OneDrive, etc.). Backoff exponencial corto
    suele resolverlo. Tras agotar reintentos, propaga la excepción.
    """
    last_exc: OSError | None = None
    for delay in (0.0, *_REPLACE_RETRY_DELAYS):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(tmp.replace, target)
            return
        except PermissionError as exc:
            last_exc = exc
            continue
        except OSError as exc:
            # Otros OSError (disk full, path inválido) no se reintentan.
            raise exc
    if last_exc is None:
        raise OSError("retry replace: estado inesperado sin excepción capturada")
    raise last_exc


async def _best_effort_unlink(path: Path) -> None:
    """Borra el path si existe, ignorando cualquier error."""
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError:
        logger.opt(exception=True).debug("Best-effort unlink falló para {}", path)


def cleanup_stale_tmps(output_dir: Path) -> int:
    """Borra archivos `.tmp` stale del directorio (best-effort, sync).

    Útil al arrancar el workflow para limpiar restos de crashes previos.
    Devuelve cuántos archivos se borraron. No falla si el dir no existe.
    """
    if not output_dir.is_dir():
        return 0
    pattern = f"{_MANIFEST_FILENAME}.*.tmp"
    removed = 0
    for tmp in output_dir.glob(pattern):
        try:
            tmp.unlink()
            removed += 1
        except OSError:
            logger.opt(exception=True).debug("No se pudo borrar tmp stale {}", tmp)
    return removed


# --- payload builder ------------------------------------------------------


def _build_manifest_payload(workflow: WorkflowJob) -> dict[str, Any]:
    """Convierte un `WorkflowJob` en el shape JSON publicado al usuario."""
    return {
        "id": workflow.id,
        "name": workflow.name,
        "slug": workflow.slug,
        "status": workflow.status.value,
        "progress_summary": _summarize_steps(workflow.steps),
        "created_at": workflow.created_at.isoformat(),
        "updated_at": workflow.updated_at.isoformat(),
        "source_json_path": workflow.source_json_path,
        "output_dir": workflow.output_dir,
        "error": workflow.error,
        "manifest_write_failed": workflow.manifest_write_failed,
        "pre_settings": workflow.pre_settings.model_dump(by_alias=True, mode="json"),
        "model_base": _model_base_block(workflow),
        "product": _product_block(workflow),
        "outputs": _workflow_outputs_block(workflow),
        "steps": [_step_block(step) for step in workflow.steps],
    }


def _product_block(workflow: WorkflowJob) -> dict[str, Any] | None:
    """Devuelve el bloque `product` con el producto promocional resuelto o `None`."""
    pre = workflow.pre_settings
    if not pre.promote_product or pre.product_image is None:
        return None
    product = pre.product_image
    ref = product.resolved_image_ref
    block: dict[str, Any] = {"local_path": product.local_path}
    if ref is not None:
        block.update(
            {
                "kind": ref.kind.value,
                "id": ref.id,
                "label": ref.label,
                "kie_url": ref.kie_url,
                "expires_at": ref.expires_at.isoformat(),
            }
        )
    return block


def _model_base_block(workflow: WorkflowJob) -> dict[str, Any] | None:
    """Devuelve el bloque `model_base` con la imagen base resuelta o `None`."""
    creation = workflow.pre_settings.model_creation
    ref = creation.resolved_image_ref
    if ref is None:
        return None
    return {
        "kind": ref.kind.value,
        "id": ref.id,
        "label": ref.label,
        "kie_url": ref.kie_url,
        "expires_at": ref.expires_at.isoformat(),
        "local_path": _base_local_path(workflow),
    }


def _base_local_path(workflow: WorkflowJob) -> str | None:
    """Path local del `base.png` descargado (si existe)."""
    base = Path(workflow.output_dir) / "base.png"
    return str(base) if base.exists() else None


def _step_block(step: WorkflowStep) -> dict[str, Any]:
    return {
        "step": step.step,
        "scene_name": step.scene_name,
        "scene_slug": step.scene_slug,
        "type": step.type.value,
        "change_scene": step.change_scene,
        "scene_description": step.scene_description,
        "prompt": step.prompt,
        "text": step.text,
        "duration_seconds": step.duration_seconds,
        "voiceover": step.voiceover,
        "include_product": step.include_product,
        "include_model": step.include_model,
        "product_prompt": step.product_prompt,
        "image_aspect_ratio": step.image_aspect_ratio,
        "scene_image_approved_at": (
            step.scene_image_approved_at.isoformat() if step.scene_image_approved_at else None
        ),
        "status": step.status.value,
        "progress": {k.value: v.value for k, v in step.progress.items()},
        "outputs": _step_outputs(step),
        "error": step.error,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "completed_at": step.completed_at.isoformat() if step.completed_at else None,
    }


def _step_outputs(step: WorkflowStep) -> dict[str, str]:
    """Devuelve los paths locales generados por el step (filtrando los None)."""
    candidates = {
        "scene_image": step.scene_image_path,
        "audio": step.audio_path,
        "video": step.video_path,
    }
    return {key: value for key, value in candidates.items() if value}


def _workflow_outputs_block(workflow: WorkflowJob) -> dict[str, str]:
    """Devuelve los outputs finales derivados que existan en el output_dir."""
    output_dir = Path(workflow.output_dir)
    candidates = {
        "video": output_dir / "final.mp4",
        "audio": output_dir / "final_audio.mp3",
        "voice_changed_audio": output_dir / "voice_changed_audio.mp3",
    }
    return {key: str(path) for key, path in candidates.items() if path.is_file()}


def _summarize_steps(steps: list[WorkflowStep]) -> str:
    """Resumen legible del progreso global (no persistido en DB)."""
    counts = Counter(step.status for step in steps)
    parts: list[str] = []
    completed = counts[WorkflowStepStatus.COMPLETED]
    if completed:
        parts.append(f"{completed} completados")
    failed = counts[WorkflowStepStatus.FAILED]
    if failed:
        parts.append(f"{failed} fallidos")
    cancelled = counts[WorkflowStepStatus.CANCELLED]
    if cancelled:
        parts.append(f"{cancelled} cancelados")
    running = (
        counts[WorkflowStepStatus.PREPARING]
        + counts[WorkflowStepStatus.RENDERING]
        + counts[WorkflowStepStatus.DOWNLOADING]
    )
    if running:
        parts.append(f"{running} en curso")
    queued = counts[WorkflowStepStatus.QUEUED]
    if queued:
        parts.append(f"{queued} pendientes")
    if not parts:
        return f"sin progreso aún (0 de {len(steps)})"
    return ", ".join(parts) + f" de {len(steps)}"


def workflow_status_label(status: WorkflowStatus) -> str:
    """Helper para UI/tests."""
    return status.value
