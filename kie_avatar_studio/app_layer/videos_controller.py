"""Controller para `VideoJob`: encolar generaciones desde assets ya existentes.

Espejo simétrico de `AudiosController` (post Etapa 3 del refactor de cola
estructurada). El controller solo orquesta la persistencia local y
delega la ejecución al `queue` de video (que usa `JobRunner` para upload
+ TTS + avatar). El `JobRunner` ya soporta el "modo asset reuse": si el
`VideoJob` viene con `image_url` y/o `audio_url` poblados, salta esos
pasos para no regastar créditos.

Esta versión solo expone `enqueue_from_assets` (Modo B del refactor de
Nuevo Video): el usuario elige una `UploadedImage` + un `GeneratedAudio`
ya existentes y agrega solo el prompt. Cuando se implemente el form de
"video desde cero" (Modo A), se agregará `enqueue_from_scratch` con
imagen local + script + voz.

Casos de uso que maneja:
- `enqueue_from_assets`: valida assets + arma VideoJob con URLs ya
  pobladas + encola.
- `wait_for_job`: helper análogo al de Audios para que la UI pueda
  bloquearse opcionalmente hasta el terminal.
- `subscribe`: registra listener al stream de `JobUpdated`.
- `cancel` / `retry` / `delete_job` / `list_video_jobs` /
  `get_video_job`: idénticos en shape a los del AudiosController.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Final

from loguru import logger

from ..domain.errors import (
    AudioExpiredError,
    AudioNotFoundError,
    ImageExpiredError,
    ImageNotFoundError,
    JobValidationError,
)
from ..domain.events import JobUpdated
from ..domain.models import JobStatus, VideoJob
from ..domain.ports import AudioStore, ImageStore, JobRepository
from .ids import new_job_id
from .queue_manager import QueueManager

_PROMPT_MAX_LENGTH: Final[int] = 5000

VideoEventListener = Callable[[JobUpdated], None] | Callable[[JobUpdated], Awaitable[None]]


class VideosController:
    """Casos de uso sobre `VideoJob`. Encola desde assets reusables."""

    def __init__(
        self,
        repo: JobRepository,
        images_store: ImageStore,
        audios_store: AudioStore,
        queue: QueueManager[VideoJob, JobUpdated],
    ) -> None:
        self._repo = repo
        self._images = images_store
        self._audios = audios_store
        self._queue = queue

    # --- reads -------------------------------------------------------------

    async def list_video_jobs(self, limit: int = 50) -> list[VideoJob]:
        """Lista los video jobs recientes (en cola, generando, terminales)."""
        return await self._repo.list_recent(limit)

    async def get_video_job(self, job_id: str) -> VideoJob | None:
        return await self._repo.get(job_id)

    # --- enqueue -----------------------------------------------------------

    async def enqueue_from_assets(
        self,
        image_id: str,
        audio_id: str,
        prompt: str,
    ) -> VideoJob:
        """Crea un `VideoJob` reusando una imagen + un audio ya en Kie.

        Resuelve los assets pasados por id, valida que no estén expirados
        y arma el `VideoJob` con `image_url` y `audio_url` ya poblados.
        El runner saltará el upload y el TTS (Modo B).

        Lanza:
        - `ImageNotFoundError` / `ImageExpiredError` si la imagen no
          existe o ya venció en Kie.
        - `AudioNotFoundError` / `AudioExpiredError` análogo para audio.
        - `JobValidationError` si el prompt está vacío o excede el límite.
        """
        clean_prompt = self._validate_prompt(prompt)
        image = await self._images.get(image_id)
        if image is None:
            raise ImageNotFoundError(f"no existe ninguna imagen con id={image_id!r}")
        # Las constantes de retención están en domain.policies; las
        # importamos local para no inflar la lista de imports del
        # controller. Imagen: 24h (File Upload API). Audio: 14d
        # (generated media). Son ventanas distintas a propósito — Kie
        # las trata diferente.
        from ..domain.policies import KIE_GENERATED_RETENTION_DAYS, KIE_UPLOAD_RETENTION_HOURS

        if image.is_expired(KIE_UPLOAD_RETENTION_HOURS):
            raise ImageExpiredError(
                f"la imagen '{image.label}' expiró en Kie hace "
                f"{-image.time_left(KIE_UPLOAD_RETENTION_HOURS)} "
                "(Kie expira los uploads tras 24h). Subila de nuevo."
            )

        audio = await self._audios.get(audio_id)
        if audio is None:
            raise AudioNotFoundError(f"no existe ningún audio con id={audio_id!r}")
        if audio.is_expired(KIE_GENERATED_RETENTION_DAYS):
            raise AudioExpiredError(
                f"el audio '{audio.label}' expiró en Kie hace "
                f"{-audio.time_left(KIE_GENERATED_RETENTION_DAYS)}; regeneralo."
            )

        job = VideoJob(
            id=new_job_id(),
            prompt=clean_prompt,
            # Script y voz se copian solo como metadata informativa: el
            # runner los va a ignorar porque `audio_url` ya está poblado.
            script=audio.script,
            voice=audio.voice_id,
            # `image_path` queda vacío a propósito: validate_job lo
            # detecta y saltea su validación (la imagen ya está en Kie).
            image_path="",
            image_url=image.kie_url,
            audio_url=audio.kie_url,
            status=JobStatus.QUEUED,
        )
        await self._repo.upsert(job)
        self._queue.enqueue(job)
        logger.info(
            "VideoJob encolado (id={}, imagen='{}', audio='{}')",
            job.id,
            image.label,
            audio.label,
        )
        return job

    async def enqueue_from_scratch(
        self,
        *,
        script: str,
        image_path: str,
        voice: str,
        prompt: str,
    ) -> VideoJob:
        """Crea un `VideoJob` desde assets locales (Modo A: batch / from scratch).

        El runner hará upload de la imagen + TTS del script + avatar gen.
        Validación delegada a `domain.policies.validate_job` (mismo contrato
        que `JobRunner` aplica al levantar el job de la cola): los caller
        que ya validaron arriba (ej. `BatchController`) no sufren doble
        chequeo porque `validate_job` es idempotente y barato.

        Lanza `JobValidationError` si script/prompt/imagen no cumplen las
        políticas de Kie.
        """
        from ..domain.policies import validate_job

        job = VideoJob(
            id=new_job_id(),
            script=script,
            image_path=image_path,
            prompt=prompt,
            voice=voice,
            status=JobStatus.QUEUED,
        )
        validate_job(job)
        await self._repo.upsert(job)
        self._queue.enqueue(job)
        logger.info(
            "VideoJob encolado from-scratch (id={}, image={!r}, voice={!r})",
            job.id,
            image_path,
            voice,
        )
        return job

    # --- stream + acciones -------------------------------------------------

    async def wait_for_job(self, job_id: str) -> VideoJob:
        """Bloquea hasta que el job llegue a un estado terminal.

        Mismo patrón register-first-then-check que `AudiosController.wait_for_job`
        para evitar perder eventos entre el lookup y el listener.
        """
        done: asyncio.Future[VideoJob] = asyncio.get_running_loop().create_future()

        def on_event(event: JobUpdated) -> None:
            if event.job.id != job_id or done.done():
                return
            if event.job.is_terminal():
                done.set_result(event.job)

        unsubscribe = self._queue.add_listener(on_event)
        try:
            existing = await self._repo.get(job_id)
            if existing is not None and existing.is_terminal() and not done.done():
                done.set_result(existing)
            return await done
        finally:
            unsubscribe()

    def subscribe(self, callback: VideoEventListener) -> Callable[[], None]:
        """Registra un listener para eventos `JobUpdated` en vivo."""
        return self._queue.add_listener(callback)

    async def cancel(self, job_id: str) -> bool:
        return await self._queue.cancel(job_id)

    async def retry(self, job_id: str) -> bool:
        job = await self._repo.get(job_id)
        if job is None:
            return False
        return await self._queue.retry(job)

    async def delete_job(self, job_id: str) -> None:
        """Borra el `VideoJob`. El `final.mp4` en outputs/ se conserva.

        Decisión: el binario en disco es del usuario; el controller solo
        toca el registro local. Si quiere borrar el video físico, tiene
        el path en `job.output_path` y un file manager.
        """
        with contextlib.suppress(Exception):
            await self._repo.delete(job_id)

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _validate_prompt(prompt: str) -> str:
        clean = prompt.strip()
        if not clean:
            raise JobValidationError("el prompt del video no puede estar vacío")
        if len(clean) > _PROMPT_MAX_LENGTH:
            raise JobValidationError(f"el prompt del video supera {_PROMPT_MAX_LENGTH} caracteres")
        return clean
