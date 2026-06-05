"""Modelos de dominio. Pydantic v2.

Sin lógica de negocio: solo estructura. La validación vive en `policies.py`,
los errores en `errors.py`, los eventos en `events.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Helper centralizado para timestamps. `datetime.utcnow` quedó deprecated en 3.12."""
    return datetime.now(UTC)


class JobStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    UPLOADING_IMAGE = "uploading_image"
    CREATING_AUDIO = "creating_audio"
    WAITING_AUDIO = "waiting_audio"
    CREATING_AVATAR = "creating_avatar"
    WAITING_VIDEO = "waiting_video"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

RESUMABLE_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.WAITING_AUDIO,
        JobStatus.WAITING_VIDEO,
        JobStatus.CREATING_AUDIO,
        JobStatus.CREATING_AVATAR,
        JobStatus.DOWNLOADING,
    }
)


class AudioJobStatus(StrEnum):
    """Estados del lifecycle de un `AudioJob` (generación TTS persistida).

    Más simple que `JobStatus` porque el flujo es lineal: validar →
    crear task en Kie → polling → persistir el `GeneratedAudio` final.
    No hay paso de "upload" (no se sube nada) ni paso de "download" (no
    descargamos el audio: solo guardamos la `kie_url`).
    """

    QUEUED = "queued"
    VALIDATING = "validating"
    CREATING = "creating"
    POLLING = "polling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


AUDIO_TERMINAL_STATUSES: frozenset[AudioJobStatus] = frozenset(
    {AudioJobStatus.COMPLETED, AudioJobStatus.FAILED, AudioJobStatus.CANCELLED}
)

AUDIO_RESUMABLE_STATUSES: frozenset[AudioJobStatus] = frozenset(
    # QUEUED y POLLING son retomables sin perder créditos: QUEUED nunca
    # llegó a llamar a Kie, POLLING ya tiene `task_id` y solo necesita
    # seguir consultando. CREATING queda excluido porque sin `task_id`
    # persistido no podemos saber si el POST realmente llegó a crear el
    # task → se marca FAILED al restaurar para que el usuario decida.
    {AudioJobStatus.QUEUED, AudioJobStatus.POLLING}
)


class VideoJob(BaseModel):
    id: str
    # `script`/`voice`/`image_path` quedan opcionales en el modo "reuse de
    # assets": cuando el job apunta a un `GeneratedAudio` ya hecho,
    # `audio_url` está poblado y `script`/`voice` son metadata
    # informativa. Idem con `image_url` y `image_path`. El runner y el
    # validator (`domain.policies.validate_job`) deciden qué requerir
    # según los URLs disponibles.
    script: str = ""
    image_path: str = ""
    prompt: str
    voice: str = ""
    status: JobStatus = JobStatus.QUEUED
    image_url: str | None = None
    audio_task_id: str | None = None
    audio_url: str | None = None
    video_task_id: str | None = None
    video_url: str | None = None
    output_path: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_resumable(self) -> bool:
        return self.status in RESUMABLE_STATUSES


class AudioJob(BaseModel):
    """Job de generación TTS persistido en cola estructurada.

    Espejo de `VideoJob` pero para audios. La cola lo procesa con
    `AudioJobRunner` (igual de async, mismo `QueueManager`). Cuando
    termina exitoso, persiste un `GeneratedAudio` cuyo `id` es el
    mismo que el del job (idempotencia: reintentos no duplican
    registros en la tabla `audios`).

    `kie_url` y `kie_file_path` quedan poblados cuando el polling
    termina con éxito; antes de eso son `None`. La pantalla los puede
    usar para mostrar "Listo para reproducir" sin tener que ir al
    store de audios.
    """

    id: str
    label: str
    script: str
    voice_id: str
    voice_settings_json: str | None = None
    status: AudioJobStatus = AudioJobStatus.QUEUED
    task_id: str | None = None
    kie_url: str | None = None
    kie_file_path: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def is_terminal(self) -> bool:
        return self.status in AUDIO_TERMINAL_STATUSES

    def is_resumable(self) -> bool:
        return self.status in AUDIO_RESUMABLE_STATUSES


