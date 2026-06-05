"""Lifecycle de `AudioJob` separado del `QueueManager`.

Espejo de `VideoJobLifecycle` pero para audios. Encapsula las reglas
de qué estados son cancellables/retryables y persiste el cambio antes
de mutar memoria (write-ahead, igual que el video).
"""

from __future__ import annotations

from typing import Final

from ..domain.models import AudioJob, AudioJobStatus
from ..domain.ports import AudioJobRepository

_NON_CANCELLABLE_STATUSES: Final[frozenset[AudioJobStatus]] = frozenset(
    {AudioJobStatus.COMPLETED, AudioJobStatus.FAILED, AudioJobStatus.CANCELLED}
)

_RETRYABLE_STATUSES: Final[frozenset[AudioJobStatus]] = frozenset(
    {AudioJobStatus.FAILED, AudioJobStatus.CANCELLED}
)


class AudioJobLifecycle:
    """Implementa `domain.ports.JobLifecycle[AudioJob]`.

    Política:
    - Cancellable mientras no esté en estado terminal. A diferencia de
      `VideoJob`, no hay paso de descarga: cancelar durante POLLING
      simplemente deja de pollear (los créditos ya se consumieron al
      crear el task en Kie — ver SPEC §6.4).
    - Retryable solo desde FAILED o CANCELLED.
    - `mark_cancelled` y `reset_for_retry` persisten ANTES de mutar el
      objeto en memoria.
    """

    def __init__(self, repository: AudioJobRepository) -> None:
        self._repository = repository

    def is_cancellable(self, job: AudioJob) -> bool:
        return job.status not in _NON_CANCELLABLE_STATUSES

    def is_retryable(self, job: AudioJob) -> bool:
        return job.status in _RETRYABLE_STATUSES

    async def mark_cancelled(self, job: AudioJob) -> None:
        job.status = AudioJobStatus.CANCELLED
        await self._repository.upsert(job)

    async def reset_for_retry(self, job: AudioJob) -> None:
        job.status = AudioJobStatus.QUEUED
        job.error = None
        # Limpiamos el task_id viejo: el reintento crea un task nuevo
        # en Kie. Si lo dejáramos, el polling podría caer sobre un task
        # que ya expiró (Kie tiene TTL de ~24h para tasks).
        job.task_id = None
        await self._repository.upsert(job)
