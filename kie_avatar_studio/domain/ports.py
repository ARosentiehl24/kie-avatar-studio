"""Puertos (Protocols) que definen los contratos que `infra/` debe cumplir.

Permiten que `app_layer/` dependa solo de tipos abstractos del dominio,
respetando DIP (Dependency Inversion Principle). Los tests pueden reemplazar
estas dependencias por dobles sin importar httpx ni aiosqlite.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

from .models import (
    AudioJob,
    AudioJobStatus,
    GeneratedAudio,
    GeneratedImage,
    ImageJob,
    ImageJobStatus,
    JobStatus,
    KieKey,
    KieTaskCreated,
    KieUploadResult,
    UploadedImage,
    VideoJob,
    VoicePreset,
    VoiceSettings,
    WorkflowJob,
    WorkflowStatus,
    WorkflowStep,
)

StatusT = TypeVar("StatusT", bound=StrEnum)
ExternalJsonObject = dict[str, Any]  # Any: objeto JSON externo de APIs de proveedor.


@runtime_checkable
class RunnableJob(Protocol):
    """Mínimo contrato que un job debe cumplir para usar `QueueManager`.

    Pensado para ser implementado por modelos Pydantic concretos
    (`VideoJob`, `AudioJob`). El `QueueManager` no debe leer ni mutar
    `status` directamente — eso es responsabilidad del `JobLifecycle[T]`
    asociado. Lo único que el queue necesita saber del job es:

    - su `id` para tracking en el dict de activos;
    - si está en estado terminal (`is_terminal`) para no re-encolarlo;
    - si es reanudable (`is_resumable`) para `restore_pending`.
    """

    id: str

    def is_terminal(self) -> bool: ...

    def is_resumable(self) -> bool: ...


T = TypeVar("T", bound=RunnableJob)
T_contra = TypeVar("T_contra", bound=RunnableJob, contravariant=True)


@runtime_checkable
class RunnableRunner(Protocol[T]):
    """Ejecuta UN job end-to-end. Implementado por `JobRunner`, `AudioJobRunner`."""

    async def run(self, job: T) -> T: ...


@runtime_checkable
class JobLifecycle(Protocol[T_contra]):
    """Reglas específicas del lifecycle de cada tipo de job.

    Separamos esto del `QueueManager` para que el queue sea
    type-agnostic: VideoJob y AudioJob tienen distintos estados y
    transiciones, pero el queue solo necesita saber "¿este job se puede
    cancelar/reintentar ahora?". El runner (que sí conoce el modelo
    específico) provee la implementación.
    """

    def is_cancellable(self, job: T_contra) -> bool: ...

    def is_retryable(self, job: T_contra) -> bool: ...

    async def mark_cancelled(self, job: T_contra) -> None:
        """Persiste status=cancelled (write-ahead) antes de mutar memoria."""

    async def reset_for_retry(self, job: T_contra) -> None:
        """Persiste status=queued + error=None para reintentar."""


@runtime_checkable
class KieGateway(Protocol):
    """Operaciones HTTP contra Kie.ai. Implementado por `infra.kie_client.KieClient`."""

    async def upload_file(
        self,
        file_path: str | Path,
        upload_path: str = ...,
        file_name: str | None = ...,
    ) -> KieUploadResult: ...

    async def create_tts_task(
        self,
        text: str,
        voice: str,
        *,
        model: str | None = ...,
        voice_settings: VoiceSettings | None = ...,
    ) -> KieTaskCreated: ...

    async def create_avatar_task(
        self, image_url: str, audio_url: str, prompt: str
    ) -> KieTaskCreated: ...

    async def create_nano_banana_task(
        self,
        prompt: str,
        *,
        image_input: list[str] | None = ...,
        aspect_ratio: str = ...,
        resolution: str = ...,
        output_format: str = ...,
        model: str = ...,
    ) -> KieTaskCreated: ...

    async def create_kling_video_task(
        self,
        image_url: str,
        prompt: str,
        *,
        model: str = ...,
        duration: int = ...,
        sound: bool = ...,
        mode: str = ...,
        aspect_ratio: str = ...,
    ) -> KieTaskCreated: ...

    async def get_task_detail(self, task_id: str) -> ExternalJsonObject: ...

    async def create_veo_video_task(
        self,
        prompt: str,
        *,
        image_urls: list[str] | None = ...,
        model: str = ...,
        generation_type: str = ...,
        aspect_ratio: str = ...,
        resolution: str = ...,
        duration: int = ...,
        enable_translation: bool = ...,
        watermark: str | None = ...,
    ) -> KieTaskCreated: ...

    async def get_veo_task_detail(self, task_id: str) -> ExternalJsonObject: ...

    async def get_account_credits(self) -> float: ...

    async def download_file(self, url: str, output_path: str | Path) -> Path: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class FFmpegGateway(Protocol):
    """Operaciones locales de FFmpeg inyectadas desde el composition root."""

    async def concat_videos(self, video_paths: list[Path], output_path: Path) -> Path: ...

    async def extract_audio(self, video_path: Path, output_path: Path) -> Path: ...


@runtime_checkable
class ElevenLabsSpeechToSpeechClient(Protocol):
    """Operación directa de ElevenLabs usada por el postproceso STS."""

    async def speech_to_speech_to_file(
        self,
        voice_id: str,
        audio_path: Path,
        output_path: Path,
        *,
        model_id: str = ...,
        remove_background_noise: bool = ...,
        output_format: str = ...,
        voice_settings: VoiceSettings | None = ...,
    ) -> Path: ...


@runtime_checkable
class ElevenLabsVoicesClient(Protocol):
    """Catálogo de voces/modelos ElevenLabs usado por la UI."""

    async def list_voices(
        self,
        *,
        voice_type: str | None = None,
        search: str | None = None,
    ) -> list[ExternalJsonObject]: ...

    async def list_models(self) -> list[ExternalJsonObject]: ...


@runtime_checkable
class AudioPreviewPlayer(Protocol):
    """Reproductor de previews de voz inyectado desde la app."""

    async def play_voice_preview(self, url: str) -> None: ...

    async def stop(self) -> None: ...


@runtime_checkable
class JobRepository(Protocol):
    """Persistencia de `VideoJob`. Implementado por `infra.db.JobsDB`."""

    async def init(self) -> None: ...

    async def upsert(self, job: VideoJob) -> None: ...

    async def get(self, job_id: str) -> VideoJob | None: ...

    async def list_recent(self, limit: int = 50) -> list[VideoJob]: ...

    async def list_by_status(self, status: JobStatus) -> list[VideoJob]: ...

    async def delete(self, job_id: str) -> None: ...


@runtime_checkable
class AudioJobRepository(Protocol):
    """Persistencia de `AudioJob`. Implementado por `infra.audio_jobs_db.AudioJobsDB`.

    Espejo de `JobRepository` pero para audios. Mismo patrón de WAL/
    conexión por operación; ver `infra.audio_jobs_db` para detalles.
    """

    async def init(self) -> None: ...

    async def upsert(self, job: AudioJob) -> None: ...

    async def get(self, job_id: str) -> AudioJob | None: ...

    async def list_recent(self, limit: int = 50) -> list[AudioJob]: ...

    async def list_by_status(self, status: AudioJobStatus) -> list[AudioJob]: ...

    async def delete(self, job_id: str) -> None: ...


@runtime_checkable
class KeyStore(Protocol):
    """Persistencia de `KieKey`. Implementado por `KeysStore`.

    Una sola key puede estar marcada como "activa": es la que el composition
    root inyecta en `KieClient`. Si no hay activa, la app puede caer al
    `KIE_API_KEY` del `.env` (compatibilidad hacia atrás).
    """

    async def init(self) -> None: ...

    async def load(self) -> list[KieKey]: ...

    async def get(self, key_id: str) -> KieKey | None: ...

    async def upsert(self, key: KieKey) -> None: ...

    async def delete(self, key_id: str) -> None: ...

    async def get_active(self) -> KieKey | None: ...

    async def set_active(self, key_id: str | None) -> None: ...

    async def get_elevenlabs_api_key(self) -> str | None: ...

    async def set_elevenlabs_api_key(self, secret: str) -> None: ...


@runtime_checkable
class EnvWriter(Protocol):
    """Escritura segura sobre `.env`. Implementado por `infra.env_writer.DotenvWriter`.

    Toda actualización de settings persistidos en `.env` (endpoints, paralelismo,
    polling, defaults) pasa por este puerto, así `app_layer` no toca el filesystem
    ni depende de `python-dotenv` directamente.
    """

    def set(self, key: str, value: str) -> None: ...

    def get(self, key: str) -> str | None: ...

    def unset(self, key: str) -> None: ...


@runtime_checkable
class ImageStore(Protocol):
    """Persistencia de `UploadedImage`. Implementado por `infra.images_db.ImagesDB`."""

    async def init(self) -> None: ...

    async def list_recent(self, limit: int = 100) -> list[UploadedImage]: ...

    async def get(self, image_id: str) -> UploadedImage | None: ...

    async def upsert(self, image: UploadedImage) -> None: ...

    async def delete(self, image_id: str) -> None: ...


@runtime_checkable
class AudioStore(Protocol):
    """Persistencia de `GeneratedAudio`. Implementado por `infra.audios_db.AudiosDB`.

    Mismo patrón que `ImageStore`: una capa fina async de CRUD sobre la
    misma `data/jobs.db`. La política de retención y limpieza viven en
    `AudiosController`, no en el store.
    """

    async def init(self) -> None: ...

    async def list_recent(self, limit: int = 100) -> list[GeneratedAudio]: ...

    async def get(self, audio_id: str) -> GeneratedAudio | None: ...

    async def upsert(self, audio: GeneratedAudio) -> None: ...

    async def delete(self, audio_id: str) -> None: ...

    async def delete_many(self, audio_ids: list[str]) -> None: ...


@runtime_checkable
class ImageJobRepository(Protocol):
    """Persistencia de `ImageJob`. Implementado por `infra.image_jobs_db.ImageJobsDB`.

    Espejo de `AudioJobRepository`. Misma `data/jobs.db`, conexión por
    operación con WAL. La política de qué status restaurar al arrancar
    vive en `IMAGE_RESUMABLE_STATUSES` (`domain.models`); el composition
    root itera esos status y llama `list_by_status` para cada uno
    (mismo patrón que video y audio).
    """

    async def init(self) -> None: ...

    async def upsert(self, job: ImageJob) -> None: ...

    async def get(self, job_id: str) -> ImageJob | None: ...

    async def list_recent(self, limit: int = 50) -> list[ImageJob]: ...

    async def list_by_status(self, status: ImageJobStatus) -> list[ImageJob]: ...

    async def delete(self, job_id: str) -> None: ...


@runtime_checkable
class GeneratedImageStore(Protocol):
    """Persistencia de `GeneratedImage`. Implementado por `infra.generated_images_db.GeneratedImagesDB`.

    Mismo patrón que `AudioStore`. `delete_many` se usa al limpiar
    expirados al arrancar la app para no abrir N conexiones aiosqlite.
    """

    async def init(self) -> None: ...

    async def list_recent(self, limit: int = 100) -> list[GeneratedImage]: ...

    async def get(self, image_id: str) -> GeneratedImage | None: ...

    async def upsert(self, image: GeneratedImage) -> None: ...

    async def delete(self, image_id: str) -> None: ...

    async def delete_many(self, image_ids: list[str]) -> None: ...


@runtime_checkable
class VoicePresetStore(Protocol):
    """Persistencia de `VoicePreset` (file-based JSON).

    Implementado por `infra.presets_store.VoicePresetsStore`. La
    decisión de no usar SQLite acá es deliberada: los presets son
    pocos (~docenas máximo), file-based los hace fácilmente editables
    a mano, versionables con git si el usuario quiere, y portables
    entre instalaciones (basta copiar `presets/voices/`).
    """

    async def init(self) -> None: ...

    async def list_all(self) -> list[VoicePreset]: ...

    async def get(self, preset_id: str) -> VoicePreset | None: ...

    async def upsert(self, preset: VoicePreset) -> None: ...

    async def delete(self, preset_id: str) -> None: ...


@runtime_checkable
class DesktopNotifier(Protocol):
    """Puerto para notificaciones del sistema operativo.

    Implementaciones disponibles en `infra.notifier`:
    - `SystemNotifier`: usa el comando nativo del SO (Linux: notify-send,
      macOS: osascript, Windows: PowerShell toast). Best-effort: si el
      backend falla o no está disponible, loguea a DEBUG y sigue.
    - `NullNotifier`: no-op (usado en tests y cuando
      `settings.notifications_enabled` es False).

    El método es async porque los backends lanzan procesos hijos
    (subprocess) y bloquear el event loop con `subprocess.run` rompería
    el refresh de la TUI mientras se dispara el toast.
    """

    async def notify(self, *, title: str, message: str, success: bool) -> None: ...


@runtime_checkable
class WorkflowRepository(Protocol):
    """Persistencia de `WorkflowJob` + `WorkflowStep`.

    Implementado por `infra.workflow_db.WorkflowDB`. A diferencia de los
    repos de jobs hoja (video/audio/image), este expone updates
    granulares por step:
    - `upsert_workflow(workflow)`: persiste header + lista completa de
      steps (usado en enqueue y restore para inicialización).
    - `upsert_step(workflow_id, step)`: persiste cambios de UN solo
      step (usado en cada transición del step runner para evitar lost
      updates con steps corriendo en paralelo).
    - `update_workflow_header(workflow)`: persiste solo el status/error/
      manifest_write_failed/updated_at del workflow (no toca steps).

    `progress` se persiste como `progress_json TEXT` siguiendo el patrón
    de `image_jobs.refs_json` / `audio_jobs.voice_settings_json`. El
    runner serializa/deserializa con `WorkflowStep.model_dump_json`.
    """

    async def init(self) -> None: ...

    async def upsert_workflow(self, workflow: WorkflowJob) -> None: ...

    async def update_workflow_header(self, workflow: WorkflowJob) -> None: ...

    async def upsert_step(self, workflow_id: str, step: WorkflowStep) -> None: ...

    async def get(self, workflow_id: str) -> WorkflowJob | None: ...

    async def list_recent(self, limit: int = 50) -> list[WorkflowJob]: ...

    async def list_by_status(self, status: WorkflowStatus) -> list[WorkflowJob]: ...

    async def delete(self, workflow_id: str) -> None: ...


@runtime_checkable
class WorkflowManifestWriter(Protocol):
    """Puerto para escribir el manifest `workflow.json` atómicamente.

    Implementado por `infra.workflow_manifest_writer.AtomicWorkflowManifestWriter`.
    Desacopla la capa de aplicación de la concreta (CR-1: `app_layer/`
    no importa de `infra/`). El `WorkflowRunner` recibe esto por
    inyección desde el composition root.

    `write` debe ser idempotente y no debe lanzar en caso de fallo
    transitorio (best-effort: si falla permanentemente, lo registra en
    el campo `manifest_write_failed` del workflow y el runner sigue
    ejecutando). Esto es porque el manifest es derivado: la DB es la
    fuente de verdad.
    """

    async def write(self, workflow: WorkflowJob) -> bool:
        """Escribe el manifest atómicamente. Devuelve True si OK, False si falló."""
