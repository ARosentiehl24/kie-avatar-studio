"""Formatters de filas para la pantalla `Videos` (cola de Kling Avatar Pro).

Extraído de `videos.py` para mantener ese archivo bajo el límite de
CR-3.2 (≤300 líneas). Mantiene SRP: este módulo solo proyecta
`VideoJob` a strings que la `DataTable` consume + cuenta status para
el header de contadores.

Las constantes `_ACTIVE_STATUSES`, `_QUEUED_STATUSES`, `_DONE_STATUSES`
y `_FAILED_STATUSES` también se exportan acá para que el agrupamiento
de estados viva junto con el formatter que los usa (cohesión).
"""

from __future__ import annotations

from typing import Final

from ...domain.models import JobStatus, VideoJob
from .._counters import format_full_counters
from .._icons import OK
from .._text_format import truncate

_PATH_PREVIEW_LEN: Final[int] = 28

_ACTIVE_STATUSES: Final[frozenset[JobStatus]] = frozenset(
    {
        JobStatus.VALIDATING,
        JobStatus.UPLOADING_IMAGE,
        JobStatus.CREATING_AUDIO,
        JobStatus.WAITING_AUDIO,
        JobStatus.CREATING_AVATAR,
        JobStatus.WAITING_VIDEO,
        JobStatus.DOWNLOADING,
    }
)
_QUEUED_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.QUEUED})
_DONE_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.COMPLETED})
_FAILED_STATUSES: Final[frozenset[JobStatus]] = frozenset({JobStatus.FAILED, JobStatus.CANCELLED})


def format_assets(job: VideoJob) -> str:
    """Resumen breve de los assets del job para la tabla."""
    parts: list[str] = []
    if job.image_url:
        parts.append(f"📷 {OK}")
    elif job.image_path:
        parts.append(f"📷 {truncate(job.image_path, 14)}")
    if job.audio_url:
        parts.append(f"🔊 {OK}")
    elif job.voice:
        parts.append(f"🔊 {truncate(job.voice, 10)}")
    return "  ".join(parts) if parts else "—"


def format_output(job: VideoJob) -> str:
    """Columna 'Output / Task': output_path si COMPLETED, task_id si en progreso."""
    if job.status == JobStatus.COMPLETED and job.output_path:
        return truncate(job.output_path, _PATH_PREVIEW_LEN)
    if job.video_task_id:
        return f"[dim]video: {truncate(job.video_task_id, _PATH_PREVIEW_LEN - 7)}[/dim]"
    if job.audio_task_id:
        return f"[dim]audio: {truncate(job.audio_task_id, _PATH_PREVIEW_LEN - 7)}[/dim]"
    if job.status == JobStatus.FAILED and job.error:
        return f"[red]{truncate(job.error, _PATH_PREVIEW_LEN)}[/red]"
    return "—"


def compute_counters(jobs: list[VideoJob]) -> tuple[int, int, int, int, int]:
    """Cuenta (total, active, queued, done, failed) para el panel superior."""
    active = sum(1 for j in jobs if j.status in _ACTIVE_STATUSES)
    queued = sum(1 for j in jobs if j.status in _QUEUED_STATUSES)
    done = sum(1 for j in jobs if j.status in _DONE_STATUSES)
    failed = sum(1 for j in jobs if j.status in _FAILED_STATUSES)
    return len(jobs), active, queued, done, failed


def format_counters(total: int, active: int, queued: int, done: int, failed: int) -> str:
    """Wrapper sobre `ui._counters.format_full_counters` con label semántico."""
    return format_full_counters(total, active, queued, done, failed, active_label="generando")
