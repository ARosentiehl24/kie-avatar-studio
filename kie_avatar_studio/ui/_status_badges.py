"""Badges Rich para representar status de jobs en tablas Textual.

Centraliza los strings comunes entre `AudiosScreen` y `HistoryScreen`
(CR-3.7). Cada pantalla específica puede extender con sus propios
status no-compartidos (por ejemplo, los pasos intermedios de
`VideoJob` que no aplican a audios).

El dict se construye a partir de los enums concretos para que sea
imposible mantener un status sin badge ni un badge sin status.
"""

from __future__ import annotations

from typing import Final

from ..domain.models import AudioJobStatus, JobStatus

# Compartidos entre video y audio (mismo `value` en ambos enums porque
# se diseñaron así: queued/validating/completed/failed/cancelled).
BASE_STATUS_BADGES: Final[dict[str, str]] = {
    JobStatus.QUEUED.value: "[yellow]⏳ En cola[/yellow]",
    JobStatus.VALIDATING.value: "[yellow]⚙ Validando[/yellow]",
    JobStatus.COMPLETED.value: "[green]✓ Listo[/green]",
    JobStatus.FAILED.value: "[red]✖ Falló[/red]",
    JobStatus.CANCELLED.value: "[dim]✖ Cancelado[/dim]",
}

# Específicos de VideoJob (no aplican a AudioJob).
VIDEO_STATUS_BADGES: Final[dict[str, str]] = {
    JobStatus.UPLOADING_IMAGE.value: "[cyan]📤 Subiendo[/cyan]",
    JobStatus.CREATING_AUDIO.value: "[cyan]🎙 Creando audio[/cyan]",
    JobStatus.WAITING_AUDIO.value: "[cyan]🔄 Esperando audio[/cyan]",
    JobStatus.CREATING_AVATAR.value: "[cyan]🎭 Creando avatar[/cyan]",
    JobStatus.WAITING_VIDEO.value: "[cyan]🔄 Esperando video[/cyan]",
    JobStatus.DOWNLOADING.value: "[cyan]⬇ Bajando[/cyan]",
}

# Específicos de AudioJob.
AUDIO_STATUS_BADGES: Final[dict[str, str]] = {
    AudioJobStatus.CREATING.value: "[cyan]📤 Creando[/cyan]",
    AudioJobStatus.POLLING.value: "[cyan]🔄 Procesando[/cyan]",
}
