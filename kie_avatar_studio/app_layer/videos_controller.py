"""Controller para `VideoJob`: encolar generaciones desde assets ya existentes.

Espejo simétrico de `AudiosController` (post Etapa 3 del refactor de cola
estructurada). El controller solo orquesta la persistencia local y
delega la ejecución al `queue` de video (que usa `JobRunner` para upload
+ TTS + avatar). El `JobRunner` ya soporta el "modo asset reuse": si el
`VideoJob` viene con `image_url` y/o `audio_url` poblados, salta esos
pasos para no regastar créditos.

Esta versión expone `enqueue_from_assets` (Modo B: imagen + audio
existentes) aceptando un `ImageAssetRef` discriminado para que pueda
elegirse indistintamente una `UploadedImage` (TTL 24h) o un
`GeneratedImage` (TTL 14d). La resolución y el chequeo de expiración
los hace `ImageCatalogController` (CR-3.7 + CR-2.1: un único lugar
sabe cómo combinar los dos stores).

Casos de uso que maneja:
- `enqueue_from_assets`: resuelve la ref de imagen + valida el audio +
  arma el `VideoJob` con URLs ya pobladas + encola.
- `enqueue_from_scratch`: imagen local + script + voz (sin cambios).
- `wait_for_job`, `subscribe`, `cancel`, `retry`, `delete_job`,
  `list_video_jobs`, `get_video_job`: idénticos en shape a
  `AudiosController`.
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
    JobValidationError,
)
from ..domain.events import JobUpdated
from ..domain.models import ImageAssetRef, JobStatus, VideoJob
from ..domain.policies import KIE_GENERATED_RETENTION_DAYS
from ..domain.ports import AudioStore, JobRepository
from .ids import new_job_id
from .image_catalog_controller import ImageCatalogController
from .queue_manager import QueueManager

_PROMPT_MAX_LENGTH: Final[int] = 5000

VideoEventListener = Callable[[JobUpdated], None] | Callable[[JobUpdated], Awaitable[None]]


class VideosController:
    """Casos de uso sobre `VideoJob`. Encola desde assets reusables."""

    def __init__(
        self,
        repo: JobRepository,
        image_catalog: ImageCatalogController,
        audios_store: AudioStore,
        queue: QueueManager[VideoJob, JobUpdated],
    ) -> None:
        self._repo = repo
        self._image_catalog = image_catalog
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
        image_ref: ImageAssetRef,
        audio_id: str,
        prompt: str,
    ) -> VideoJob:
        """Crea un `VideoJob` reusando una imagen + un audio ya en Kie.

        La imagen viene como `ImageAssetRef` discriminado (uploaded o
        generated): `ImageCatalogController` resuelve la URL actualizada
        y aplica el TTL correcto según `kind` (24h para uploaded, 14d
        para generated). Esto evita colisiones de id entre stores y
        garantiza que el caller no asuma equivocadamente el origen.

        Lanza:
        - `ImageNotFoundError` / `ImageExpiredError` para refs uploaded.
        - `GeneratedImageNotFoundError` / `GeneratedImageExpiredError`
          para refs generated.
        - `AudioNotFoundError` / `AudioExpiredError` para el audio.
        - `JobValidationError` si el prompt está vacío o excede el límite.
        """
        clean_prompt = self._validate_prompt(prompt)
        # El catalog resuelve la URL actualizada y valida expiración
        # según el `kind` de la ref (TTL correcto por origen).
        resolved_image = await self._image_catalog.resolve_asset(image_ref.kind, image_ref.id)

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
            image_url=resolved_image.kie_url,
            audio_url=audio.kie_url,
            status=JobStatus.QUEUED,
        )
        await self._repo.upsert(job)
        self._queue.enqueue(job)
        logger.info(
            "VideoJob encolado (id={}, imagen='{}' ({}), audio='{}')",
            job.id,
            resolved_image.label,
            resolved_image.kind.value,
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
