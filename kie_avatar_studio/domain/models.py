"""Modelos de dominio. Pydantic v2.

Sin lógica de negocio: solo estructura. La validación vive en `policies.py`,
los errores en `errors.py`, los eventos en `events.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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


class ImageJobStatus(StrEnum):
    """Estados del lifecycle de un `ImageJob` (generación de imagen Nano Banana 2).

    Mirror de `AudioJobStatus`: el flujo es lineal validar → crear task → polling.
    No hay step de upload local (las refs ya están como URLs en Kie) ni de
    download eager (la imagen generada queda como `kie_url`, se descarga
    lazy al "Ver" igual que `GeneratedAudio`).
    """

    QUEUED = "queued"
    VALIDATING = "validating"
    CREATING = "creating"
    POLLING = "polling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


IMAGE_TERMINAL_STATUSES: frozenset[ImageJobStatus] = frozenset(
    {ImageJobStatus.COMPLETED, ImageJobStatus.FAILED, ImageJobStatus.CANCELLED}
)

IMAGE_RESUMABLE_STATUSES: frozenset[ImageJobStatus] = frozenset(
    # Mismo razonamiento que en audio: QUEUED y POLLING son retomables sin
    # perder créditos. CREATING queda excluido y se marca FAILED al restart
    # (ver `app._mark_creating_image_jobs_as_failed`) para evitar pagar
    # dos veces si el POST a Kie llegó pero la respuesta nunca volvió.
    {ImageJobStatus.QUEUED, ImageJobStatus.POLLING}
)


class ImageAssetKind(StrEnum):
    """Origen de una imagen reutilizable como input de otro job.

    Discriminador del DTO `ImageAssetRef`. Permite resolver la imagen
    contra el store correcto (uploaded vs generated) y aplicar la
    política de retención correspondiente (24h vs 14d) sin ambigüedad
    entre ids que podrían colisionar entre stores.
    """

    UPLOADED = "uploaded"
    GENERATED = "generated"


class ImageAssetRef(BaseModel):
    """Referencia discriminada a una imagen ya hosteada en Kie.

    Usada como input por:
    - `GenerateImageFormScreen` para el selector de refs (`image_input`).
    - `NewVideoFormScreen` para elegir la imagen del video entre
      uploaded + generated en un único selector.
    - `VideosController.enqueue_from_assets()` para resolver la imagen
      sin asumir que viene de `UploadedImage`.

    `expires_at` se calcula en el catálogo al momento de listar; la
    pantalla lo usa para mostrar tiempo restante y para excluir refs
    ya vencidas. El runner valida nuevamente justo antes de llamar a
    Kie (las refs pueden vencer entre encolar y ejecutar).
    """

    kind: ImageAssetKind
    id: str
    label: str
    kie_url: str
    expires_at: datetime


class ImageGenerationSettings(BaseModel):
    """Parámetros opcionales del input de Nano Banana 2 expuestos al usuario.

    Los defaults coinciden con los del OpenAPI spec del endpoint
    (`docs.kie.ai/market/google/nanobanana2`). Las validaciones de enum
    viven en `policies.validate_image_settings` para que el dominio
    emita errores tipados en español.
    """

    aspect_ratio: str = "auto"
    resolution: str = "1K"
    output_format: str = "jpg"
    model: str | None = None


class ImageJob(BaseModel):
    """Job de generación de imagen persistido en cola estructurada.

    Espejo de `AudioJob` para imágenes. Procesado por `ImageJobRunner`
    bajo el mismo `QueueManager` y `Semaphore(max_parallel_jobs)`. Al
    completarse exitosamente persiste un `GeneratedImage` con el mismo
    `id` (idempotencia: reintentos no duplican filas en
    `generated_images`).

    `refs_json` guarda la lista serializada de `ImageAssetRef`
    (no las URLs sueltas) para que el runner pueda revalidar cada
    referencia antes de llamar a Kie (chequear expiración por kind).
    `settings_json` aplica la misma estrategia con
    `ImageGenerationSettings` — opcional porque un job puede usar
    todos los defaults del modelo.

    `kie_url` y `kie_file_path` quedan poblados cuando el polling
    termina con éxito; antes son `None`.
    """

    id: str
    label: str
    prompt: str
    settings_json: str | None = None
    refs_json: str | None = None
    status: ImageJobStatus = ImageJobStatus.QUEUED
    task_id: str | None = None
    kie_url: str | None = None
    kie_file_path: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def is_terminal(self) -> bool:
        return self.status in IMAGE_TERMINAL_STATUSES

    def is_resumable(self) -> bool:
        return self.status in IMAGE_RESUMABLE_STATUSES


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


class GeneratedImage(BaseModel):
    """Imagen ya generada por Nano Banana 2 y servida por Kie.

    Mirror exacto de `GeneratedAudio`: solo guardamos `kie_url` y la
    descarga local es lazy al hacer "Ver". La política de retención
    es la misma (14 días, `KIE_GENERATED_RETENTION_DAYS`) porque para
    Kie todo el output de los modelos cae en la misma categoría
    "generated media".

    `settings` y `refs_count` son metadata informativa que aparece en
    la tabla; el catálogo (`ImageCatalogController`) NO los usa para
    resolver la imagen — solo necesita `kie_url` y `expires_at`.

    `file_size` y `mime_type` quedan opcionales: `recordInfo` para
    Nano Banana no siempre los devuelve, y como no descargamos el
    archivo eager no podemos derivarlos. La pantalla muestra "—" cuando
    están en `None`.
    """

    id: str
    label: str
    prompt: str
    settings: ImageGenerationSettings | None = None
    refs_count: int = 0
    kie_url: str
    kie_file_path: str
    file_size: int | None = None
    mime_type: str | None = None
    generated_at: datetime = Field(default_factory=_utcnow)

    def expires_at(self, retention_days: int) -> datetime:
        """Devuelve la fecha en que Kie borrará la imagen automáticamente."""
        return self.generated_at + timedelta(days=retention_days)

    def is_expired(self, retention_days: int, *, now: datetime | None = None) -> bool:
        """Indica si la imagen ya debería estar borrada en Kie."""
        reference = now or _utcnow()
        return reference >= self.expires_at(retention_days)

    def time_left(self, retention_days: int, *, now: datetime | None = None) -> timedelta:
        """Tiempo restante antes de que Kie auto-borre la imagen.

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