class KieUploadResult(BaseModel):
    file_name: str
    file_path: str
    download_url: str
    file_size: int
    mime_type: str


class KieTaskCreated(BaseModel):
    task_id: str


class KieTaskResult(BaseModel):
    """Resultado normalizado de `/api/v1/jobs/recordInfo`.

    `status` siempre es uno de: pending | running | success | failed.
    `result_url` solo está presente en éxito.
    """

    task_id: str
    status: str
    result_url: str | None = None
    raw: dict[str, Any] | None = None


KeyValidationStatus = Literal["ok", "unauthorized", "error"]


class KieKey(BaseModel):
    """Credencial de Kie.ai con metadata de validación.

    El campo `key` contiene el secreto. Nunca debe loguearse ni mostrarse
    completo en la UI (CR-7.1). Las pantallas deben renderizar masked y
    ofrecer un toggle explícito para revelar.
    """

    id: str
    label: str
    key: str
    created_at: datetime = Field(default_factory=_utcnow)
    last_validated_at: datetime | None = None
    last_validated_status: KeyValidationStatus | None = None
    # Saldo en créditos detectado durante el último test (None = no se probó
    # aún). Best-effort: si el endpoint de créditos falla, queda en el valor
    # anterior. Se persiste en `data/keys.json` junto al resto del registro.
    last_known_credits: float | None = None

    def masked(self, visible_tail: int = 4) -> str:
        """Devuelve la key parcialmente oculta para mostrar en logs/UI."""
        if len(self.key) <= visible_tail:
            return "*" * len(self.key)
        return "*" * (len(self.key) - visible_tail) + self.key[-visible_tail:]


class UploadedImage(BaseModel):
    """Imagen ya subida a Kie. Mantenemos el path local para previsualizar.

    Si el archivo local desaparece, el registro sigue siendo útil porque
    `kie_url` ya está en los servidores de Kie y se puede usar para crear
    nuevos jobs (ver SPEC §5) — **pero solo dentro de las 24h posteriores
    al upload**: Kie expira los archivos subidos por File Upload API tras
    24h (a diferencia de los `GeneratedAudio` que duran 14 días).

    `expires_at` se calcula derivando `uploaded_at + KIE_UPLOAD_RETENTION_HOURS`
    (la política oficial de Kie); ver `domain/policies.py`.
    """

    id: str
    label: str
    local_path: str
    kie_url: str
    kie_file_path: str
    file_size: int
    mime_type: str
    uploaded_at: datetime = Field(default_factory=_utcnow)

    def local_file_exists(self) -> bool:
        from pathlib import Path  # local: evita import al tope solo por este método

        return Path(self.local_path).is_file()

    def expires_at(self, retention_hours: int) -> datetime:
        """Devuelve la fecha en que Kie borrará el archivo automáticamente."""
        return self.uploaded_at + timedelta(hours=retention_hours)

    def is_expired(self, retention_hours: int, *, now: datetime | None = None) -> bool:
        """Indica si el archivo ya debería estar borrado en Kie."""
        reference = now or _utcnow()
        return reference >= self.expires_at(retention_hours)

    def time_left(self, retention_hours: int, *, now: datetime | None = None) -> timedelta:
        """Tiempo restante antes de que Kie auto-borre el archivo.

        Puede ser negativo (ya expiró); el caller decide cómo formatearlo.
        """
        reference = now or _utcnow()
        return self.expires_at(retention_hours) - reference


class VoiceSettings(BaseModel):
    """Parámetros opcionales del input TTS aceptados por Kie/ElevenLabs.

    Todos los campos son opcionales. Si están en `None`, no se envían a Kie y
    el proveedor aplica el default documentado (que también vive en el
    OpenAPI spec de `elevenlabs/text-to-speech-multilingual-v2`).

    Rangos validados por `Field`; la verificación adicional vive en
    `policies.validate_voice_settings` para emitir mensajes en español
    consistentes con el resto del dominio.

    `language_code` solo es aceptado por los modelos turbo v2.5 y flash v2.5;
    los demás devuelven 422 si se lo mandan. El caller decide qué modelo usa.
    """

    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    similarity_boost: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)
    speed: float | None = Field(default=None, ge=0.7, le=1.2)
    language_code: str | None = Field(default=None, max_length=8)

    def is_empty(self) -> bool:
        """`True` si ningún campo fue seteado (equivalente a no mandar settings)."""
        return self.model_dump(exclude_none=True) == {}


