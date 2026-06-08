"""`ImageJobRunner`: ejecuta UN `ImageJob` siguiendo la state machine.

Mirror de `AudioJobRunner` pero para imágenes generadas por Nano Banana 2.
Cumple `RunnableRunner[ImageJob]` del puerto `domain.ports`.

State machine (mirror reducido del audio: sin upload local de assets, sin
descarga eager del resultado):

    queued → validating → creating → polling → completed | failed

Cada transición se persiste en `ImageJobRepository` ANTES de avanzar
(write-ahead). Al COMPLETED, además persiste el `GeneratedImage` final
en `GeneratedImageStore`. `GeneratedImage.id` == `ImageJob.id` por
idempotencia: un retry no duplica filas en `generated_images`.

### Revalidación de refs (CRÍTICO)

Las refs (`image_input`) del `ImageJob` pueden vencer entre el momento
de encolar y el momento de ejecutar. El runner revalida cada ref justo
antes de `create_nano_banana_task`, consultando el store correspondiente
según `kind` y aplicando la política de retención de Kie:

- `kind == UPLOADED`: `KIE_UPLOAD_RETENTION_HOURS` (24h).
- `kind == GENERATED`: `KIE_GENERATED_RETENTION_DAYS` (14d).

Si alguna ref expiró, el job falla con `KieError` claro (sin pegarle a
Kie con una URL inválida que devolvería un 422 críptico).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from loguru import logger

from ..config import Settings
from ..domain.errors import (
    GeneratedImageExpiredError,
    GeneratedImageNotFoundError,
    ImageExpiredError,
    ImageNotFoundError,
    JobValidationError,
    KieError,
)
from ..domain.models import (
    GeneratedImage,
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
)
from ..domain.policies import (
    KIE_GENERATED_RETENTION_DAYS,
    KIE_UPLOAD_RETENTION_HOURS,
    validate_image_prompt,
    validate_image_refs,
    validate_image_settings,
)
from ..domain.ports import (
    GeneratedImageStore,
    ImageJobRepository,
    ImageStore,
    KieGateway,
)
from .polling import poll_task_for_url


class ImageJobRunner:
    """Ejecuta un `ImageJob` end-to-end y persiste cada transición."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        repository: ImageJobRepository,
        generated_store: GeneratedImageStore,
        uploaded_store: ImageStore,
        *,
        upload_retention_hours: int = KIE_UPLOAD_RETENTION_HOURS,
        generated_retention_days: int = KIE_GENERATED_RETENTION_DAYS,
    ) -> None:
        self._settings = settings
        self._client = client
        self._repository = repository
        self._generated_store = generated_store
        self._uploaded_store = uploaded_store
        self._upload_retention_hours = upload_retention_hours
        self._generated_retention_days = generated_retention_days

    async def run(self, job: ImageJob) -> ImageJob:
        try:
            refs, settings = await self._validate(job)
            task_id = await self._create_task(job, refs=refs, settings=settings)
            image_url = await self._poll_for_url(task_id)
            await self._finalize(job, image_url, refs_count=len(refs), settings=settings)
        except (KieError, JobValidationError) as exc:
            # KieError cubre los errores HTTP de Kie y los NotFound de
            # store local (ImageNotFoundError, GeneratedImageNotFoundError).
            # JobValidationError cubre fallas de validación de prompt/settings/refs
            # y las expiraciones tipadas (ImageExpiredError, GeneratedImageExpiredError,
            # ImageGenerationValidationError).
            await self._fail(job, exc)
        except Exception as exc:
            logger.exception("ImageJob {} falló con error no manejado", job.id)
            await self._fail(job, exc)
        return job

    # --- pasos de la state machine ----------------------------------------

    async def _validate(self, job: ImageJob) -> tuple[list[ImageAssetRef], ImageGenerationSettings]:
        """Valida prompt + settings + refs (incluyendo expiración actual).

        Devuelve la lista resuelta de refs y el `ImageGenerationSettings`
        ya parseado para evitar reparsear en `_create_task`.
        """
        await self._transition(job, ImageJobStatus.VALIDATING)
        validate_image_prompt(job.prompt)
        settings = self._parse_settings(job)
        validate_image_settings(settings)
        refs = self._parse_refs(job)
        validate_image_refs(refs)
        await self._revalidate_refs_freshness(refs)
        return refs, settings

    async def _create_task(
        self,
        job: ImageJob,
        *,
        refs: list[ImageAssetRef],
        settings: ImageGenerationSettings,
    ) -> str:
        # Si el job ya tenía `task_id` (resume desde POLLING), reusamos ese
        # task en Kie en vez de crear uno nuevo. Evita doble cobro.
        if job.task_id:
            logger.info("ImageJob {} reanudado con task existente {}", job.id, job.task_id)
            await self._transition(job, ImageJobStatus.POLLING)
            return job.task_id

        await self._transition(job, ImageJobStatus.CREATING)
        created = await self._client.create_nano_banana_task(
            job.prompt,
            image_input=[ref.kie_url for ref in refs],
            aspect_ratio=settings.aspect_ratio,
            resolution=settings.resolution,
            output_format=settings.output_format,
        )
        job.task_id = created.task_id
        await self._transition(job, ImageJobStatus.POLLING)
        return created.task_id

    async def _poll_for_url(self, task_id: str) -> str:
        return await poll_task_for_url(
            self._client,
            task_id,
            kind="image",
            interval_seconds=self._settings.poll_interval_seconds,
            timeout_seconds=self._settings.task_timeout_seconds,
        )

    async def _finalize(
        self,
        job: ImageJob,
        image_url: str,
        *,
        refs_count: int,
        settings: ImageGenerationSettings,
    ) -> None:
        job.kie_url = image_url
        job.kie_file_path = _derive_file_path(image_url)
        await self._repository.upsert(job)
        # Persistimos el `GeneratedImage` con id == job.id para idempotencia:
        # un retry hace upsert sobre la misma fila, no duplica registros.
        await self._generated_store.upsert(
            self._build_generated_image(job, refs_count=refs_count, settings=settings)
        )
        await self._transition(job, ImageJobStatus.COMPLETED)
        logger.info("ImageJob {} ('{}') completado", job.id, job.label)

    # --- helpers ----------------------------------------------------------

    async def _transition(self, job: ImageJob, status: ImageJobStatus) -> None:
        """Mutación + persistencia atómica (write-ahead)."""
        job.status = status
        await self._repository.upsert(job)

    async def _fail(self, job: ImageJob, exc: BaseException) -> None:
        job.error = str(exc) or exc.__class__.__name__
        await self._transition(job, ImageJobStatus.FAILED)

    @staticmethod
    def _parse_settings(job: ImageJob) -> ImageGenerationSettings:
        if not job.settings_json:
            return ImageGenerationSettings()
        return ImageGenerationSettings.model_validate_json(job.settings_json)

    @staticmethod
    def _parse_refs(job: ImageJob) -> list[ImageAssetRef]:
        if not job.refs_json:
            return []
        raw = json.loads(job.refs_json)
        if not isinstance(raw, list):
            raise JobValidationError(
                f"refs_json debe ser una lista de ImageAssetRef (recibí: {type(raw).__name__})"
            )
        return [ImageAssetRef.model_validate(item) for item in raw]

    async def _revalidate_refs_freshness(self, refs: list[ImageAssetRef]) -> None:
        """Verifica que cada ref siga existiendo y no haya vencido en Kie.

        Si el job estuvo encolado mucho tiempo, una ref puede haberse
        borrado del catálogo local o haber superado su TTL en Kie. Es
        mejor fallar con error claro acá que mandar una URL inválida y
        que Kie devuelva un 422 genérico que cuesta créditos.

        - `UPLOADED`: TTL 24h en Kie. Re-consultamos el store por id.
        - `GENERATED`: TTL 14d en Kie. Idem contra `generated_images`.
        """
        now = datetime.now(UTC)
        for ref in refs:
            if ref.kind == ImageAssetKind.UPLOADED:
                stored = await self._uploaded_store.get(ref.id)
                if stored is None:
                    raise ImageNotFoundError(
                        f"ref '{ref.label}' (uploaded) ya no existe en el catálogo local"
                    )
                if stored.is_expired(self._upload_retention_hours, now=now):
                    raise ImageExpiredError(
                        f"ref '{ref.label}' (uploaded) expiró en Kie hace "
                        f"{-stored.time_left(self._upload_retention_hours, now=now)}"
                    )
            else:
                gen = await self._generated_store.get(ref.id)
                if gen is None:
                    raise GeneratedImageNotFoundError(
                        f"ref '{ref.label}' (generated) ya no existe en el catálogo local"
                    )
                if gen.is_expired(self._generated_retention_days, now=now):
                    raise GeneratedImageExpiredError(
                        f"ref '{ref.label}' (generated) expiró en Kie hace "
                        f"{-gen.time_left(self._generated_retention_days, now=now)}"
                    )

    def _build_generated_image(
        self,
        job: ImageJob,
        *,
        refs_count: int,
        settings: ImageGenerationSettings,
    ) -> GeneratedImage:
        if job.kie_url is None or job.kie_file_path is None:
            raise KieError(
                f"ImageJob {job.id} sin kie_url/kie_file_path al finalizar; "
                "indica un bug en la transición a COMPLETED."
            )
        return GeneratedImage(
            id=job.id,
            label=job.label,
            prompt=job.prompt,
            settings=settings,
            refs_count=refs_count,
            kie_url=job.kie_url,
            kie_file_path=job.kie_file_path,
        )


def _derive_file_path(url: str) -> str:
    """Extrae el path relativo del archivo en Kie a partir de la URL pública.

    Espejo de la función homónima en `audio_job_runner.py`. Sirve para
    mostrar un identificador estable en la tabla (sin el host
    `tempfile.redpandaai.co/...`). Si la URL no tiene path, devuelve la
    URL completa como fallback defensivo.
    """
    _, separator, rest = url.partition("://")
    if not separator or "/" not in rest:
        return url
    _, _, path = rest.partition("/")
    return path or url