# --- Workflow automation models -------------------------------------------


class ModelCreationMethod(StrEnum):
    """Cómo se obtiene la imagen base de la modelo del workflow."""

    PROMPT = "prompt"
    LOCAL = "local"
    CATALOG = "catalog"


class ModelCreation(BaseModel):
    """Configuración para resolver la imagen base de la modelo del workflow.

    Solo uno de los campos es relevante según `method`:
    - `PROMPT`: usa `prompt` para generar con Nano Banana 2.
    - `LOCAL`: sube `local_path` con `KieGateway.upload_file`.
    - `CATALOG`: resuelve `asset_kind` + `asset_id` contra los stores.

    Los demás quedan `None`. La validación cruzada vive en
    `policies.validate_model_creation` (no usa Pydantic discriminated
    unions porque necesitamos mensajes en español).

    `resolved_image_ref` lo rellena el `WorkflowRunner` en runtime, o el
    `WorkflowController.enqueue_entry` cuando la UI lo pre-resolvió antes
    de encolar (caso preview de prompt aprobado / foto subida con method=local).
    Se serializa al manifest para que el usuario vea la URL Kie +
    expiración + el path local descargado.
    """

    method: ModelCreationMethod
    prompt: str | None = None
    local_path: str | None = None
    asset_kind: ImageAssetKind | None = None
    asset_id: str | None = None
    resolved_image_ref: ImageAssetRef | None = None


