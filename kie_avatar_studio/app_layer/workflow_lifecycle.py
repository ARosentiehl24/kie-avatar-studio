"""Lifecycle de `WorkflowJob` separado del `QueueManager`.

Implementa `domain.ports.JobLifecycle[WorkflowJob]`. Reglas:
- Cancellable mientras no esté en estado terminal. Cancelar durante
  PREPARING_BASE / RUNNING aborta los sub-jobs en curso (el runner
  les emite cancel a su asyncio.Task).
- Retryable solo desde FAILED / CANCELLED / PARTIALLY_FAILED.
- Las transiciones persisten ANTES de mutar el objeto en memoria
  (write-ahead) — `update_workflow_header` para no tocar los steps.
"""

from __future__ import annotations

from typing import Final

from ..domain.models import WorkflowJob, WorkflowStatus
from ..domain.ports import WorkflowRepository

_NON_CANCELLABLE_STATUSES: Final[frozenset[WorkflowStatus]] = frozenset(
    {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.PARTIALLY_FAILED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    }
)

_RETRYABLE_STATUSES: Final[frozenset[WorkflowStatus]] = frozenset(
    {
        WorkflowStatus.FAILED,
        WorkflowStatus.PARTIALLY_FAILED,
        WorkflowStatus.CANCELLED,
    }
)


class WorkflowLifecycle:
    """Implementa `domain.ports.JobLifecycle[WorkflowJob]`."""

    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def is_cancellable(self, job: WorkflowJob) -> bool:
        return job.status not in _NON_CANCELLABLE_STATUSES

    def is_retryable(self, job: WorkflowJob) -> bool:
        return job.status in _RETRYABLE_STATUSES

    async def mark_cancelled(self, job: WorkflowJob) -> None:
        original = job.status
        job.status = WorkflowStatus.CANCELLED
        try:
            await self._repository.update_workflow_header(job)
        except Exception:
            job.status = original
            raise

    async def reset_for_retry(self, job: WorkflowJob) -> None:
        """Resetea el header y todos los steps FAILED / CANCELLED a QUEUED.

        Reseteamos los steps individuales fallidos o cancelados para que no sean
        considerados 'terminales' por el runner y se vuelvan a ejecutar
        correctamente en la nueva pasada.
        """
        from ..domain.models import WorkflowStepStatus

        original_status = job.status
        original_error = job.error
        original_manifest = job.manifest_write_failed

        job.status = WorkflowStatus.QUEUED
        job.error = None
        job.manifest_write_failed = False
        try:
            await self._repository.update_workflow_header(job)
        except Exception:
            job.status = original_status
            job.error = original_error
            job.manifest_write_failed = original_manifest
            raise

        for step in job.steps:
            if step.status in (WorkflowStepStatus.FAILED, WorkflowStepStatus.CANCELLED):
                original_step_status = step.status
                original_step_error = step.error
                original_step_completed = step.completed_at
                original_step_started = step.started_at
                original_step_progress = step.progress.copy()
                original_step_audio = step.audio_job_id
                original_step_video = step.video_task_id

                step.status = WorkflowStepStatus.QUEUED
                step.error = None
                step.completed_at = None
                step.started_at = None
                step.progress.clear()
                # Forzar recreación de tareas fallidas (audio, video)
                step.audio_job_id = None
                step.video_task_id = None
                try:
                    await self._repository.upsert_step(job.id, step)
                except Exception:
                    step.status = original_step_status
                    step.error = original_step_error
                    step.completed_at = original_step_completed
                    step.started_at = original_step_started
                    step.progress = original_step_progress
                    step.audio_job_id = original_step_audio
                    step.video_task_id = original_step_video
                    raise
