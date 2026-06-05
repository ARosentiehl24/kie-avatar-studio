"""Eventos del dominio que las capas superiores pueden propagar a la UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .models import AudioJob, AudioJobStatus, ImageJob, ImageJobStatus, JobStatus, VideoJob


@dataclass(frozen=True, slots=True)
class JobUpdated:
    """Notificación de que un `VideoJob` cambió de estado o de campos relevantes."""

    job: VideoJob


@dataclass(frozen=True, slots=True)
class AudioJobUpdated:
    """Notificación de que un `AudioJob` cambió de estado o campos relevantes.

    Evento separado de `JobUpdated` (no genérico) para que las pantallas
    puedan suscribirse al stream correcto sin tener que matchear runtime
    types. Mismo `dataclass` slim que `JobUpdated`.
    """

    job: AudioJob


@dataclass(frozen=True, slots=True)
class ImageJobUpdated:
    """Notificación de que un `ImageJob` cambió de estado o campos relevantes.

    Mismo patrón que `AudioJobUpdated`: un evento por tipo de job para
    evitar que las pantallas tengan que matchear runtime types al
    suscribirse al `QueueManager` correspondiente.
    """

    job: ImageJob


JobKind = Literal["video", "audio", "image"]


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """Vista normalizada de un job (video, audio o image) para la pantalla Historial.

    Permite que la `HistoryScreen` muestre una tabla unificada sin tener
    que conocer la diferencia entre `VideoJob`, `AudioJob` e `ImageJob`.
    El `raw` queda disponible si alguna fila necesita atributos específicos
    (típicamente para abrir la pantalla nativa del job).

    Status se proyecta a su `value` para que la pantalla solo trabaje con
    strings — los enums concretos siguen vivos en los respectivos
    modelos pero acá los aplanamos para evitar branching por tipo en la
    UI.
    """

    kind: JobKind
    id: str
    label: str
    status_value: str
    detail: str  # script (audio), prompt (video) o prompt (image) — preview para la tabla.
    created_at: datetime
    raw: VideoJob | AudioJob | ImageJob

    @classmethod
    def from_video_job(cls, job: VideoJob) -> HistoryEntry:
        return cls(
            kind="video",
            id=job.id,
            label=_video_label(job),
            status_value=job.status.value,
            detail=job.prompt,
            created_at=job.created_at,
            raw=job,
        )

    @classmethod
    def from_audio_job(cls, job: AudioJob) -> HistoryEntry:
        return cls(
            kind="audio",
            id=job.id,
            label=job.label,
            status_value=job.status.value,
            detail=job.script,
            created_at=job.created_at,
            raw=job,
        )

    @classmethod
    def from_image_job(cls, job: ImageJob) -> HistoryEntry:
        return cls(
            kind="image",
            id=job.id,
            label=job.label,
            status_value=job.status.value,
            detail=job.prompt,
            created_at=job.created_at,
            raw=job,
        )


_VIDEO_LABEL_MAX_LEN: int = 40


def _video_label(job: VideoJob) -> str:
    """`VideoJob` no tiene label; usamos el inicio del script como fallback."""
    text = job.script.strip()
    if not text:
        return job.id
    if len(text) <= _VIDEO_LABEL_MAX_LEN:
        return text
    return text[: _VIDEO_LABEL_MAX_LEN - 1] + "…"


# Set unificado de status terminales (para chequeos rápidos en UI).
_TERMINAL_VIDEO_STATUS_VALUES: frozenset[str] = frozenset(
    {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}
)
_TERMINAL_AUDIO_STATUS_VALUES: frozenset[str] = frozenset(
    {
        AudioJobStatus.COMPLETED.value,
        AudioJobStatus.FAILED.value,
        AudioJobStatus.CANCELLED.value,
    }
)
_TERMINAL_IMAGE_STATUS_VALUES: frozenset[str] = frozenset(
    {
        ImageJobStatus.COMPLETED.value,
        ImageJobStatus.FAILED.value,
        ImageJobStatus.CANCELLED.value,
    }
)
TERMINAL_HISTORY_STATUS_VALUES: frozenset[str] = (
    _TERMINAL_VIDEO_STATUS_VALUES | _TERMINAL_AUDIO_STATUS_VALUES | _TERMINAL_IMAGE_STATUS_VALUES
)


@dataclass(frozen=True, slots=True)
class JobLog:
    """Línea de log asociada a un job, útil para mostrar en `job_detail`."""

    job_id: str
    level: str
    message: str
