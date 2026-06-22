"""`JobRunner`: ejecuta UN `VideoJob` siguiendo la state machine del SPEC §9.

Aplica SRP estrictamente:
- Validación → `domain.policies.validate_job`.
- HTTP → `domain.ports.KieGateway` (inyectado).
- Persistencia → `domain.ports.JobRepository` (inyectado).
- Heurísticas de polling → `domain.policies.normalize_task_status` / `extract_result_url`.

El runner solo orquesta y propaga errores tipados.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

from loguru import logger

from ..config import Settings
from ..domain.errors import (
    JobValidationError,
    KieError,
)
from ..domain.models import JobStatus, VideoJob
from ..domain.policies import validate_job
from ..domain.ports import JobRepository, KieGateway
from .ids import sanitize_filename
from .polling import poll_task_for_url

_FINAL_FILE_NAME: Final[str] = "final.mp4"


class JobRunner:
    """Ejecuta un job end-to-end y persiste cada transición de estado."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        repository: JobRepository,
    ) -> None:
        self._settings = settings
        self._client = client
        self._repository = repository

    async def run(self, job: VideoJob) -> VideoJob:
        try:
            await self._validate(job)
            image_url, audio_url = await self._produce_inputs(job)
            video_url = await self._produce_video(job, image_url, audio_url)
            await self._download_final(job, video_url)
            await self._transition(job, JobStatus.COMPLETED)
        except (KieError, JobValidationError) as exc:
            await self._fail(job, exc)
        except Exception as exc:
            logger.exception("Job {} falló con error no manejado", job.id)
            await self._fail(job, exc)
        return job

    # --- pasos de la state machine ----------------------------------------

    async def _validate(self, job: VideoJob) -> None:
        await self._transition(job, JobStatus.VALIDATING)
        validate_job(job)

    async def _produce_inputs(self, job: VideoJob) -> tuple[str, str]:
        """Sube la imagen y crea el TTS en paralelo (SPEC §4 - intra-job parallelism).

        Etapa "Modo B" (video desde assets reusables): si el job ya viene
        con `image_url` y/o `audio_url` poblados (apuntan a recursos ya
        existentes en Kie), saltamos el paso correspondiente para no
        regastar créditos ni tiempo. El upload y TTS desde cero siguen
        funcionando si los campos están en `None` (modo "from scratch").
        """
        return await asyncio.gather(
            self._upload_image_if_needed(job),
            self._create_audio_if_needed(job),
        )

    async def _upload_image_if_needed(self, job: VideoJob) -> str:
        if job.image_url:
            # Imagen ya está en Kie (subida desde la pantalla Imágenes).
            # No re-uploadeamos: gastaríamos slot y duplicaríamos archivos.
            return job.image_url
        return await self._upload_image(job)

    async def _create_audio_if_needed(self, job: VideoJob) -> str:
        if job.audio_url:
            # Audio TTS ya generado (desde la pantalla Audios). Reusamos
            # la `kie_url` directamente: no recreamos el task ni
            # consumimos créditos de TTS otra vez.
            return job.audio_url
        return await self._create_audio(job)

    async def _upload_image(self, job: VideoJob) -> str:
        await self._transition(job, JobStatus.UPLOADING_IMAGE)
        result = await self._client.upload_file(job.image_path)
        job.image_url = result.download_url
        await self._repository.upsert(job)
        return result.download_url

    async def _create_audio(self, job: VideoJob) -> str:
        await self._transition(job, JobStatus.CREATING_AUDIO)
        created = await self._client.create_tts_task(job.script, job.voice)
        job.audio_task_id = created.task_id
        await self._transition(job, JobStatus.WAITING_AUDIO)
        audio_url = await self._poll_for_url(created.task_id, kind="audio")
        job.audio_url = audio_url
        await self._repository.upsert(job)
        return audio_url

    async def _produce_video(self, job: VideoJob, image_url: str, audio_url: str) -> str:
        await self._transition(job, JobStatus.CREATING_AVATAR)
        created = await self._client.create_avatar_task(image_url, audio_url, job.prompt)
        job.video_task_id = created.task_id
        await self._transition(job, JobStatus.WAITING_VIDEO)
        video_url = await self._poll_for_url(created.task_id, kind="video")
        job.video_url = video_url
        await self._repository.upsert(job)
        return video_url

    async def _download_final(self, job: VideoJob, video_url: str) -> None:
        await self._transition(job, JobStatus.DOWNLOADING)
        out = Path(self._settings.outputs_dir) / sanitize_filename(job.id) / _FINAL_FILE_NAME
        await self._client.download_file(video_url, out)
        job.output_path = str(out)

    # --- helpers ----------------------------------------------------------

    async def _transition(self, job: VideoJob, status: JobStatus) -> None:
        """Mutación + persistencia atómica (write-ahead). Solo este método cambia status."""
        job.status = status
        await self._repository.upsert(job)

    async def _fail(self, job: VideoJob, exc: BaseException) -> None:
        job.error = str(exc) or exc.__class__.__name__
        await self._transition(job, JobStatus.FAILED)

    async def _poll_for_url(self, task_id: str, *, kind: str) -> str:
        """Delegación al helper compartido `polling.poll_task_for_url`."""
        return await poll_task_for_url(
            self._client,
            task_id,
            kind=kind,
            interval_seconds=self._settings.poll_interval_seconds,
            timeout_seconds=self._settings.task_timeout_seconds,
        )
