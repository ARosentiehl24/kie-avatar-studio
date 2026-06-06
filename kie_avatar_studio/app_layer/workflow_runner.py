"""`WorkflowRunner`: orquesta UN `WorkflowJob` end-to-end.

Responsabilidades:
1. Resolver la imagen base de la modelo (`pre_settings.model_creation`).
2. Lanzar todos los steps en paralelo respetando el `_capacity_limiter`
   global (cada step adquiere slots cuando llega a sub-jobs reales).
3. Serializar las transiciones de los steps con un `asyncio.Lock` por
   workflow (evita lost updates concurrentes).
4. Persistir y regenerar el manifest atómicamente en cada transición.
5. Emitir el evento `WorkflowJobUpdated` al listener del queue.
6. Decidir el status final (COMPLETED / PARTIALLY_FAILED / FAILED).

Cumple `RunnableRunner[WorkflowJob]` del puerto `domain.ports`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from loguru import logger

from ..config import Settings
from ..domain.errors import (
    KieError,
    WorkflowStepError,
    WorkflowValidationError,
)
from ..domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ModelCreation,
    ModelCreationMethod,
    VoicePreset,
    VoiceSettings,
    WorkflowJob,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from ..domain.policies import (
    KIE_GENERATED_RETENTION_DAYS,
    KIE_UPLOAD_RETENTION_HOURS,
    validate_image_path,
    validate_workflow,
)
from ..domain.ports import (
    AudioJobRepository,
    AudioStore,
    GeneratedImageStore,
    ImageJobRepository,
    ImageStore,
    KieGateway,
    VoicePresetStore,
    WorkflowManifestWriter,
    WorkflowRepository,
)
from .ids import new_image_job_id
from .image_job_runner import ImageJobRunner
from .workflow_step_runner import (
    WorkflowExecutionContext,
    WorkflowStepRunner,
)

BASE_IMAGE_FILENAME: Final[str] = "base.png"
_DEFAULT_VOICE_ID: Final[str] = "pNInz6obpgDQGcFmaJgB"  # Adam — fallback si no hay preset


WorkflowNotify = Callable[[WorkflowJob], Awaitable[None] | None]
"""Callback opcional que se llama tras cada transición (UI listener)."""


class WorkflowRunner:
    """Orquesta un `WorkflowJob` end-to-end y emite eventos al callback."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        repository: WorkflowRepository,
        manifest_writer: WorkflowManifestWriter,
        step_runner: WorkflowStepRunner,
        presets_store: VoicePresetStore,
        uploaded_images: ImageStore,
        generated_images: GeneratedImageStore,
        image_jobs_repo: ImageJobRepository,
        audio_jobs_repo: AudioJobRepository,
        audios_store: AudioStore,
        capacity_limiter: asyncio.Semaphore,
        *,
        notify: WorkflowNotify | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._repository = repository
        self._manifest_writer = manifest_writer
        self._step_runner = step_runner
        self._presets_store = presets_store
        self._uploaded_images = uploaded_images
        self._generated_images = generated_images
        self._image_jobs_repo = image_jobs_repo
        self._audio_jobs_repo = audio_jobs_repo
        self._audios_store = audios_store
        self._capacity_limiter = capacity_limiter
        self._notify = notify
        # Lock por workflow_id para serializar transiciones (steps paralelos
        # transicionando contra el mismo workflow object).
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(self, job: WorkflowJob) -> WorkflowJob:
        try:
            validate_workflow(job)
            voice_id, voice_settings = await self._resolve_voice(job)
            base_ref = await self._resolve_base_image(job)
            output_dir = Path(job.output_dir)
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
            await self._download_base_image_locally(base_ref, output_dir)
            await self._mark_running(job)
            context = WorkflowExecutionContext(
                audio_language=job.pre_settings.audio_language,
                voice_id=voice_id,
                voice_settings=voice_settings,
                base_image_ref=base_ref,
                output_dir=output_dir,
            )
            await self._execute_steps(job, context)
            await self._finalize_workflow(job)
        except asyncio.CancelledError:
            await self._mark_cancelled(job)
            raise
        except (WorkflowValidationError, WorkflowStepError, KieError) as exc:
            await self._fail_workflow(job, exc)
        except Exception as exc:
            logger.exception("WorkflowJob {} falló con error no manejado", job.id)
            await self._fail_workflow(job, exc)
        return job

    # --- voice / base image resolution ------------------------------------

    async def _resolve_voice(
        self, job: WorkflowJob
    ) -> tuple[str, VoiceSettings | None]:
        """Resuelve voice_id + voice_settings desde el preset (si hay)."""
        preset_id = job.pre_settings.voice_preset_id
        if not preset_id:
            return _DEFAULT_VOICE_ID, None
        preset = await self._presets_store.get(preset_id)
        if preset is None:
            raise WorkflowValidationError(
                f"voice_preset '{preset_id}' no existe en el catálogo "
                "(revisá los presets configurados)."
            )
        return _voice_from_preset(preset)

    async def _resolve_base_image(self, job: WorkflowJob) -> ImageAssetRef:
        creation = job.pre_settings.model_creation
        await self._mark_preparing_base(job)
        if creation.method == ModelCreationMethod.PROMPT:
            return await self._resolve_base_from_prompt(job, creation)
        if creation.method == ModelCreationMethod.LOCAL:
            return await self._resolve_base_from_local(creation)
        return await self._resolve_base_from_catalog(creation)

    async def _resolve_base_from_prompt(
        self, job: WorkflowJob, creation: ModelCreation
    ) -> ImageAssetRef:
        """Genera la imagen base usando Nano Banana 2."""
        if not creation.prompt:
            raise WorkflowValidationError("model_creation.method='prompt' requiere prompt")
        from json import dumps

        from ..domain.models import (
            ImageGenerationSettings,
            ImageJob,
            ImageJobStatus,
        )

        image_job = ImageJob(
            id=new_image_job_id(),
            label=f"[wf-base]{job.slug}",
            prompt=creation.prompt,
            settings_json=ImageGenerationSettings().model_dump_json(exclude_none=True),
            refs_json=dumps([]),
            status=ImageJobStatus.QUEUED,
        )
        await self._image_jobs_repo.upsert(image_job)
        runner = ImageJobRunner(
            self._settings,
            self._client,
            self._image_jobs_repo,
            self._generated_images,
            self._uploaded_images,
        )
        async with self._capacity_limiter:
            await runner.run(image_job)
        if image_job.status != ImageJobStatus.COMPLETED or not image_job.kie_url:
            raise WorkflowValidationError(
                f"falló la generación de la imagen base ({image_job.error or 'sin mensaje'})"
            )
        generated = await self._generated_images.get(image_job.id)
        if generated is None:
            raise WorkflowValidationError(
                "la imagen base generada no apareció en el store local"
            )
        ref = ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id=generated.id,
            label=generated.label,
            kie_url=generated.kie_url,
            expires_at=generated.expires_at(KIE_GENERATED_RETENTION_DAYS),
        )
        creation.resolved_image_ref = ref
        return ref

    async def _resolve_base_from_local(self, creation: ModelCreation) -> ImageAssetRef:
        """Sube una imagen local con `KieGateway.upload_file`."""
        if not creation.local_path:
            raise WorkflowValidationError(
                "model_creation.method='local' requiere local_path"
            )
        path = Path(creation.local_path)
        # Revalidación: el archivo puede haber sido movido/borrado entre
        # la validación inicial y el momento del upload.
        validate_image_path(path)
        result = await self._client.upload_file(path)
        from datetime import timedelta

        expires_at = datetime.now(UTC) + timedelta(hours=KIE_UPLOAD_RETENTION_HOURS)
        ref = ImageAssetRef(
            kind=ImageAssetKind.UPLOADED,
            id=result.file_path,
            label=path.name,
            kie_url=result.download_url,
            expires_at=expires_at,
        )
        creation.resolved_image_ref = ref
        return ref

    async def _resolve_base_from_catalog(self, creation: ModelCreation) -> ImageAssetRef:
        """Resuelve la imagen base desde el catálogo (uploaded/generated)."""
        if creation.asset_kind is None or not creation.asset_id:
            raise WorkflowValidationError(
                "model_creation.method='catalog' requiere asset_kind y asset_id"
            )
        if creation.asset_kind == ImageAssetKind.UPLOADED:
            uploaded = await self._uploaded_images.get(creation.asset_id)
            if uploaded is None:
                raise WorkflowValidationError(
                    f"imagen subida '{creation.asset_id}' no existe en el catálogo"
                )
            expires = uploaded.expires_at(KIE_UPLOAD_RETENTION_HOURS)
            ref = ImageAssetRef(
                kind=ImageAssetKind.UPLOADED,
                id=uploaded.id,
                label=uploaded.label,
                kie_url=uploaded.kie_url,
                expires_at=expires,
            )
        else:
            generated = await self._generated_images.get(creation.asset_id)
            if generated is None:
                raise WorkflowValidationError(
                    f"imagen generada '{creation.asset_id}' no existe en el catálogo"
                )
            ref = ImageAssetRef(
                kind=ImageAssetKind.GENERATED,
                id=generated.id,
                label=generated.label,
                kie_url=generated.kie_url,
                expires_at=generated.expires_at(KIE_GENERATED_RETENTION_DAYS),
            )
        creation.resolved_image_ref = ref
        return ref

    async def _download_base_image_locally(
        self, ref: ImageAssetRef, output_dir: Path
    ) -> None:
        """Descarga la imagen base a `output_dir/base.png` para uso del usuario."""
        target = output_dir / BASE_IMAGE_FILENAME
        await self._client.download_file(ref.kie_url, target)

    # --- step orchestration -----------------------------------------------

    async def _execute_steps(
        self, job: WorkflowJob, context: WorkflowExecutionContext
    ) -> None:
        """Lanza todos los steps en paralelo. Recolecta excepciones por step.

        El semáforo global vive en el `WorkflowStepRunner` (vía sus
        helpers + executors). El workflow_runner no consume slots del
        global — vive en su propio `_workflows_limiter` aplicado por el
        `QueueManager` superior.
        """
        async def _run_one(step: WorkflowStep) -> None:
            await self._step_runner.run(step, context, self._build_step_transition(job))

        tasks = [asyncio.create_task(_run_one(s), name=f"wf-{job.id}-step-{s.step}") for s in job.steps]
        await asyncio.gather(*tasks, return_exceptions=True)

    def _build_step_transition(
        self, job: WorkflowJob
    ) -> Callable[[WorkflowStep], Awaitable[None]]:
        """Crea el callback que el step runner llama tras cada transición.

        Serializa con el lock del workflow para evitar lost updates.
        Persiste el step + regenera el manifest + notifica al listener.
        """
        lock = self._lock_for(job.id)

        async def _on_transition(step: WorkflowStep) -> None:
            async with lock:
                await self._repository.upsert_step(job.id, step)
                await self._write_manifest(job)
                await self._dispatch_notify(job)

        return _on_transition

    def _lock_for(self, workflow_id: str) -> asyncio.Lock:
        lock = self._locks.get(workflow_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[workflow_id] = lock
        return lock

    # --- workflow header transitions --------------------------------------

    async def _mark_preparing_base(self, job: WorkflowJob) -> None:
        job.status = WorkflowStatus.PREPARING_BASE
        await self._repository.update_workflow_header(job)
        await self._write_manifest(job)
        await self._dispatch_notify(job)

    async def _mark_running(self, job: WorkflowJob) -> None:
        job.status = WorkflowStatus.RUNNING
        await self._repository.update_workflow_header(job)
        await self._write_manifest(job)
        await self._dispatch_notify(job)

    async def _finalize_workflow(self, job: WorkflowJob) -> None:
        statuses = [s.status for s in job.steps]
        if all(s == WorkflowStepStatus.COMPLETED for s in statuses):
            job.status = WorkflowStatus.COMPLETED
        elif any(s == WorkflowStepStatus.COMPLETED for s in statuses):
            job.status = WorkflowStatus.PARTIALLY_FAILED
        else:
            job.status = WorkflowStatus.FAILED
        await self._repository.update_workflow_header(job)
        await self._write_manifest(job)
        await self._dispatch_notify(job)
        logger.info(
            "WorkflowJob {} ({}) finalizado con status={}", job.id, job.name, job.status.value
        )

    async def _fail_workflow(self, job: WorkflowJob, exc: BaseException) -> None:
        job.status = WorkflowStatus.FAILED
        job.error = str(exc) or exc.__class__.__name__
        await self._repository.update_workflow_header(job)
        await self._write_manifest(job)
        await self._dispatch_notify(job)

    async def _mark_cancelled(self, job: WorkflowJob) -> None:
        job.status = WorkflowStatus.CANCELLED
        await self._repository.update_workflow_header(job)
        await self._write_manifest(job)
        await self._dispatch_notify(job)

    # --- helpers ----------------------------------------------------------

    async def _write_manifest(self, job: WorkflowJob) -> None:
        """Regenera el manifest atómicamente. Fail-safe (nunca levanta).

        Si la escritura falla permanentemente, marca `manifest_write_failed`
        y persiste solo el header (no toca steps). El runner sigue.
        """
        ok = await self._manifest_writer.write(job)
        if not ok and not job.manifest_write_failed:
            job.manifest_write_failed = True
            try:
                await self._repository.update_workflow_header(job)
            except Exception:
                logger.exception(
                    "No se pudo persistir manifest_write_failed=True en workflow {}", job.id
                )

    async def _dispatch_notify(self, job: WorkflowJob) -> None:
        if self._notify is None:
            return
        try:
            result = self._notify(job)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.opt(exception=True).warning("listener de workflow falló")


# --- module-level helpers --------------------------------------------------


def _voice_from_preset(preset: VoicePreset) -> tuple[str, VoiceSettings | None]:
    return preset.voice_id, preset.voice_settings


__all__ = ["BASE_IMAGE_FILENAME", "WorkflowRunner"]
