"""Formatters de filas para la pantalla `Imágenes` (galería mixta).

Extraído de `images.py` para mantener ese archivo bajo el límite de
CR-3.2 (≤300 líneas). Mantiene SRP: este módulo solo proyecta los
modelos del dominio a tuplas de strings que la `DataTable` consume.

Convenciones:
- Cada `_row_for_*` devuelve una tupla con los mismos 7 campos en el
  mismo orden, alineado con `_TABLE_COLUMNS` de `images.py`.
- `_format_size` y `_format_time_left` son helpers reutilizables si
  el día de mañana otra pantalla muestra timestamps de expiración con
  el mismo formato.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from ...domain.models import GeneratedImage, ImageJob, UploadedImage
from .._icons import ERROR, OK

_BYTES_PER_MB: Final[float] = 1024 * 1024
_SECONDS_PER_MINUTE: Final[int] = 60
_SECONDS_PER_HOUR: Final[int] = 60 * _SECONDS_PER_MINUTE
_SECONDS_PER_DAY: Final[int] = 24 * _SECONDS_PER_HOUR


def row_for_uploaded(image: UploadedImage, retention_hours: int) -> tuple[str, ...]:
    """Proyecta un `UploadedImage` a la fila de la tabla mixta."""
    local_flag = ("local " + OK) if image.local_file_exists() else ("local " + ERROR)
    return (
        "subida",
        image.id,
        image.label,
        f"{image.mime_type} ({local_flag})",
        format_size(image.file_size),
        image.uploaded_at.strftime("%Y-%m-%d %H:%M"),
        format_time_left(image.time_left(retention_hours)),
    )


def row_for_generated(image: GeneratedImage, retention_days: int) -> tuple[str, ...]:
    """Proyecta un `GeneratedImage` a la fila de la tabla mixta."""
    detail_parts = [f"refs: {image.refs_count}"]
    if image.settings is not None:
        detail_parts.append(
            f"{image.settings.aspect_ratio} {image.settings.resolution} "
            f"{image.settings.output_format}"
        )
    size = format_size(image.file_size) if image.file_size is not None else "—"
    return (
        "generada",
        image.id,
        image.label,
        " · ".join(detail_parts),
        size,
        image.generated_at.strftime("%Y-%m-%d %H:%M"),
        format_time_left(image.time_left(retention_days)),
    )


def row_for_job(job: ImageJob) -> tuple[str, ...]:
    """Proyecta un `ImageJob` no-completado a la fila de la tabla mixta."""
    detail = job.error if job.error else f"task: {job.task_id or '—'}"
    return (
        f"job · {job.status.value}",
        job.id,
        job.label,
        detail,
        "—",
        job.created_at.strftime("%Y-%m-%d %H:%M"),
        "—",
    )


def format_size(size_bytes: int) -> str:
    """Formatea bytes como KB o MB con 1 decimal."""
    if size_bytes >= _BYTES_PER_MB:
        return f"{size_bytes / _BYTES_PER_MB:.1f} MB"
    return f"{size_bytes / 1024:.1f} KB"


def format_time_left(delta: timedelta) -> str:
    """Formatea un `timedelta` como `Xd Yh` / `Xh Ym` / `EXPIRADO`."""
    total_seconds = delta.total_seconds()
    if total_seconds <= 0:
        return "EXPIRADO"
    days = int(total_seconds // _SECONDS_PER_DAY)
    hours = int((total_seconds % _SECONDS_PER_DAY) // _SECONDS_PER_HOUR)
    if days > 0:
        return f"{days}d {hours}h"
    minutes = int((total_seconds % _SECONDS_PER_HOUR) // _SECONDS_PER_MINUTE)
    return f"{hours}h {minutes}m"
