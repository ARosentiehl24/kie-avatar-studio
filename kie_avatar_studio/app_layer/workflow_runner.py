"""`WorkflowRunner`: orquesta UN `WorkflowJob` end-to-end.

Responsabilidades:
1. Llamar a `WorkflowBaseResolver` para resolver voice + imagen base.
2. Lanzar todos los steps en paralelo respetando el `_capacity_limiter`
   global (cada step adquiere slots cuando llega a sub-jobs reales).
3. Serializar las transiciones de los steps con un `asyncio.Lock` por
   workflow (evita lost updates concurrentes).
4. Persistir y regenerar el manifest atómicamente en cada transición.
5. Emitir el evento `WorkflowJobUpdated` al listener del queue.
6. Decidir el status final (COMPLETED / PARTIALLY_FAILED / FAILED).

Cumple `RunnableRunner[WorkflowJob]` del puerto `domain.ports`.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..config import Settings
from ..domain.errors import (
    KieError,
    StepAwaitingApprovalSignal,
    WorkflowStepError,
    WorkflowValidationError,
)
from ..domain.models import (
    ImageAssetRef,
    SceneApprovalMode,
    WorkflowJob,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from ..domain.policies import validate_workflow
from ..domain.ports import (
    KieGateway,
    WorkflowManifestWriter,
    WorkflowRepository,
)
from .workflow_base_resolver import WorkflowBaseResolver
from .workflow_execution_context import WorkflowExecutionContext
from .workflow_step_runner import WorkflowStepRunner

WorkflowNotify = Callable[[WorkflowJob], Awaitable[None] | None]
"""Callback opcional que se llama tras cada transición (UI listener)."""


@dataclass(frozen=True, slots=True)
class WorkflowRunnerDeps:
    """Dependencias del `WorkflowRunner` agrupadas (CR-3.1 ≤4 args)."""

    repository: WorkflowRepository
    manifest_writer: WorkflowManifestWriter
    step_runner: WorkflowStepRunner
    base_resolver: WorkflowBaseResolver


class WorkflowRunner:
    """Orquesta un `WorkflowJob` end-to-end y emite eventos al callback."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        deps: WorkflowRunnerDeps,
        *,
        notify: WorkflowNotify | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._repository = deps.repository
        self._manifest_writer = deps.manifest_writer
        self._step_runner = deps.step_runner
        self._base_resolver = deps.base_resolver
        self._notify = notify
        # Lock por workflow_id para serializar transiciones (steps paralelos
        # transicionando contra el mismo workflow object).
        self._locks: dict[str, asyncio.Lock] = {}

    def set_notify(self, notify: WorkflowNotify | None) -> None:
        """Permite cablear el callback de eventos después del __init__.

        Útil para el composition root cuando el queue se construye después
        del runner pero ambos se referencian mutuamente.
        """
        self._notify = notify

    async def run(self, job: WorkflowJob) -> WorkflowJob:
        try:
            validate_workflow(job)
            voice_id, voice_settings = await self._base_resolver.resolve_voice(job)
            await self._mark_preparing_base(job)
            base_ref = await self._base_resolver.resolve_base_image(job)
            output_dir = Path(job.output_dir)
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
            await self._base_resolver.download_base_locally(base_ref, output_dir)
            await self._mark_running(job)
            context = WorkflowExecutionContext(
                audio_language=job.pre_settings.audio_language,
                voice_id=voice_id,
                voice_settings=voice_settings,
                base_image_ref=base_ref,
                output_dir=output_dir,
                i2v_duration_seconds_override=job.pre_settings.i2v_duration_seconds,
                scene_approval_mode=job.pre_settings.scene_approval_mode,
                product_image_ref=self._resolve_product_ref(job),
                image_aspect_ratio=job.pre_settings.image_aspect_ratio,
            )
            paused = await self._execute_steps(job, context)
            if paused:
                # Al menos un step quedó AWAITING_APPROVAL. Marcamos el
                # workflow como AWAITING_APPROVAL y liberamos el slot del
                # semáforo. El controller `approve_scene` lo re-encolará
                # cuando el usuario revise.
                await self._mark_awaiting_approval(job)
            else:
                await self._finalize_workflow(job)
        except asyncio.CancelledError:
            await self._mark_cancelled(job)
            raise
        except (WorkflowValidationError, WorkflowStepError, KieError) as exc:
            await self._fail_workflow(job, exc)
        except Exception as exc:
            logger.exception("WorkflowJob {} falló con error no manejado", job.id)
            await self._fail_workflow(job, exc)
        finally:
            # Limpiamos el lock del workflow al terminar (evita fugas de memoria)
            self._locks.pop(job.id, None)
        return job

    @staticmethod
    def _resolve_product_ref(job: WorkflowJob) -> ImageAssetRef | None:
        """Devuelve la ref Kie del producto si el workflow lo promociona.

        El producto se pre-resuelve en la UI antes de encolar (file picker
        + upload), igual que `method=local` de la imagen base. Acá solo lo
        leemos de `pre_settings.product_image.resolved_image_ref`.

        Deuda (v1): si la ref expiró (24h en cola), NO se re-sube en runtime
        — el `local_path` queda en `pre_settings` para una mejora futura.
        Mismo trade-off que la imagen base con `method=local` hoy.
        """
        pre = job.pre_settings
        if not pre.promote_product or pre.product_image is None:
            return None
        return pre.product_image.resolved_image_ref

    # --- step orchestration -----------------------------------------------

    async def _execute_steps(self, job: WorkflowJob, context: WorkflowExecutionContext) -> bool:
        """Lanza los steps. Devuelve True si al menos uno quedó AWAITING_APPROVAL.

        Filtramos steps ya completados (is_terminal() == True).
        Para el paralelismo:
        - Si estamos en `SceneApprovalMode.MANUAL`, ejecutamos los steps en
          SERIE (secuencialmente) y detenemos inmediatamente en el primer step
          que requiera aprobación. Esto evita que se ejecuten steps siguientes
          en paralelo y se gasten créditos/tiempo innecesariamente antes de que
          el usuario apruebe, resolviendo el bug de quedar atascado en 'running'
          mientras se espera por steps paralelos lejanos (ej. step 12).
        - Si estamos en `SceneApprovalMode.AUTO`, los ejecutamos todos en
          PARALELO (concurrente) para máximo rendimiento.
        """
        pending_steps = [s for s in job.steps if not s.is_terminal()]
        if not pending_steps:
            return False

        if context.scene_approval_mode == SceneApprovalMode.MANUAL:
            # Ejecución en serie (secuencial) para MANUAL
            paused = False
            for s in pending_steps:
                try:
                    await self._step_runner.run(s, context, self._build_step_transition(job))
                except StepAwaitingApprovalSignal:
                    paused = True
                    break  # Detener ejecución inmediatamente: no correr steps siguientes
            return paused

        # Ejecución en paralelo (concurrente) para AUTO (comportamiento clásico)
        paused = False

        async def _run_one(step: WorkflowStep) -> None:
            nonlocal paused
            try:
                await self._step_runner.run(step, context, self._build_step_transition(job))
            except StepAwaitingApprovalSignal:
                paused = True

        tasks = [
            asyncio.create_task(_run_one(s), name=f"wf-{job.id}-step-{s.step}")
            for s in pending_steps
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Inspeccionar si hubo excepciones inesperadas (excluyendo cancelaciones)
        # para no tragarnos errores del propio runner.
        unexpected = [
            r
            for r in results
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError)
        ]
        if unexpected:
            raise unexpected[0]
        return paused

    def _build_step_transition(self, job: WorkflowJob) -> Callable[[WorkflowStep], Awaitable[None]]:
        """Crea el callback que el step runner llama tras cada transición.

        Serializa con el lock del workflow para evitar lost updates.
        Persiste el step + regenera el manifest + notifica al listener.
        """
        lock = self._lock_for(job.id)

        async def _on_transition(step: WorkflowStep) -> None:
            async with lock:
                await self._repository.upsert_step(job.id, step)
                await self._write_manifest(job)
                await self._dispatch_notify(job)

        return _on_transition

    def _lock_for(self, workflow_id: str) -> asyncio.Lock:
        lock = self._locks.get(workflow_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[workflow_id] = lock
        return lock

    # --- workflow header transitions --------------------------------------

    async def _mark_preparing_base(self, job: WorkflowJob) -> None:
        await self._update_header(job, WorkflowStatus.PREPARING_BASE)

    async def _mark_running(self, job: WorkflowJob) -> None:
        await self._update_header(job, WorkflowStatus.RUNNING)

    async def _mark_cancelled(self, job: WorkflowJob) -> None:
        await self._update_header(job, WorkflowStatus.CANCELLED)

    async def _mark_awaiting_approval(self, job: WorkflowJob) -> None:
        """Pausa el workflow esperando aprobación humana de scene_image."""
        await self._update_header(job, WorkflowStatus.AWAITING_APPROVAL)
        logger.info(
            "WorkflowJob {} ({}) pausado en AWAITING_APPROVAL (modo manual)",
            job.id,
            job.name,
        )

    async def _fail_workflow(self, job: WorkflowJob, exc: BaseException) -> None:
        job.error = str(exc) or exc.__class__.__name__
        await self._update_header(job, WorkflowStatus.FAILED)

    async def _finalize_workflow(self, job: WorkflowJob) -> None:
        statuses = [s.status for s in job.steps]
        if all(s == WorkflowStepStatus.COMPLETED for s in statuses):
            final = WorkflowStatus.COMPLETED
        elif any(s == WorkflowStepStatus.COMPLETED for s in statuses):
            final = WorkflowStatus.PARTIALLY_FAILED
        else:
            final = WorkflowStatus.FAILED
        await self._update_header(job, final)
        logger.info(
            "WorkflowJob {} ({}) finalizado con status={}",
            job.id,
            job.name,
            final.value,
        )

    async def _update_header(self, job: WorkflowJob, status: WorkflowStatus) -> None:
        job.status = status
        await self._repository.update_workflow_header(job)
        await self._write_manifest(job)
        await self._dispatch_notify(job)

    # --- helpers ----------------------------------------------------------

    async def _write_manifest(self, job: WorkflowJob) -> None:
        """Regenera el manifest atómicamente. Fail-safe (nunca levanta).

        Si la escritura falla permanentemente, marca `manifest_write_failed`
        y persiste solo el header (no toca steps). El runner sigue.
        """
        ok = await self._manifest_writer.write(job)
        if not ok and not job.manifest_write_failed:
            job.manifest_write_failed = True
            try:
                await self._repository.update_workflow_header(job)
            except Exception:
                logger.exception(
                    "No se pudo persistir manifest_write_failed=True en workflow {}",
                    job.id,
                )

    async def _dispatch_notify(self, job: WorkflowJob) -> None:
        if self._notify is None:
            return
        try:
            result = self._notify(job)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.opt(exception=True).warning("listener de workflow falló")


__all__ = ["WorkflowRunner", "WorkflowRunnerDeps"]