class GeneratedAudio(BaseModel):
    """Audio TTS ya generado y servido por Kie. Solo guardamos la URL Kie.

    No descargamos el archivo localmente (decisión del usuario: solo URL Kie
    para mantener el storage liviano). `kie_url` se sirve desde
    `tempfile.redpandaai.co` durante el TTL.

    `voice_settings` queda nullable porque la generación puede haber usado los
    defaults del proveedor (no enviar ninguna setting). `voice_settings_json`
    es el campo que persiste `AudiosDB` — esta clase no se preocupa por la
    serialización SQL.

    `duration_seconds` y `file_size` quedan `None` si el polling de Kie no los
    devuelve en `recordInfo`. La pantalla los muestra como "—" en ese caso.

    `expires_at` deriva de `generated_at + KIE_GENERATED_RETENTION_DAYS`
    (política oficial: 14 días para generated media — ver `policies.py`).
    """

    id: str
    label: str
    script: str
    voice_id: str
    voice_settings: VoiceSettings | None = None
    kie_url: str
    kie_file_path: str
    file_size: int | None = None
    mime_type: str | None = None
    duration_seconds: float | None = None
    generated_at: datetime = Field(default_factory=_utcnow)

    def expires_at(self, retention_days: int) -> datetime:
        """Devuelve la fecha en que Kie borrará el audio automáticamente."""
        return self.generated_at + timedelta(days=retention_days)

    def is_expired(self, retention_days: int, *, now: datetime | None = None) -> bool:
        """Indica si el audio ya debería estar borrado en Kie."""
        reference = now or _utcnow()
        return reference >= self.expires_at(retention_days)

    def time_left(self, retention_days: int, *, now: datetime | None = None) -> timedelta:
        """Tiempo restante antes de que Kie auto-borre el audio.

        Puede ser negativo (ya expiró); el caller decide cómo formatearlo.
        """
        reference = now or _utcnow()
        return self.expires_at(retention_days) - reference


class VoicePreset(BaseModel):
    """Preset de voz reusable: combinación nombrada de voice_id + settings.

    Pensado para que el usuario guarde configuraciones que repite
    ('narrador-calmo', 'locutora-comercial') y las elija de un Select
    en lugar de reescribir voice_id + 5 sliders cada vez.

    `id` es el slug del nombre (sanitizado) — usado como filename en
     `presets_dir/voices/<id>.json` y como key del Select.

    `voice_settings` es nullable: un preset puede guardar SOLO el
    voice_id (sin overrides; equivale a usar los defaults de Kie).
    Esto es útil para guardar accesos rápidos a voices del catalogo.
    """

    id: str
    label: str
    voice_id: str
    voice_settings: VoiceSettings | None = None
    description: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class BatchEntry(BaseModel):
    """Una carpeta `batch_jobs/<name>/` analizada y lista (o no) para encolar.

    El `BatchLoader` la construye escaneando el filesystem; no toca red ni DB.
    Cuando `errors` está vacío, todos los campos requeridos están poblados
    y el `BatchController` puede pasarla a `VideosController.enqueue_from_scratch`
    sin más validación.

    `errors` guarda mensajes en español que la UI muestra tal cual al usuario
    para que sepa qué falta arreglar en la carpeta (ej. 'falta script.txt').
    """

    name: str
    path: Path
    script: str = ""
    image_path: Path | None = None
    prompt: str = ""
    voice: str = ""
    errors: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


class GitHubRelease(BaseModel):
    """Subset de la respuesta de la API de GitHub Releases que usamos.

    El cliente HTTP de infra y el `UpdateChecker` de app_layer comparten
    este DTO. Vivir en `domain/` mantiene la regla CR-1: app_layer no
    importa de infra; ambos importan de domain.

    `tag_name` viene como `v1.2.3`; el `UpdateChecker` normaliza
    quitando el `v` para comparar con `__version__` (que es `"1.2.3"`).
    """

    tag_name: str
    html_url: str
    body: str = ""
    published_at: str = ""