class SceneApprovalMode(StrEnum):
    """Modo de aprobación de scene_image generada por Nano Banana para b-rolls.

    "Genera scene nueva" = `change_scene=true` **o** `include_product=true`
    (ver `needs_scene_generation`): ambos componen una scene_image nueva con
    Nano Banana que vale la pena revisar.

    - `AUTO` (default): cuando un b-roll genera scene nueva, el workflow
      continúa inmediatamente al render Kling 3.0 sin esperar revisión
      humana. Comportamiento histórico.

    - `MANUAL`: cuando un b-roll genera scene nueva, el workflow se pausa
      en `AWAITING_APPROVAL` y el step queda con
      `WorkflowStepStatus.AWAITING_APPROVAL`. El usuario revisa la imagen
      en la UI y aprueba / regenera / cancela. Sin acción, el workflow
      espera indefinidamente.

    Pensado para evitar gastar créditos en Kling 3.0 animando una
    scene_image que salió mal. NO aplica a b-rolls que reusan la base tal
    cual (`change_scene=false` y `include_product=false`: no hay imagen
    nueva que aprobar) ni a a-rolls (nunca pausan, aunque generen scene
    con producto).
    """

    AUTO = "auto"
    MANUAL = "manual"


class ProductImage(BaseModel):
    """Imagen de un producto a promocionar dentro de un workflow.

    Un workflow promociona como mucho UN producto (global, elegido en
    settings antes de encolar). Los steps con `include_product=True`
    componen este producto sobre la imagen base usando Nano Banana 2
    (que acepta varias imágenes de referencia, hasta `MAX_IMAGE_REFS`).

    `local_path` es la foto elegida desde `inputs/` (para mostrar en el
    summary). `resolved_image_ref` es la ref Kie tras subirla (TTL 24h,
    como `method=local` de la imagen base). Ambos se serializan dentro
    de `pre_settings_json` — no hay columnas dedicadas en la DB.
    """

    local_path: str | None = None
    resolved_image_ref: ImageAssetRef | None = None


class WorkflowPreSettings(BaseModel):
    """Pre-configuración del workflow: idioma, voz preset, modelo base.

    El JSON del usuario trae `voice_preset` (snake_case sin sufijo `_id`).
    Mantenemos `voice_preset_id` como atributo Python para coherencia
    interna y exponemos el alias `voice_preset` para parsear/serializar.

    `audio_language` es un `language_code` ISO 639-1 (ej. `"es-419"`).
    Si está seteado, el `WorkflowStepRunner` fuerza el modelo TTS turbo
    (acepta `language_code`); si es `None`, usa el multilingual default
    (que NO acepta `language_code` → Kie devuelve 422 si se manda).
    """

    model_config = ConfigDict(populate_by_name=True)

    audio_language: str | None = None
    voice_preset_id: str | None = Field(default=None, alias="voice_preset")
    model_creation: ModelCreation
    # Override global del workflow para la duración de los b-roll. Si es
    # `None`, cada step usa su propio `duration_seconds` (si lo tiene) o
    # cae al default global de `Settings.default_i2v_duration_seconds`.
    # Si el usuario lo setea desde el modal de Configurar, FORZA esa
    # duración para TODOS los b-roll del workflow (sobreescribe el del
    # step si el step trae uno propio del JSON). Aceptable porque el
    # modal es la última palabra antes de encolar.
    i2v_duration_seconds: int | None = None
    # Modo de aprobación de scene_image. Ver docstring de
    # `SceneApprovalMode`. Default `AUTO` para no romper workflows
    # existentes (comportamiento histórico).
    scene_approval_mode: SceneApprovalMode = SceneApprovalMode.AUTO
    # Promoción de producto: si `True`, el workflow promociona UN producto
    # global (elegido en la UI antes de encolar, subido a Kie). Los steps
    # con `include_product=True` lo componen sobre la base con Nano Banana.
    # El producto resuelto vive en `product_image`. Default `False`.
    promote_product: bool = False
    product_image: ProductImage | None = None
    # Aspect ratio global para todas las imágenes generadas/compuestas con
    # Nano Banana 2 (base del modelo y scene_images de cada step). Si es
    # `None`, se usa el aspect ratio por defecto del modelo (auto).
    # Valores soportados: auto, 1:1, 9:16, 16:9, etc. (ver ASPECT_RATIOS).
    image_aspect_ratio: str | None = None


