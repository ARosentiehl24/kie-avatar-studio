"""Controller para administrar la generación de imágenes Nano Banana 2.

Mirror de `AudiosController` pero para imágenes. Mantenemos separado de
`ImagesController` (que solo gestiona uploads) para no romper SRP: este
controller orquesta la cola + persistencia de los `ImageJob` y de los
`GeneratedImage` resultantes.

Casos de uso:
- `enqueue_generation`: valida label, persiste el `ImageJob` y lo encola.
- `wait_for_job`: bloquea hasta terminal (mismo patrón que audio).
- `subscribe`: registra listener al stream `ImageJobUpdated`.
- `cancel` / `retry` para jobs activos o terminales fallidos.
- `list_generated` / `get` / `get_for_use` sobre `GeneratedImage`.
- `cleanup_expired`: barre imágenes cuya retención en Kie ya venció.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable

from loguru import logger

from ..domain.errors import (
    GeneratedImageExpiredError,
    GeneratedImageNotFoundError,
)
from ..domain.events import ImageJobUpdated
from ..domain.models import (
    GeneratedImage,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
)
from ..domain.policies import KIE_GENERATED_RETENTION_DAYS, validate_image_label
from ..domain.ports import GeneratedImageStore, ImageJobRepository
from .ids import new_image_job_id
from .queue_manager import QueueManager

ImageEventListener = (
    Callable[[ImageJobUpdated], None] | Callable[[ImageJobUpdated], Awaitable[None]]
)


class GeneratedImagesController:
    """Casos de uso sobre imágenes generadas. Encola; no espera el resultado."""

    def __init__(
        self,
        store: GeneratedImageStore,
        image_jobs_repo: ImageJobRepository,
        queue: QueueManager[ImageJob, ImageJobUpdated],
        *,
        retention_days: int = KIE_GENERATED_RETENTION_DAYS,
    ) -> None:
        self._store = store
        self._jobs_repo = image_jobs_repo
        self._queue = queue
        self._retention_days = retention_days

    @property
    def retention_days(self) -> int:
        return self._retention_days

    async def list_generated(self) -> list[GeneratedImage]:
        return await self._store.list_recent()

    async def list_image_jobs(self, limit: int = 50) -> list[ImageJob]:
        return await self._jobs_repo.list_recent(limit)

    async def get_image_job(self, job_id: str) -> ImageJob | None:
        return await self._jobs_repo.get(job_id)

    async def get(self, image_id: str) -> GeneratedImage | None:
        return await self._store.get(image_id)

    async def get_for_use(self, image_id: str) -> GeneratedImage:
        """Devuelve la imagen lista para reutilizar (selector de refs / video).

        Lanza:
        - `GeneratedImageNotFoundError` si no existe en el store local.
        - `GeneratedImageExpiredError` si ya superó la ventana de
          retención de Kie.
        """
        image = await self._store.get(image_id)
        if image is None:
            raise GeneratedImageNotFoundError(
                f"no existe ninguna imagen generada con id={image_id!r}"
            )
        if image.is_expired(self._retention_days):
            raise GeneratedImageExpiredError(
                f"la imagen '{image.label}' expiró en Kie hace "
                f"{-image.time_left(self._retention_days)}; regenerala."
            )
        return image

    async def enqueue_generation(
        self,
        label: str,
        prompt: str,
        settings: ImageGenerationSettings | None = None,
        refs: list[ImageAssetRef] | None = None,
    ) -> ImageJob:
        """Crea un `ImageJob`, lo persiste como QUEUED y lo encola.

        La validación detallada (prompt, settings, refs) ocurre en
        `ImageJobRunner._validate` (mismo patrón que audio). Acá
        solo limpiamos el label y serializamos settings/refs a JSON.
        """
        clean_label = validate_image_label(label)
        settings_json: str | None = None
        if settings is not None:
            settings_json = settings.model_dump_json(exclude_none=True)
        refs_json: str | None = None
        if refs:
            refs_json = json.dumps([r.model_dump(mode="json") for r in refs])
        job = ImageJob(
            id=new_image_job_id(),
            label=clean_label,
            prompt=prompt,
            settings_json=settings_json,
            refs_json=refs_json,
            status=ImageJobStatus.QUEUED,
        )
        await self._jobs_repo.upsert(job)
        self._queue.enqueue(job)
        logger.info("ImageJob '{}' encolado (id={})", clean_label, job.id)
        return job

    async def wait_for_job(self, job_id: str) -> ImageJob:
        """Bloquea hasta que el job llegue a un estado terminal.

        Mismo patrón que `AudiosController.wait_for_job` (registrar el
        listener antes del lookup para no perder eventos durante el
        await).
        """
        done: asyncio.Future[ImageJob] = asyncio.get_running_loop().create_future()

        def on_event(event: ImageJobUpdated) -> None:
            if event.job.id != job_id or done.done():
                return
            if event.job.is_terminal():
                done.set_result(event.job)

        unsubscribe = self._queue.add_listener(on_event)
        try:
            existing = await self._jobs_repo.get(job_id)
            if existing is not None and existing.is_terminal() and not done.done():
                done.set_result(existing)
            return await done
        finally:
            unsubscribe()

    def subscribe(self, callback: ImageEventListener) -> Callable[[], None]:
        return self._queue.add_listener(callback)

    async def cancel(self, job_id: str) -> bool:
        return await self._queue.cancel(job_id)

    async def retry(self, job_id: str) -> bool:
        job = await self._jobs_repo.get(job_id)
        if job is None:
            return False
        return await self._queue.retry(job)

    async def delete(self, image_id: str) -> None:
        await self._store.delete(image_id)

    async def delete_job(self, job_id: str) -> None:
        """Borra el `ImageJob` Y (si existe) el `GeneratedImage` con el mismo id.

        Mismo id por idempotencia (ver `ImageJobRunner._finalize`). El
        borrado de la imagen generada es tolerante: si no existe no
        rompe — los jobs failed/cancelled nunca la generaron.
        """
        with contextlib.suppress(GeneratedImageNotFoundError):
            await self._store.delete(job_id)
        await self._jobs_repo.delete(job_id)

    async def cleanup_expired(self) -> list[GeneratedImage]:
        """Borra del store local todas las imágenes cuyo TTL ya venció en Kie.

        Idempotente: una segunda llamada no borra nada.
        """
        all_images = await self._store.list_recent()
        expired = [i for i in all_images if i.is_expired(self._retention_days)]
        if not expired:
            return []
        await self._store.delete_many([i.id for i in expired])
        for image in expired:
            logger.info(
                "Imagen generada '{}' quitada del registro local (expiró en Kie hace {})",
                image.id,
                -image.time_left(self._retention_days),
            )
        return expired
