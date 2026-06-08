"""Lifecycle de `ImageJob` separado del `QueueManager`.

Espejo de `AudioJobLifecycle` pero para imágenes Nano Banana 2.
Encapsula las reglas de cancelación/reintento y persiste antes de mutar
memoria (write-ahead).
"""

from __future__ import annotations

from typing import Final

from ..domain.models import ImageJob, ImageJobStatus
from ..domain.ports import ImageJobRepository

_NON_CANCELLABLE_STATUSES: Final[frozenset[ImageJobStatus]] = frozenset(
    {ImageJobStatus.COMPLETED, ImageJobStatus.FAILED, ImageJobStatus.CANCELLED}
)

_RETRYABLE_STATUSES: Final[frozenset[ImageJobStatus]] = frozenset(
    {ImageJobStatus.FAILED, ImageJobStatus.CANCELLED}
)


class ImageJobLifecycle:
    """Implementa `domain.ports.JobLifecycle[ImageJob]`.

    Política idéntica a `AudioJobLifecycle`:
    - Cancellable mientras no esté en estado terminal. Cancelar durante
      POLLING simplemente deja de pollear (los créditos ya se consumieron
      al crear el task en Kie).
    - Retryable solo desde FAILED o CANCELLED.
    - Las transiciones persisten ANTES de mutar el objeto en memoria.
    """

    def __init__(self, repository: ImageJobRepository) -> None:
        self._repository = repository

    def is_cancellable(self, job: ImageJob) -> bool:
        return job.status not in _NON_CANCELLABLE_STATUSES

    def is_retryable(self, job: ImageJob) -> bool:
        return job.status in _RETRYABLE_STATUSES

    async def mark_cancelled(self, job: ImageJob) -> None:
        job.status = ImageJobStatus.CANCELLED
        await self._repository.upsert(job)

    async def reset_for_retry(self, job: ImageJob) -> None:
        job.status = ImageJobStatus.QUEUED
        job.error = None
        # Limpiamos el task_id viejo: el reintento crea un task nuevo
        # en Kie. Si lo dejáramos, el polling podría caer sobre un task
        # que ya expiró (Kie tiene TTL de ~24h para tasks).
        job.task_id = None
        await self._repository.upsert(job)