class StepType(StrEnum):
    """Tipo de escena del workflow.

    `A_ROLL`: la modelo habla a cámara (lip-sync). Avatar Pro genera un
    `final.mp4` con audio embebido, y la app descarga también `audio.mp3`
    para edición/post-producción.

    `B_ROLL`: video auxiliar (objeto, ilustración, plano). Kling 3.0
    genera un video silencioso. Si `text` no es vacío, además se
    genera un audio TTS aparte para post-producción.
    """

    A_ROLL = "a-roll"
    B_ROLL = "b-roll"


class WorkflowStepStatus(StrEnum):
    """Estado del lifecycle de un `WorkflowStep`."""

    QUEUED = "queued"
    PREPARING = "preparing"
    AWAITING_APPROVAL = "awaiting_approval"
    RENDERING = "rendering"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


WORKFLOW_STEP_TERMINAL_STATUSES: frozenset[WorkflowStepStatus] = frozenset(
    {
        WorkflowStepStatus.COMPLETED,
        WorkflowStepStatus.FAILED,
        WorkflowStepStatus.CANCELLED,
    }
)


class WorkflowProgressKey(StrEnum):
    """Sub-componentes del progreso granular de un step.

    Las keys esperadas dependen del tipo del step:
    - `A_ROLL`: SCENE_IMAGE, AUDIO, VIDEO, DOWNLOAD
    - `B_ROLL` con `text`: SCENE_IMAGE, AUDIO, VIDEO, DOWNLOAD_VIDEO, DOWNLOAD_AUDIO
    - `B_ROLL` sin `text`: SCENE_IMAGE, VIDEO, DOWNLOAD

    La validación cruzada (key vs tipo) vive en
    `policies.validate_workflow_step_progress`.
    """

    SCENE_IMAGE = "scene_image"
    AUDIO = "audio"
    VIDEO = "video"
    DOWNLOAD = "download"
    DOWNLOAD_VIDEO = "download_video"
    DOWNLOAD_AUDIO = "download_audio"


