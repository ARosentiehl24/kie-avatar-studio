"""Lifecycle de `VideoJob` separado del `QueueManager`.

Encapsula las reglas específicas de transición de estado para video jobs:
qué estados son cancellables/retryables, y cómo persistir el cambio antes
de mutar memoria. Permite que `QueueManager` sea type-agnostic (igual de
útil para `AudioJob`).
"""

from __future__ import annotations

from typing import Final

from ..domain.models import JobStatus, VideoJob
from ..domain.ports import JobRepository

_NON_CANCELLABLE_STATUSES: Final[frozenset[JobStatus]] = frozenset(
    {JobStatus.DOWNLOADING, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

_RETRYABLE_STATUSES: Final[frozenset[JobStatus]] = frozenset(
    {JobStatus.FAILED, JobStatus.CANCELLED}
)


class VideoJobLifecycle:
    """Implementa `domain.ports.JobLifecycle[VideoJob]`.

    Política:
    - Cancellable mientras no esté descargando ni en estado terminal.
    - Retryable solo desde FAILED o CANCELLED.
    - `mark_cancelled` y `reset_for_retry` persisten el estado en
      `JobRepository` ANTES de mutar el objeto en memoria (write-ahead,
      coherente con `JobRunner._transition`).
    """

    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    def is_cancellable(self, job: VideoJob) -> bool:
        return job.status not in _NON_CANCELLABLE_STATUSES

    def is_retryable(self, job: VideoJob) -> bool:
        return job.status in _RETRYABLE_STATUSES

    async def mark_cancelled(self, job: VideoJob) -> None:
        job.status = JobStatus.CANCELLED
        await self._repository.upsert(job)

    async def reset_for_retry(self, job: VideoJob) -> None:
        job.status = JobStatus.QUEUED
        job.error = None
        await self._repository.upsert(job)
