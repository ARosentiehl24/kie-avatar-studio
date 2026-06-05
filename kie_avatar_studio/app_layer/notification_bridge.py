"""Bridge que conecta los queues de jobs con el `DesktopNotifier`.

Se suscribe a los streams de eventos de los `QueueManager` de video y
audio, detecta transiciones a `COMPLETED` / `FAILED` y dispara una
notificación del SO **una sola vez por job** (los queues pueden reemitir
el mismo evento — ej. al hidratar listeners en pantallas que se
abren/cierran).

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
from typing import Any, Final

from loguru import logger

from ..domain.events import AudioJobUpdated, JobUpdated
from ..domain.models import AudioJob, AudioJobStatus, JobStatus, VideoJob
from ..domain.ports import DesktopNotifier

# Status que disparan toast. Cancelled NO porque la cancelación la
# inicia el usuario (ya sabe que pasó) — sería ruido.
_VIDEO_NOTIFY_STATUS: Final[frozenset[JobStatus]] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED}
)
_AUDIO_NOTIFY_STATUS: Final[frozenset[AudioJobStatus]] = frozenset(
    {AudioJobStatus.COMPLETED, AudioJobStatus.FAILED}
)

# Truncado del label/script en el toast: notify-send y Windows toast
# tienen límite blando (~256 chars body) y los DEs cortan visualmente
# después de la primera línea. Mantener corto y útil.
_LABEL_MAX_LEN: Final[int] = 60


class JobNotificationBridge:
    """Listener de queues que dispara notificaciones del SO al terminar un job.

    El método `attach(...)` registra los listeners en los queues
    correspondientes y devuelve un callable de unsubscribe (mismo
    contrato que `add_listener`). El composition root puede ignorar el
    unsubscribe si vive lo mismo que la app — no es leak en ese caso.
    """

    def __init__(self, notifier: DesktopNotifier) -> None:
        self._notifier = notifier
        # IDs de jobs ya notificados en esta corrida — evita que el
        # mismo COMPLETED dispare varios toasts cuando múltiples
        # pantallas (Cola, Historial, Videos) suben/bajan listeners.
        self._notified_video_ids: set[str] = set()
        self._notified_audio_ids: set[str] = set()
        # Mantenemos referencia fuerte a las tasks fire-and-forget para
        # que el GC no las recoja antes de que el subprocess termine.
        self._pending: set[asyncio.Task[None]] = set()

    # --- API pública: el composition root la usa para wirear ----------

    def on_video_event(self, event: JobUpdated) -> None:
        job = event.job
        if job.status not in _VIDEO_NOTIFY_STATUS:
            return
        if job.id in self._notified_video_ids:
            return
        self._notified_video_ids.add(job.id)
        self._schedule(self._notify_video(job))

    def on_audio_event(self, event: AudioJobUpdated) -> None:
        job = event.job
        if job.status not in _AUDIO_NOTIFY_STATUS:
            return
        if job.id in self._notified_audio_ids:
            return
        self._notified_audio_ids.add(job.id)
        self._schedule(self._notify_audio(job))

    # --- internals -----------------------------------------------------

    def _schedule(self, coro: Coroutine[Any, Any, None]) -> None:
        """Lanza la notificación en background sin bloquear el listener."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Sin loop activo (ej. tests sync): swallow — el caller no
            # está usando el bridge correctamente pero no debe crashear.
            logger.debug("JobNotificationBridge: sin event loop, skipping")
            return
        task: asyncio.Task[None] = loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _notify_video(self, job: VideoJob) -> None:
        success = job.status == JobStatus.COMPLETED
        label = _short_label(job.script or job.id)
        if success:
            title = "✓ Video listo"
            output = job.output_path or "(ver pantalla Videos)"
            message = f"{label}\n→ {output}"
        else:
            title = "✖ Video falló"
            message = f"{label}\n{_short_error(job.error)}"
        await self._notifier.notify(title=title, message=message, success=success)

    async def _notify_audio(self, job: AudioJob) -> None:
        success = job.status == AudioJobStatus.COMPLETED
        label = _short_label(job.label or job.id)
        if success:
            title = "✓ Audio listo"
            message = f"{label}\n→ Escuchá desde Audios (a)"
        else:
            title = "✖ Audio falló"
            message = f"{label}\n{_short_error(job.error)}"
        await self._notifier.notify(title=title, message=message, success=success)


def _short_label(text: str) -> str:
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= _LABEL_MAX_LEN:
        return cleaned
    return cleaned[: _LABEL_MAX_LEN - 1] + "…"


def _short_error(error: str | None) -> str:
    if not error:
        return "(ver logs)"
    return _short_label(error)