class WorkflowProgressStatus(StrEnum):
    """Estado de cada sub-componente del progreso de un step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStep(BaseModel):
    """Una escena del workflow (a-roll o b-roll).

    Persistido en `workflow_steps`. `bg_image_job_id`, `audio_job_id` y
    `video_task_id` se llenan en runtime para soportar restore tras
    crash (reusar el task de Kie en vez de re-crearlo).

    `progress` es un dict por sub-componente: keys del enum
    `WorkflowProgressKey`, valores del enum `WorkflowProgressStatus`.
    Persistido como JSON string en la columna `progress_json`.
    """

    # `populate_by_name=True` permite construir el modelo tanto con los
    # nombres canónicos (`change_scene`, `scene_description`) como con los
    # aliases legacy (`change_background`, `background_description`) que
    # vienen del JSON viejo (ver AliasChoices en los Fields). Esto deja a
    # `model_validate({...})` aceptar ambos shapes sin romper.
    model_config = ConfigDict(populate_by_name=True)

    step: int
    scene_name: str
    scene_slug: str
    type: StepType
    # `change_scene` (antes `change_background`): si True dispara una
    # regeneración con Nano Banana 2 (refs=[base] + `scene_description` +
    # `prompt`) y la imagen resultante se usa como `image_urls[0]` del
    # Kling 3.0 video. Si False, el b-roll reusa la imagen base de la modelo
    # directamente (sin gastar Nano Banana).
    #
    # AliasChoices acepta ambos nombres en JSON (new + legacy). Necesita
    # `model_config = ConfigDict(populate_by_name=True)` (definido abajo).
    change_scene: bool = Field(
        default=True,
        validation_alias=AliasChoices("change_scene", "change_background"),
        serialization_alias="change_scene",
    )
    # `scene_description` (antes `background_description`): texto que
    # describe la nueva escena cuando `change_scene=True`. Solo se usa
    # en ese caso. AliasChoices retrocompatible.
    scene_description: str = Field(
        default="",
        validation_alias=AliasChoices("scene_description", "background_description"),
        serialization_alias="scene_description",
    )
    prompt: str
    text: str = ""
    # Duración del video b-roll en segundos. Solo aplica a steps de tipo
    # `b-roll` (Kling 3.0 acepta `duration: 5|10`). Si es `None`, el step
    # usa el fallback: `WorkflowPreSettings.i2v_duration_seconds` → si
    # también es None, `Settings.default_i2v_duration_seconds` (5 por
    # default). Steps `a-roll` ignoran este campo (la duración del
    # avatar la determina la longitud del audio TTS).
    duration_seconds: int | None = None
    # `voiceover` solo aplica a steps `b-roll`. Controla quién genera el audio:
    # - `True` (default): comportamiento clásico. Si `text` no vacío → 1 TTS
    #   ElevenLabs genera `audio.mp3` aparte; el `video.mp4` queda silencioso
    #   (Kling con `sound=false`). El usuario monta en post.
    # - `False`: NO se llama a TTS. Kling 3.0 genera sound effects ambientales
    #   nativos basados en el prompt (`sound=true`), embebidos en el video.
    #   El `text` se ignora si está seteado (warning del validator).
    # A-roll ignora este flag (siempre tiene lip-sync; audio embebido por
    # Avatar Pro).
    voiceover: bool = True
    # `include_product` (a-roll o b-roll): si `True`, este step compone el
    # producto global del workflow (`pre_settings.product_image`) sobre la
    # imagen base usando Nano Banana 2 (refs = [base, producto]). Requiere
    # `pre_settings.promote_product=True`. La generación de scene se dispara
    # si `change_scene` O `include_product` (ver `needs_scene_generation`).
    # Si `include_product=True` y `change_scene=False`, Nano Banana mantiene
    # el mismo fondo de la base y solo añade el producto.
    include_product: bool = False
    # `include_model`: si `True` (default), pasa la imagen de la modelo base
    # como referencia a Nano Banana 2. Si `False`, no la pasa (útil para
    # b-rolls que son ilustraciones o planos de objeto donde no debe aparecer
    # la modelo, evitando que se mezcle su cara/cuerpo en la imagen).
    include_model: bool = True
    # `product_prompt`: texto que se añade al prompt de la escena para
    # indicarle a Nano Banana cómo/dónde colocar el producto. Solo se usa
    # si `include_product=True`. Vacío = Nano Banana lo compone solo con el
    # prompt de la escena (warning del validator).
    product_prompt: str = ""
    bg_image_job_id: str | None = None
    audio_job_id: str | None = None
    video_task_id: str | None = None
    scene_image_path: str | None = None
    audio_path: str | None = None
    video_path: str | None = None
    # Timestamp de aprobación humana de la scene_image (solo aplica cuando
    # `pre_settings.scene_approval_mode == MANUAL` y el step genera scene
    # nueva: `change_scene=true` o `include_product=true`, ver
    # `needs_scene_generation`). `None` = pendiente o no requerida. Cuando
    # el usuario aprueba desde la UI, el controller setea
    # `datetime.now(UTC)` y re-encola el workflow. El step runner reusa la
    # scene_image existente (bg_image_job_id) sin regenerar con Nano Banana.
    scene_image_approved_at: datetime | None = None
    # Aspect ratio del step para la imagen generada/compuesta con Nano Banana.
    # Si está configurado, sobrescribe el `image_aspect_ratio` global de
    # `pre_settings`. Si ambos son `None`, usa el del modelo (auto).
    image_aspect_ratio: str | None = None
    status: WorkflowStepStatus = WorkflowStepStatus.QUEUED
    progress: dict[WorkflowProgressKey, WorkflowProgressStatus] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def is_terminal(self) -> bool:
        return self.status in WORKFLOW_STEP_TERMINAL_STATUSES

    def is_awaiting_approval(self) -> bool:
        return self.status == WorkflowStepStatus.AWAITING_APPROVAL


class WorkflowStatus(StrEnum):
    """Estado global del lifecycle de un `WorkflowJob`.

    `AWAITING_APPROVAL` es un estado de pausa explícito (no es failed):
    el workflow ya generó la scene_image de algún b-roll que genera scene
    nueva (`change_scene=true` o `include_product=true`) Y
    `pre_settings.scene_approval_mode == "manual"`, y ahora espera que el
    usuario apruebe / regenere / cancele desde la UI antes de continuar al
    render i2v. El step en cuestión queda con
    `WorkflowStepStatus.AWAITING_APPROVAL` y el slot del semáforo de
    workflows queda libre (el runner termina su tarea sin avanzar).
    """

    QUEUED = "queued"
    PREPARING_BASE = "preparing_base"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


WORKFLOW_TERMINAL_STATUSES: frozenset[WorkflowStatus] = frozenset(
    {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.PARTIALLY_FAILED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    }
)

# AWAITING_APPROVAL NO está acá: requiere acción humana, no se auto-restart.
# Cuando el usuario aprueba/regenera/cancela, el controller lo vuelve a
# poner en QUEUED y el queue lo retoma normalmente.
WORKFLOW_RESUMABLE_STATUSES: frozenset[WorkflowStatus] = frozenset(
    {
        WorkflowStatus.QUEUED,
        WorkflowStatus.PREPARING_BASE,
        WorkflowStatus.RUNNING,
    }
)


class WorkflowJob(BaseModel):
    """Job de automatización end-to-end.

    Persistido en `workflow_jobs` + `workflow_steps`. El manifest
    (`output_dir/workflow.json`) es un snapshot derivado regenerado
    atómicamente tras cada transición (ver `infra.workflow_manifest_writer`).

    `source_json_path` es el path original (relativo al cwd) del JSON
    que disparó el workflow. Útil para debugging y para que el usuario
    sepa qué archivo abrir.

    `manifest_write_failed` se setea a `True` si el último intento de
    escribir el manifest atómicamente falló de forma permanente. NO
    bloquea la ejecución: el manifest es derivado, la DB es la fuente
    de verdad. La UI puede mostrar un badge informativo.
    """

    id: str
    name: str
    slug: str
    source_json_path: str
    output_dir: str
    pre_settings: WorkflowPreSettings
    steps: list[WorkflowStep]
    status: WorkflowStatus = WorkflowStatus.QUEUED
    error: str | None = None
    manifest_write_failed: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def is_terminal(self) -> bool:
        return self.status in WORKFLOW_TERMINAL_STATUSES

    def is_resumable(self) -> bool:
        return self.status in WORKFLOW_RESUMABLE_STATUSES

    def is_awaiting_approval(self) -> bool:
        return self.status == WorkflowStatus.AWAITING_APPROVAL

    def pending_approval_step(self) -> WorkflowStep | None:
        """Devuelve el primer step en estado AWAITING_APPROVAL, si hay alguno."""
        for step in self.steps:
            if step.is_awaiting_approval():
                return step
        return None

    def step_by_number(self, step_number: int) -> WorkflowStep | None:
        """Devuelve el `WorkflowStep` con `step == step_number` o `None`."""
        for step in self.steps:
            if step.step == step_number:
                return step
        return None


class WorkflowEntry(BaseModel):
    """Una entrada del directorio `workflows/` listada por el loader.

    Mirror de `BatchEntry`: representa un JSON detectado en el filesystem,
    parseado y validado. Si `errors` está vacío, `workflow` está
    correctamente parseado y se puede pasar a `WorkflowController.enqueue`.

    `warnings` recoge mensajes informativos (no bloqueantes) — ej. b-roll
    con `change_scene=false` que normalmente es un error de usuario
    pero técnicamente válido.
    """

    name: str
    path: Path
    workflow_payload: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors
