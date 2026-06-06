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
        job.status = WorkflowStatus.CANCELLED
        await self._repository.update_workflow_header(job)

    async def reset_for_retry(self, job: WorkflowJob) -> None:
        """Resetea solo el header. NO toca los steps (el runner decide qué
        re-ejecutar según el estado individual de cada step).
        """
        job.status = WorkflowStatus.QUEUED
        job.error = None
        job.manifest_write_failed = False
        await self._repository.update_workflow_header(job)
