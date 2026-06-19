"""`WorkflowStepRunner`: ejecuta UN `WorkflowStep` end-to-end.

Todos los steps renderizan video con VEO 3.1. La generación de imagen
previa (Nano Banana / GPT Image) se mantiene igual que antes; lo único
que cambia es el backend de video:

- `_run_veo`: scene_image (opcional) + VEO 3.1 → `video.mp4`

El step runner NO escribe directamente a la DB ni al manifest. Recibe
un callback `on_transition(step)` que el `WorkflowRunner` provee y que
serializa persistencia + manifest + notificación.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from loguru import logger

from ..config import Settings
from ..domain.errors import StepAwaitingApprovalSignal, WorkflowStepError
from ..domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from ..domain.policies import KIE_GENERATED_RETENTION_DAYS, is_path_inside, validate_veo_step
from ..domain.ports import GeneratedImageStore, ImageJobRepository, KieGateway
from .ids import new_image_job_id
from .runner_factories import WorkflowRunnerFactory
from .veo_poller import poll_veo_task_for_url
from .workflow_execution_context import (
    WorkflowExecutionContext,
    build_scene_prompt,
    initialize_progress,
    mark_remaining_progress_failed,
    needs_scene_generation,
    ref_dict,
    set_progress,
)
from .workflow_kie_helpers import download_kie_asset

SCENE_IMAGE_FILENAME: Final[str] = "scene.png"
VIDEO_FILENAME: Final[str] = "video.mp4"
VEO_GENERATION_TYPE: Final[str] = "FIRST_AND_LAST_FRAMES_2_VIDEO"

# Compatibilidad hacia atrás para tests/imports externos.
AUDIO_FILENAME: Final[str] = "audio.mp3"
A_ROLL_VIDEO_FILENAME: Final[str] = VIDEO_FILENAME
B_ROLL_VIDEO_FILENAME: Final[str] = VIDEO_FILENAME

StepTransition = Callable[[WorkflowStep], Awaitable[None]]


class WorkflowStepRunner:
    """Ejecuta UN `WorkflowStep` usando VEO 3.1 para todo video."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        image_limiter: asyncio.Semaphore,
        *,
        audio_limiter: asyncio.Semaphore,
        video_limiter: asyncio.Semaphore,
        download_limiter: asyncio.Semaphore,
        image_jobs_repo: ImageJobRepository,
        generated_images_store: GeneratedImageStore,
        runner_factory: WorkflowRunnerFactory,
    ) -> None:
        self._settings = settings
        self._client = client
        self._image_limiter = image_limiter
        self._audio_limiter = audio_limiter
        self._video_limiter = video_limiter
        self._download_limiter = download_limiter
        self._image_jobs_repo = image_jobs_repo
        self._generated_images_store = generated_images_store
        self._runner_factory = runner_factory

    async def run(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> WorkflowStep:
        """Ejecuta el step y devuelve el resultado. Captura excepciones."""
        initialize_progress(step)
        self._mark_audio_progress_skipped(step)
        self._reset_audio_outputs(step)
        step.started_at = datetime.now(UTC)
        try:
            await self._run_veo(step, context, on_transition)
            step.status = WorkflowStepStatus.COMPLETED
            step.completed_at = datetime.now(UTC)
            await on_transition(step)
        except asyncio.CancelledError:
            step.status = WorkflowStepStatus.CANCELLED
            step.error = "cancelado"
            step.completed_at = datetime.now(UTC)
            await on_transition(step)
            raise
        except StepAwaitingApprovalSignal:
            raise
        except Exception as exc:
            logger.exception("Step {} ({}): falló", step.step, step.scene_name)
            step.status = WorkflowStepStatus.FAILED
            step.error = str(exc) or exc.__class__.__name__
            step.completed_at = datetime.now(UTC)
            mark_remaining_progress_failed(step)
            await on_transition(step)
        return step

    async def _run_veo(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> None:
        await self._transition(step, on_transition, status=WorkflowStepStatus.PREPARING)
        scene_ref = await self._prepare_scene_image(step, context, on_transition)
        await self._transition(
            step,
            on_transition,
            status=WorkflowStepStatus.RENDERING,
            progress_updates={WorkflowProgressKey.VIDEO: WorkflowProgressStatus.RUNNING},
        )
        await self._render_veo_to_step(step, context, scene_ref, on_transition)

    async def _render_veo_to_step(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_ref: ImageAssetRef,
        on_transition: StepTransition,
    ) -> None:
        output_path = self._video_output_path(step, context)
        video_url = await self._create_or_poll_veo_video(step, context, scene_ref)
        download_key = self._download_progress_key(step)
        await self._transition(
            step,
            on_transition,
            status=WorkflowStepStatus.DOWNLOADING,
            progress_updates={
                WorkflowProgressKey.VIDEO: WorkflowProgressStatus.COMPLETED,
                download_key: WorkflowProgressStatus.RUNNING,
            },
        )
        async with self._download_limiter:
            await download_kie_asset(client=self._client, url=video_url, output_path=output_path)
        step.video_path = str(output_path)
        set_progress(step, download_key, WorkflowProgressStatus.COMPLETED)

    async def _create_or_poll_veo_video(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_ref: ImageAssetRef,
    ) -> str:
        veo = context.veo_settings
        image_urls = [scene_ref.kie_url]
        validate_veo_step(VEO_GENERATION_TYPE, image_urls, veo.model, veo.duration)
        logger.debug(
            "workflow.step.veo step={} model={} aspect_ratio={} resolution={} duration={}",
            step.step,
            veo.model,
            veo.aspect_ratio,
            veo.resolution,
            veo.duration,
        )
        if step.video_task_id is None:
            async with self._video_limiter:
                created = await self._client.create_veo_video_task(
                    step.prompt,
                    image_urls=image_urls,
                    model=veo.model,
                    generation_type=VEO_GENERATION_TYPE,
                    aspect_ratio=veo.aspect_ratio,
                    resolution=veo.resolution,
                    duration=veo.duration,
                    enable_translation=veo.enable_translation,
                    watermark=veo.watermark,
                )
            step.video_task_id = created.task_id
        async with self._video_limiter:
            return await poll_veo_task_for_url(
                self._client,
                step.video_task_id,
                interval_seconds=self._settings.poll_interval_seconds,
                timeout_seconds=self._settings.task_timeout_seconds,
            )

    def _video_output_path(self, step: WorkflowStep, context: WorkflowExecutionContext) -> Path:
        step_dir = context.step_dir(step)
        output_path = step_dir / VIDEO_FILENAME
        if not is_path_inside(output_path, self._settings.outputs_dir):
            raise WorkflowStepError(f"step {step.step}: output video fuera de outputs_dir")
        step_dir.mkdir(parents=True, exist_ok=True)
        return output_path

    @staticmethod
    def _download_progress_key(step: WorkflowStep) -> WorkflowProgressKey:
        if WorkflowProgressKey.DOWNLOAD_VIDEO in step.progress:
            return WorkflowProgressKey.DOWNLOAD_VIDEO
        return WorkflowProgressKey.DOWNLOAD

    @staticmethod
    def _mark_audio_progress_skipped(step: WorkflowStep) -> None:
        """Marca como omitidos los hitos de audio del flujo legado."""
        if WorkflowProgressKey.AUDIO in step.progress:
            set_progress(step, WorkflowProgressKey.AUDIO, WorkflowProgressStatus.SKIPPED)
        if WorkflowProgressKey.DOWNLOAD_AUDIO in step.progress:
            set_progress(step, WorkflowProgressKey.DOWNLOAD_AUDIO, WorkflowProgressStatus.SKIPPED)

    @staticmethod
    def _reset_audio_outputs(step: WorkflowStep) -> None:
        """Limpia artefactos de audio heredados del flujo pre-VEO."""
        step.audio_job_id = None
        step.audio_path = None

    async def _transition(
        self,
        step: WorkflowStep,
        on_transition: StepTransition,
        *,
        status: WorkflowStepStatus | None = None,
        progress_updates: dict[WorkflowProgressKey, WorkflowProgressStatus] | None = None,
    ) -> None:
        """Aplica mutaciones del step y notifica el cambio."""
        if status is not None:
            step.status = status
        if progress_updates:
            for key, value in progress_updates.items():
                set_progress(step, key, value)
        await on_transition(step)

    # --- scene image preparation ------------------------------------------

    async def _prepare_scene_image(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> ImageAssetRef:
        """Genera (o reusa) la imagen scene del step y la descarga local."""
        step_dir = context.step_dir(step)
        scene_path = step_dir / SCENE_IMAGE_FILENAME
        if not is_path_inside(scene_path, self._settings.outputs_dir):
            raise WorkflowStepError(f"step {step.step}: scene_image fuera de outputs_dir")
        step_dir.mkdir(parents=True, exist_ok=True)
        set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        if not needs_scene_generation(step):
            async with self._download_limiter:
                await download_kie_asset(
                    client=self._client,
                    url=context.base_image_ref.kie_url,
                    output_path=scene_path,
                )
            step.scene_image_path = str(scene_path)
            set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED)
            return context.base_image_ref
        if step.scene_image_approved_at is not None and step.bg_image_job_id:
            ref = await self._reload_scene_ref(step, scene_path)
            set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED)
            return ref
        if step.bg_image_job_id and context.requires_scene_approval(step):
            ref = await self._reload_scene_ref(step, scene_path)
            step.status = WorkflowStepStatus.AWAITING_APPROVAL
            await on_transition(step)
            raise StepAwaitingApprovalSignal(
                f"step {step.step}: scene_image previa todavía pendiente de "
                "aprobación (reanudado sin regenerar)"
            )
        ref = await self._generate_scene_image(step, context, scene_path, on_transition)
        if context.requires_scene_approval(step):
            step.status = WorkflowStepStatus.AWAITING_APPROVAL
            await on_transition(step)
            raise StepAwaitingApprovalSignal(
                f"step {step.step}: scene_image generada, esperando aprobación humana"
            )
        return ref

    async def _reload_scene_ref(self, step: WorkflowStep, scene_path: Path) -> ImageAssetRef:
        """Recupera el `ImageAssetRef` de una scene_image ya generada."""
        if not step.bg_image_job_id:
            raise WorkflowStepError(
                f"step {step.step}: resume tras aprobación pero falta bg_image_job_id"
            )
        generated = await self._generated_images_store.get(step.bg_image_job_id)
        if generated is None:
            raise WorkflowStepError(
                f"step {step.step}: scene_image aprobada (id={step.bg_image_job_id}) "
                "ya no existe en el store local; regenerá el step"
            )
        ref = ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id=generated.id,
            label=generated.label,
            kie_url=generated.kie_url,
            expires_at=generated.expires_at(KIE_GENERATED_RETENTION_DAYS),
        )
        if ref.expires_at <= datetime.now(UTC):
            raise WorkflowStepError(
                f"step {step.step}: scene_image (id={step.bg_image_job_id}) "
                f"expirada en Kie ({ref.expires_at.isoformat()}); usá Regenerar "
                "desde el modal de aprobación para crear una nueva"
            )
        if not scene_path.exists():  # noqa: ASYNC240 - check sync trivial
            async with self._download_limiter:
                await download_kie_asset(client=self._client, url=ref.kie_url, output_path=scene_path)
        step.scene_image_path = str(scene_path)
        return ref

    async def _generate_scene_image(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_path: Path,
        on_transition: StepTransition,
    ) -> ImageAssetRef:
        image_job = self._build_scene_image_job(step, context)
        await self._image_jobs_repo.upsert(image_job)
        step.bg_image_job_id = image_job.id
        await on_transition(step)
        runner = self._runner_factory.make_image_runner()
        async with self._image_limiter:
            await runner.run(image_job)
        if image_job.status != ImageJobStatus.COMPLETED or not image_job.kie_url:
            raise WorkflowStepError(
                f"step {step.step}: falló la generación de scene_image "
                f"({image_job.error or 'sin mensaje'})"
            )
        ref = await self._make_scene_ref(step, image_job)
        async with self._download_limiter:
            await download_kie_asset(client=self._client, url=ref.kie_url, output_path=scene_path)
        step.scene_image_path = str(scene_path)
        set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED)
        return ref

    def _build_scene_image_job(
        self, step: WorkflowStep, context: WorkflowExecutionContext
    ) -> ImageJob:
        refs = []
        if step.include_model:
            refs.append(ref_dict(context.base_image_ref))
        if step.include_product and context.product_image_ref is not None:
            refs.append(ref_dict(context.product_image_ref))

        settings = ImageGenerationSettings()
        effective_aspect = step.image_aspect_ratio or context.image_aspect_ratio
        if effective_aspect is not None:
            settings.aspect_ratio = effective_aspect

        return ImageJob(
            id=step.bg_image_job_id or new_image_job_id(),
            label=f"[wf]{step.scene_slug}",
            prompt=build_scene_prompt(step),
            settings_json=settings.model_dump_json(exclude_none=True),
            refs_json=json.dumps(refs, ensure_ascii=False),
            status=ImageJobStatus.QUEUED,
        )

    async def _make_scene_ref(self, step: WorkflowStep, image_job: ImageJob) -> ImageAssetRef:
        generated = await self._generated_images_store.get(image_job.id)
        if generated is None:
            raise WorkflowStepError(
                f"step {step.step}: scene_image generada no apareció en el store"
            )
        return ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id=generated.id,
            label=generated.label,
            kie_url=generated.kie_url,
            expires_at=generated.expires_at(KIE_GENERATED_RETENTION_DAYS),
        )


__all__ = [
    "AUDIO_FILENAME",
    "A_ROLL_VIDEO_FILENAME",
    "B_ROLL_VIDEO_FILENAME",
    "SCENE_IMAGE_FILENAME",
    "WorkflowStepRunner",
]
