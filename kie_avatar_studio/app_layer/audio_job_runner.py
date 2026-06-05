"""`AudioJobRunner`: ejecuta UN `AudioJob` siguiendo la state machine.

Mueve la lógica de `AudiosController.generate` a un runner durable que
puede ser orquestado por `QueueManager`. Cumple `RunnableRunner[AudioJob]`
del puerto `domain.ports`.

State machine (simplificada respecto a `JobRunner` porque no hay
upload de imagen ni download local):

    queued → validating → creating → polling → completed | failed

Cada transición se persiste en `AudioJobRepository` ANTES de avanzar
(write-ahead). Cuando llega a `COMPLETED`, además persiste el
`GeneratedAudio` final en `AudioStore`. Como `AudioJob.id` y
`GeneratedAudio.id` comparten valor, reintentar un job no duplica
filas en la tabla `generated_audios` (upsert sobre el mismo id).
"""

from __future__ import annotations

from typing import Final

from loguru import logger

from ..config import Settings
from ..domain.errors import (
    AudioValidationError,
    KieError,
)
from ..domain.models import (
    AudioJob,
    AudioJobStatus,
    GeneratedAudio,
    VoiceSettings,
)
from ..domain.policies import (
    validate_tts_script,
    validate_voice_id,
    validate_voice_settings,
)
from ..domain.ports import AudioJobRepository, AudioStore, KieGateway
from .polling import poll_task_for_url

DEFAULT_POLL_INTERVAL_SECONDS: Final[int] = 5
DEFAULT_TASK_TIMEOUT_SECONDS: Final[int] = 5 * 60


class AudioJobRunner:
    """Ejecuta un `AudioJob` end-to-end y persiste cada transición."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        repository: AudioJobRepository,
        audio_store: AudioStore,
        *,
        tts_model: str | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._repository = repository
        self._audio_store = audio_store
        self._tts_model = tts_model

    async def run(self, job: AudioJob) -> AudioJob:
        try:
            await self._validate(job)
            task_id = await self._create_task(job)
            audio_url = await self._poll_for_url(task_id)
            await self._finalize(job, audio_url)
        except (KieError, AudioValidationError) as exc:
            await self._fail(job, exc)
        except Exception as exc:
            logger.exception("AudioJob {} falló con error no manejado", job.id)
            await self._fail(job, exc)
        return job

    # --- pasos de la state machine ----------------------------------------

    async def _validate(self, job: AudioJob) -> None:
        await self._transition(job, AudioJobStatus.VALIDATING)
        validate_tts_script(job.script)
        validate_voice_id(job.voice_id, allow_custom=True)
        settings = self._parse_settings(job)
        if settings is not None:
            validate_voice_settings(settings)

    async def _create_task(self, job: AudioJob) -> str:
        # Si el job venía con un `task_id` (resume desde POLLING), reusamos
        # ese mismo task en Kie en lugar de crear uno nuevo. Evita gastar
        # créditos dos veces por la misma generación.
        if job.task_id:
            logger.info("AudioJob {} reanudado con task existente {}", job.id, job.task_id)
            await self._transition(job, AudioJobStatus.POLLING)
            return job.task_id

        await self._transition(job, AudioJobStatus.CREATING)
        settings = self._parse_settings(job)
        created = await self._client.create_tts_task(
            job.script,
            job.voice_id,
            model=self._tts_model,
            voice_settings=settings,
        )
        job.task_id = created.task_id
        await self._transition(job, AudioJobStatus.POLLING)
        return created.task_id

    async def _poll_for_url(self, task_id: str) -> str:
        return await poll_task_for_url(
            self._client,
            task_id,
            kind="audio",
            interval_seconds=self._settings.poll_interval_seconds,
            timeout_seconds=self._settings.task_timeout_seconds,
        )

    async def _finalize(self, job: AudioJob, audio_url: str) -> None:
        job.kie_url = audio_url
        job.kie_file_path = _derive_file_path(audio_url)
        await self._repository.upsert(job)
        # Persistimos el `GeneratedAudio` con id == job.id para idempotencia:
        # un reintento del mismo job hace upsert sobre la misma fila, no
        # duplica registros en la tabla de audios.
        await self._audio_store.upsert(self._build_generated_audio(job))
        await self._transition(job, AudioJobStatus.COMPLETED)
        logger.info("AudioJob {} ('{}') completado", job.id, job.label)

    # --- helpers ----------------------------------------------------------

    async def _transition(self, job: AudioJob, status: AudioJobStatus) -> None:
        """Mutación + persistencia atómica (write-ahead)."""
        job.status = status
        await self._repository.upsert(job)

    async def _fail(self, job: AudioJob, exc: BaseException) -> None:
        job.error = str(exc) or exc.__class__.__name__
        await self._transition(job, AudioJobStatus.FAILED)

    @staticmethod
    def _parse_settings(job: AudioJob) -> VoiceSettings | None:
        if not job.voice_settings_json:
            return None
        return VoiceSettings.model_validate_json(job.voice_settings_json)

    def _build_generated_audio(self, job: AudioJob) -> GeneratedAudio:
        if job.kie_url is None or job.kie_file_path is None:
            raise KieError(
                f"AudioJob {job.id} sin kie_url/kie_file_path al finalizar; "
                "indica un bug en la transición a COMPLETED."
            )
        return GeneratedAudio(
            id=job.id,
            label=job.label,
            script=job.script,
            voice_id=job.voice_id,
            voice_settings=self._parse_settings(job),
            kie_url=job.kie_url,
            kie_file_path=job.kie_file_path,
        )


def _derive_file_path(url: str) -> str:
    """Extrae el path relativo del archivo en Kie a partir de la URL pública.

    Espejo de la función homónima en `audios_controller.py`. Sirve para
    mostrar un identificador estable en la tabla (sin el host
    `tempfile.redpandaai.co/...`). Si la URL no tiene path, devuelve la
    URL completa como fallback defensivo.
    """
    _, separator, rest = url.partition("://")
    if not separator or "/" not in rest:
        return url
    _, _, path = rest.partition("/")
    return path or url
