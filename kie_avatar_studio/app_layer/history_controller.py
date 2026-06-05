"""Controller del Historial unificado (video jobs + audio jobs).

Agrega los dos repositorios + las dos colas detrás de una API simple
para que `HistoryScreen` no tenga que saber que existen dos tipos de
job. Mantiene SRP: el controller solo proyecta los modelos a
`HistoryEntry` (definido en `domain.events`) y normaliza las
suscripciones a un único callback genérico.

Decisiones clave:

- **Solo lectura**: las acciones (cancel, retry, delete) se hacen en
  las pantallas específicas (`AudiosScreen`, futura `VideoJobsScreen`).
  Mantener acá solo el listado evita duplicar la lógica de mutación.

- **Suscripción a ambas queues**: `subscribe(callback)` registra un
  listener en cada queue interno y devuelve un callable que desuscribe
  ambos. El callback recibe `HistoryEntry` ya normalizado para que la
  UI no tenga que matchear tipos.

- **Ordenamiento por `created_at` desc** en `list_recent_entries`:
  últimos primero, igual que las pantallas individuales.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from ..domain.events import (
    AudioJobUpdated,
    HistoryEntry,
    JobUpdated,
)
from ..domain.models import AudioJob, VideoJob
from ..domain.ports import AudioJobRepository, JobRepository
from .queue_manager import QueueManager

_DEFAULT_LIMIT: Final[int] = 100

HistoryListener = Callable[[HistoryEntry], None]


class HistoryController:
    """Vista de solo lectura sobre la actividad de video y audio."""

    def __init__(
        self,
        video_repo: JobRepository,
        audio_repo: AudioJobRepository,
        video_queue: QueueManager[VideoJob, JobUpdated],
        audio_queue: QueueManager[AudioJob, AudioJobUpdated],
    ) -> None:
        self._video_repo = video_repo
        self._audio_repo = audio_repo
        self._video_queue = video_queue
        self._audio_queue = audio_queue

    async def list_recent_entries(self, limit: int = _DEFAULT_LIMIT) -> list[HistoryEntry]:
        """Lista los jobs recientes de ambos tipos, ordenados por created_at desc.

        Pide `limit` de cada tipo y mergea: el total devuelto puede ser
        hasta `2 * limit` filas. Es intencional — el caller decide si
        quiere truncar para la UI. Esto evita el bug de "te dije 50
        pero te di los 50 más nuevos de un solo tipo si el otro está
        vacío".
        """
        video_jobs = await self._video_repo.list_recent(limit)
        audio_jobs = await self._audio_repo.list_recent(limit)
        entries: list[HistoryEntry] = [HistoryEntry.from_video_job(j) for j in video_jobs]
        entries.extend(HistoryEntry.from_audio_job(j) for j in audio_jobs)
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries

    def subscribe(self, callback: HistoryListener) -> Callable[[], None]:
        """Registra un listener en ambas colas, normalizando a `HistoryEntry`.

        El callback recibe la entrada proyectada — no necesita conocer
        VideoJob ni AudioJob. Solo se acepta callback sync porque el uso
        previsto es la UI Textual que postea un Message (sync). Si se
        quisiera soportar async, replicar el dispatch de
        `QueueManager._dispatch_listener` acá.

        Devuelve un callable que desuscribe los DOS listeners (uno en
        cada queue) atómicamente.
        """

        def on_video(event: JobUpdated) -> None:
            callback(HistoryEntry.from_video_job(event.job))

        def on_audio(event: AudioJobUpdated) -> None:
            callback(HistoryEntry.from_audio_job(event.job))

        unsubscribe_video = self._video_queue.add_listener(on_video)
        unsubscribe_audio = self._audio_queue.add_listener(on_audio)

        def unsubscribe_both() -> None:
            unsubscribe_video()
            unsubscribe_audio()

        return unsubscribe_both
