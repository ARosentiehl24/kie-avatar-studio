"""Badges Rich para representar status de jobs en tablas Textual.

Centraliza los strings comunes entre `AudiosScreen`, `ImagesScreen`,
`VideosScreen` y `HistoryScreen` (CR-3.7). Cada pantalla específica
puede extender con sus propios status no-compartidos.

El dict se construye a partir de los enums concretos para que sea
imposible mantener un status sin badge ni un badge sin status.

También expone `KIND_BADGES` con el label/icono visual de cada tipo
de job (`video`, `audio`, `image`) — antes estaba duplicado entre
`HistoryScreen` y `QueueScreen` como `_KIND_ICONS`.

### Decisión de UI: solo color, sin emojis prefix

Los chars emoji-tipo-dingbat (`✅`, `❌`) no se renderizan en todos los
terminales (caen a `√`/`✗` text-style sin separación visual). El color
del badge (yellow/green/red/cyan) ya comunica el estado sin ambigüedad
y es 100% portable.
"""

from __future__ import annotations

from typing import Final

from ..domain.events import JobKind
from ..domain.models import AudioJobStatus, ImageJobStatus, JobStatus

# Compartidos entre video, audio e image (mismo `value` en los tres
# enums porque se diseñaron así: queued/validating/completed/failed/
# cancelled).
BASE_STATUS_BADGES: Final[dict[str, str]] = {
    JobStatus.QUEUED.value: "[yellow]En cola[/yellow]",
    JobStatus.VALIDATING.value: "[yellow]Validando[/yellow]",
    JobStatus.COMPLETED.value: "[green]Listo[/green]",
    JobStatus.FAILED.value: "[red]Falló[/red]",
    JobStatus.CANCELLED.value: "[dim]Cancelado[/dim]",
}

# Específicos de VideoJob (no aplican a AudioJob/ImageJob).
VIDEO_STATUS_BADGES: Final[dict[str, str]] = {
    JobStatus.UPLOADING_IMAGE.value: "[cyan]Subiendo[/cyan]",
    JobStatus.CREATING_AUDIO.value: "[cyan]Creando audio[/cyan]",
    JobStatus.WAITING_AUDIO.value: "[cyan]Esperando audio[/cyan]",
    JobStatus.CREATING_AVATAR.value: "[cyan]Creando avatar[/cyan]",
    JobStatus.WAITING_VIDEO.value: "[cyan]Esperando video[/cyan]",
    JobStatus.DOWNLOADING.value: "[cyan]Bajando[/cyan]",
}

# Específicos de AudioJob.
AUDIO_STATUS_BADGES: Final[dict[str, str]] = {
    AudioJobStatus.CREATING.value: "[cyan]Creando[/cyan]",
    AudioJobStatus.POLLING.value: "[cyan]Procesando[/cyan]",
}

# Específicos de ImageJob (Nano Banana 2). Mismos `value` strings
# que los de audio (creating/polling) pero los listamos aparte por
# trazabilidad: si Kie agrega un step nuevo a Nano Banana, lo
# agregamos acá sin afectar los badges de audio.
IMAGE_STATUS_BADGES: Final[dict[str, str]] = {
    ImageJobStatus.CREATING.value: "[cyan]Creando[/cyan]",
    ImageJobStatus.POLLING.value: "[cyan]Procesando[/cyan]",
}

# Labels de cada tipo de job para columnas "Tipo" en tablas mixtas.
# Compartidos entre `HistoryScreen` y `QueueScreen`. Si cambia el
# emoji o el label de un kind, se cambia acá y se propaga a las dos
# pantallas (CR-3.7).
KIND_BADGES: Final[dict[JobKind, str]] = {
    "video": "🎬 Video",
    "audio": "🔊 Audio",
    "image": "📷 Imagen",
}
