"""Bridge que conecta los queues de jobs con el `DesktopNotifier`.

Se suscribe a los streams de eventos de los `QueueManager` de video,
audio e image; detecta transiciones a `COMPLETED` / `FAILED` y dispara
una notificación del SO **una sola vez por job** (los queues pueden
reemitir el mismo evento — ej. al hidratar listeners en pantallas que
se abren/cierran).

No usa estado mutable de UI: el set de IDs ya notificados es local al
bridge. Si el usuario reinicia la app y un job persistido sigue en
COMPLETED, no se notifica de nuevo (correcto: ya pasó).

Diseñado como componente cross-cutting: no toca UI ni infra
directamente; solo `domain` (eventos, ports). El composition root
(`app.py`) lo arma y lo cablea como listener.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from ..domain.events import (
    AudioJobUpdated,
    ImageJobUpdated,
    JobUpdated,
    WorkflowJobUpdated,
)
from ..domain.models import (
    AudioJob,
    AudioJobStatus,
    ImageJob,
    ImageJobStatus,
    JobStatus,
    VideoJob,
    WorkflowJob,
    WorkflowStatus,
)
from ..domain.ports import DesktopNotifier

# Truncado del label/script en el toast: notify-send y Windows toast
# tienen límite blando (~256 chars body) y los DEs cortan visualmente
# después de la primera línea. Mantener corto y útil.
_LABEL_MAX_LEN: Final[int] = 60


# ---------------------------------------------------------------------------
# Tabla de configuración por tipo de job: única fuente de verdad para los
# tres handlers `on_*_event` (CR-3.7). Si Kie agrega un nuevo tipo de job
# (ej. música), se suma una entrada acá y se cablea desde `app.py`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NotifySpec:
    """Spec de cómo notificar un kind concreto al SO."""

    completed_status: StrEnum
    failed_status: StrEnum
    label_extractor: Any  # Callable[[Job], str] — Any para evitar Generic complexity
    title_ok: str
    title_fail: str
    success_hint: str  # Sufijo del mensaje cuando completed (`→ ...`).


def _video_label(job: VideoJob) -> str:
    return job.script or job.id


def _job_label(job: AudioJob | ImageJob) -> str:
    return job.label or job.id


_VIDEO_SPEC: Final[_NotifySpec] = _NotifySpec(
    completed_status=JobStatus.COMPLETED,
    failed_status=JobStatus.FAILED,
    label_extractor=_video_label,
    title_ok="✅ Video listo",
    title_fail="❌ Video falló",
    success_hint="(ver pantalla Videos)",
)

_AUDIO_SPEC: Final[_NotifySpec] = _NotifySpec(
    completed_status=AudioJobStatus.COMPLETED,
    failed_status=AudioJobStatus.FAILED,
    label_extractor=_job_label,
    title_ok="✅ Audio listo",
    title_fail="❌ Audio falló",
    success_hint="Escuchá desde Audios (a)",
)

_IMAGE_SPEC: Final[_NotifySpec] = _NotifySpec(
    completed_status=ImageJobStatus.COMPLETED,
    failed_status=ImageJobStatus.FAILED,
    label_extractor=_job_label,
    title_ok="✅ Imagen lista",
    title_fail="❌ Imagen falló",
    success_hint="Mirala en Imágenes (i)",
)


def _workflow_label(workflow: WorkflowJob) -> str:
    return workflow.name or workflow.id


_WORKFLOW_SPEC: Final[_NotifySpec] = _NotifySpec(
    completed_status=WorkflowStatus.COMPLETED,
    failed_status=WorkflowStatus.FAILED,
    label_extractor=_workflow_label,
    title_ok="✅ Workflow completado",
    title_fail="❌ Workflow falló",
    success_hint="Mirá los outputs en la carpeta del workflow",
)

_WORKFLOW_PARTIAL_SPEC: Final[_NotifySpec] = _NotifySpec(
    # Tratamos PARTIALLY_FAILED como un "completed with warnings": el usuario
    # tiene algunos outputs útiles pero también algún step que falló.
    completed_status=WorkflowStatus.PARTIALLY_FAILED,
    failed_status=WorkflowStatus.FAILED,
    label_extractor=_workflow_label,
    title_ok="⚠️ Workflow parcialmente completado",
    title_fail="❌ Workflow falló",
    success_hint="Mirá los outputs (algunos steps fallaron, revisá el detalle)",
)

# Spec especial para AWAITING_APPROVAL: solo dispara la notificación "ok"
# (no hay caso "fail" para este estado). Reusamos la estructura pero con
# un `failed_status` ficticio que nunca matchea (CANCELLED no aparece en
# transiciones automáticas hacia este estado).
_WORKFLOW_APPROVAL_SPEC: Final[_NotifySpec] = _NotifySpec(
    completed_status=WorkflowStatus.AWAITING_APPROVAL,
    failed_status=WorkflowStatus.CANCELLED,
    label_extractor=_workflow_label,
    title_ok="⏳ Workflow esperando revisión manual",
    title_fail="",  # no usado: la transición a CANCELLED desde awaiting es manual
    success_hint="Abrí Automatización → Revisar escena para continuar",
)


class JobNotificationBridge:
    """Listener de queues que dispara notificaciones del SO al terminar un job.

    Los handlers `on_video_event` / `on_audio_event` / `on_image_event` son
    thin wrappers sobre `_handle_event(spec, job)` que centraliza la lógica
    de dedup + scheduling + render del toast.
    """

    def __init__(self, notifier: DesktopNotifier) -> None:
        self._notifier = notifier
        # Un set de IDs ya notificados por kind. Usar dict de sets indexado
        # por id(spec) evita tres atributos paralelos y permite agregar
        # nuevos kinds sin tocar el __init__ (CR-2.2 OCP).
        self._notified: dict[int, set[str]] = {
            id(spec): set()
            for spec in (
                _VIDEO_SPEC,
                _AUDIO_SPEC,
                _IMAGE_SPEC,
                _WORKFLOW_SPEC,
                _WORKFLOW_PARTIAL_SPEC,
                _WORKFLOW_APPROVAL_SPEC,
            )
        }
        # Mantenemos referencia fuerte a las tasks fire-and-forget para
        # que el GC no las recoja antes de que el subprocess termine.
        self._pending: set[asyncio.Task[None]] = set()

    # --- API pública: el composition root la usa para wirear ----------

    def on_video_event(self, event: JobUpdated) -> None:
        self._handle_event(_VIDEO_SPEC, event.job)

    def on_audio_event(self, event: AudioJobUpdated) -> None:
        self._handle_event(_AUDIO_SPEC, event.job)

    def on_image_event(self, event: ImageJobUpdated) -> None:
        self._handle_event(_IMAGE_SPEC, event.job)

    def on_workflow_event(self, event: WorkflowJobUpdated) -> None:
        # Despachamos contra tres specs:
        # - _WORKFLOW_SPEC: COMPLETED / FAILED
        # - _WORKFLOW_PARTIAL_SPEC: PARTIALLY_FAILED como "completed con warnings"
        # - _WORKFLOW_APPROVAL_SPEC: AWAITING_APPROVAL (acción humana requerida)
        # El dedup por (spec, id) garantiza una notificación por kind.
        self._handle_event(_WORKFLOW_SPEC, event.job)
        self._handle_event(_WORKFLOW_PARTIAL_SPEC, event.job)
        self._handle_event(_WORKFLOW_APPROVAL_SPEC, event.job)

    # --- internals -----------------------------------------------------

    def _handle_event(
        self,
        spec: _NotifySpec,
        job: VideoJob | AudioJob | ImageJob | WorkflowJob,
    ) -> None:
        seen = self._notified[id(spec)]
        # Dedup transient: para AWAITING_APPROVAL, si el workflow ya salió
        # del estado pausado (porque el usuario aprobó/regeneró/canceló y
        # volvió a QUEUED), liberamos el seen para poder volver a notificar
        # si pausa de nuevo. Sin esto, multi-step MANUAL solo notifica una
        # vez por workflow_id durante toda su vida.
        if job.status not in (spec.completed_status, spec.failed_status):
            if job.id in seen and spec is _WORKFLOW_APPROVAL_SPEC:
                seen.discard(job.id)
            return
        if job.id in seen:
            return
        seen.add(job.id)
        self._schedule(self._notify(spec, job))

    async def _notify(
        self,
        spec: _NotifySpec,
        job: VideoJob | AudioJob | ImageJob | WorkflowJob,
    ) -> None:
        success = job.status == spec.completed_status
        label = _short_label(spec.label_extractor(job))
        if success:
            # Para video usamos el output_path concreto si está; para los
            # otros el hint genérico del spec. WorkflowJob.output_dir
            # apunta al directorio con los outputs por step.
            output = getattr(job, "output_path", None) or getattr(job, "output_dir", None)
            hint = output if output else spec.success_hint
            message = f"{label}\n→ {hint}"
            title = spec.title_ok
        else:
            message = f"{label}\n{_short_error(job.error)}"
            title = spec.title_fail
        await self._notifier.notify(title=title, message=message, success=success)

    def _schedule(self, coro: Coroutine[Any, Any, None]) -> None:
        """Lanza la notificación en background sin bloquear el listener."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Sin loop activo (ej. tests sync): swallow — el caller no
            # está usando el bridge correctamente pero no debe crashear.
            from loguru import logger

            logger.debug("JobNotificationBridge: sin event loop, skipping")
            return
        task: asyncio.Task[None] = loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


def _short_label(text: str) -> str:
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= _LABEL_MAX_LEN:
        return cleaned
    return cleaned[: _LABEL_MAX_LEN - 1] + "…"


def _short_error(error: str | None) -> str:
    if not error:
        return "(ver logs)"
    return _short_label(error)
