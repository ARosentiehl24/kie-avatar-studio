"""`WorkflowStepRunner`: ejecuta UN `WorkflowStep` end-to-end.

Maneja los 3 tipos de step con métodos separados (CR-3.1: SRP):

- `_run_a_roll`: scene_image (opcional) + audio TTS + Avatar Pro → final.mp4
  (con audio embebido por Avatar Pro, NO se descarga audio aparte).
- `_run_b_roll_with_audio`: scene_image + audio TTS + i2v silencioso →
  video.mp4 + audio.mp3 (descargas en paralelo).
- `_run_b_roll_silent`: scene_image + i2v silencioso → video.mp4 (sin TTS).

### Contrato con el `WorkflowRunner`

El step runner NO escribe directamente a la DB ni al manifest. Recibe
un callback `on_transition(step)` que el `WorkflowRunner` provee y que
serializa (lock por workflow_id) la persistencia + manifest regen +
notificación al listener. Esto evita lost updates entre steps paralelos.

### Capacity limiter

Para los image/audio sub-jobs, usa `WorkflowRunnerFactory` para
construir runners ad-hoc y los wrappa con `async with self._limiter`.
Para Avatar Pro y i2v (llamadas Kie directas sin runner) usa los
helpers de `workflow_kie_helpers` que adquieren el limiter internamente.

### Tamaño del módulo (CR-3.2)

Este módulo está justo por encima del límite de 300 líneas (~390 hoy).
Excederlo es una decisión deliberada: los 3 paths separados son
inherentes al dominio (a-roll, b-roll-con-texto, b-roll-silencioso son
flujos diferentes, no variaciones) y partirlos en archivos distintos
fragmentaría la state machine compartida y obligaría a re-exportar 6+
funciones. Las extracciones razonables ya están hechas:
`workflow_execution_context.py` saca el contexto + helpers de progress,
`workflow_kie_helpers.py` saca los helpers Kie directos,
`runner_factories.py` saca la construcción de runners hoja. Cualquier
extracción adicional rompe la cohesión del state machine.
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
from ..domain.errors import WorkflowStepError
from ..domain.models import (
    AudioJob,
    AudioJobStatus,
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
    StepType,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from ..domain.policies import KIE_GENERATED_RETENTION_DAYS
from ..domain.ports import GeneratedImageStore, ImageJobRepository, KieGateway
from .ids import new_audio_id, new_image_job_id
from .runner_factories import WorkflowRunnerFactory
from .workflow_execution_context import (
    WorkflowExecutionContext,
    build_scene_prompt,
    initialize_progress,
    is_b_roll_with_audio,
    mark_remaining_progress_failed,
    ref_dict,
    set_progress,
)
from .workflow_kie_helpers import (
    download_kie_asset,
    render_avatar_video,
    render_i2v_video,
)

SCENE_IMAGE_FILENAME: Final[str] = "scene.png"
AUDIO_FILENAME: Final[str] = "audio.mp3"
A_ROLL_VIDEO_FILENAME: Final[str] = "final.mp4"
B_ROLL_VIDEO_FILENAME: Final[str] = "video.mp4"

StepTransition = Callable[[WorkflowStep], Awaitable[None]]


class WorkflowStepRunner:
    """Ejecuta UN `WorkflowStep` siguiendo el path adecuado a su tipo."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        capacity_limiter: asyncio.Semaphore,
        *,
        image_jobs_repo: ImageJobRepository,
        generated_images_store: GeneratedImageStore,
        runner_factory: WorkflowRunnerFactory,
    ) -> None:
        self._settings = settings
        self._client = client
        self._limiter = capacity_limiter
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
        step.started_at = datetime.now(UTC)
        try:
            await self._dispatch_path(step, context, on_transition)
            step.status = WorkflowStepStatus.COMPLETED
            step.completed_at = datetime.now(UTC)
            await on_transition(step)
        except asyncio.CancelledError:
            step.status = WorkflowStepStatus.CANCELLED
            step.error = "cancelado"
            step.completed_at = datetime.now(UTC)
            await on_transition(step)
            raise
        except Exception as exc:
            logger.exception("Step {} ({}): falló", step.step, step.scene_name)
            step.status = WorkflowStepStatus.FAILED
            step.error = str(exc) or exc.__class__.__name__
            step.completed_at = datetime.now(UTC)
            mark_remaining_progress_failed(step)
            await on_transition(step)
        return step

    async def _dispatch_path(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> None:
        if step.type == StepType.A_ROLL:
            await self._run_a_roll(step, context, on_transition)
        elif is_b_roll_with_audio(step):
            await self._run_b_roll_with_audio(step, context, on_transition)
        else:
            await self._run_b_roll_silent(step, context, on_transition)

    # --- a-roll path ------------------------------------------------------

    async def _run_a_roll(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> None:
        step.status = WorkflowStepStatus.PREPARING
        await on_transition(step)
        scene_ref, audio_url = await asyncio.gather(
            self._prepare_scene_image(step, context, on_transition),
            self._prepare_audio(step, context, on_transition),
        )
        step.status = WorkflowStepStatus.RENDERING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await self._render_avatar_to_step(step, context, scene_ref, audio_url, on_transition)

    async def _render_avatar_to_step(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_ref: ImageAssetRef,
        audio_url: str,
        on_transition: StepTransition,
    ) -> None:
        output_path = context.step_dir(step) / A_ROLL_VIDEO_FILENAME
        output_path.parent.mkdir(parents=True, exist_ok=True)
        step.status = WorkflowStepStatus.DOWNLOADING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        task_id, video_path = await render_avatar_video(
            client=self._client,
            settings=self._settings,
            limiter=self._limiter,
            image_url=scene_ref.kie_url,
            audio_url=audio_url,
            prompt=step.prompt,
            output_path=output_path,
            existing_task_id=step.video_task_id,
        )
        step.video_task_id = task_id
        step.video_path = video_path
        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.COMPLETED)

    # --- b-roll with audio (silent video + standalone audio) --------------

    async def _run_b_roll_with_audio(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> None:
        step.status = WorkflowStepStatus.PREPARING
        await on_transition(step)
        scene_ref, audio_url = await asyncio.gather(
            self._prepare_scene_image(step, context, on_transition),
            self._prepare_audio(step, context, on_transition),
        )
        step.status = WorkflowStepStatus.RENDERING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await self._render_i2v_and_download_audio(
            step, context, scene_ref, audio_url, on_transition
        )

    async def _render_i2v_and_download_audio(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_ref: ImageAssetRef,
        audio_url: str,
        on_transition: StepTransition,
    ) -> None:
        step_dir = context.step_dir(step)
        step_dir.mkdir(parents=True, exist_ok=True)
        video_path = step_dir / B_ROLL_VIDEO_FILENAME
        audio_path = step_dir / AUDIO_FILENAME
        step.status = WorkflowStepStatus.DOWNLOADING
        set_progress(step, WorkflowProgressKey.DOWNLOAD_VIDEO, WorkflowProgressStatus.RUNNING)
        set_progress(step, WorkflowProgressKey.DOWNLOAD_AUDIO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await asyncio.gather(
            self._render_i2v(step, scene_ref, video_path),
            self._download_audio_only(step, audio_url, audio_path),
        )
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD_VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD_AUDIO, WorkflowProgressStatus.COMPLETED)

    async def _render_i2v(
        self,
        step: WorkflowStep,
        scene_ref: ImageAssetRef,
        video_path: Path,
    ) -> None:
        task_id, path = await render_i2v_video(
            client=self._client,
            settings=self._settings,
            limiter=self._limiter,
            image_url=scene_ref.kie_url,
            prompt=step.prompt,
            output_path=video_path,
            duration=self._settings.default_i2v_duration_seconds,
            existing_task_id=step.video_task_id,
        )
        step.video_task_id = task_id
        step.video_path = path

    async def _download_audio_only(
        self, step: WorkflowStep, audio_url: str, audio_path: Path
    ) -> None:
        await download_kie_asset(client=self._client, url=audio_url, output_path=audio_path)
        step.audio_path = str(audio_path)

    # --- b-roll silent (only video) ---------------------------------------

    async def _run_b_roll_silent(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> None:
        step.status = WorkflowStepStatus.PREPARING
        await on_transition(step)
        scene_ref = await self._prepare_scene_image(step, context, on_transition)
        step.status = WorkflowStepStatus.RENDERING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await self._render_i2v_to_step(step, context, scene_ref, on_transition)

    async def _render_i2v_to_step(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_ref: ImageAssetRef,
        on_transition: StepTransition,
    ) -> None:
        step_dir = context.step_dir(step)
        step_dir.mkdir(parents=True, exist_ok=True)
        output_path = step_dir / B_ROLL_VIDEO_FILENAME
        step.status = WorkflowStepStatus.DOWNLOADING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await self._render_i2v(step, scene_ref, output_path)
        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.COMPLETED)

    # --- scene image preparation ------------------------------------------

    async def _prepare_scene_image(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> ImageAssetRef:
        """Genera (o reusa) la imagen scene del step y la descarga local.

        - Si `change_background=False`: reusa la imagen base de la modelo.
        - Si `change_background=True`: genera nueva imagen Nano Banana 2
          con refs=[base] y prompt=`background_description + step.prompt`.
        """
        step_dir = context.step_dir(step)
        step_dir.mkdir(parents=True, exist_ok=True)
        scene_path = step_dir / SCENE_IMAGE_FILENAME
        set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        if not step.change_background:
            await download_kie_asset(
                client=self._client,
                url=context.base_image_ref.kie_url,
                output_path=scene_path,
            )
            step.scene_image_path = str(scene_path)
            set_progress(
                step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED
            )
            return context.base_image_ref
        return await self._generate_scene_image(step, context, scene_path, on_transition)

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
        async with self._limiter:
            await runner.run(image_job)
        if image_job.status != ImageJobStatus.COMPLETED or not image_job.kie_url:
            raise WorkflowStepError(
                f"step {step.step}: falló la generación de scene_image "
                f"({image_job.error or 'sin mensaje'})"
            )
        ref = await self._make_scene_ref(step, image_job)
        await download_kie_asset(client=self._client, url=ref.kie_url, output_path=scene_path)
        step.scene_image_path = str(scene_path)
        set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED)
        return ref

    def _build_scene_image_job(
        self, step: WorkflowStep, context: WorkflowExecutionContext
    ) -> ImageJob:
        return ImageJob(
            id=step.bg_image_job_id or new_image_job_id(),
            label=f"[wf]{step.scene_slug}",
            prompt=build_scene_prompt(step),
            settings_json=ImageGenerationSettings().model_dump_json(exclude_none=True),
            refs_json=json.dumps([ref_dict(context.base_image_ref)], ensure_ascii=False),
            status=ImageJobStatus.QUEUED,
        )

    async def _make_scene_ref(
        self, step: WorkflowStep, image_job: ImageJob
    ) -> ImageAssetRef:
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

    def _build_audio_job(
        self, step: WorkflowStep, context: WorkflowExecutionContext
    ) -> AudioJob:
        voice_settings = context.resolved_voice_settings()
        settings_json: str | None = None
        if voice_settings is not None and not voice_settings.is_empty():
            settings_json = voice_settings.model_dump_json(exclude_none=True)
        return AudioJob(
            id=step.audio_job_id or new_audio_id(),
            label=f"[wf]{step.scene_slug}",
            script=step.text,
            voice_id=context.voice_id,
            voice_settings_json=settings_json,
            status=AudioJobStatus.QUEUED,
        )

    # --- audio preparation ------------------------------------------------

    async def _prepare_audio(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> str:
        """Genera el audio TTS del step. Devuelve `kie_url` para uso del Avatar."""
        if not step.text.strip():
            raise WorkflowStepError(
                f"step {step.step}: _prepare_audio invocado sin text — bug del orquestador"
            )
        audio_job = self._build_audio_job(step, context)
        step.audio_job_id = audio_job.id
        set_progress(step, WorkflowProgressKey.AUDIO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        runner = self._runner_factory.make_audio_runner(tts_model=context.tts_model)
        async with self._limiter:
            await runner.run(audio_job)
        if audio_job.status != AudioJobStatus.COMPLETED or not audio_job.kie_url:
            raise WorkflowStepError(
                f"step {step.step}: falló la generación de audio "
                f"({audio_job.error or 'sin mensaje'})"
            )
        set_progress(step, WorkflowProgressKey.AUDIO, WorkflowProgressStatus.COMPLETED)
        return audio_job.kie_url


__all__ = [
    "AUDIO_FILENAME",
    "A_ROLL_VIDEO_FILENAME",
    "B_ROLL_VIDEO_FILENAME",
    "SCENE_IMAGE_FILENAME",
    "WorkflowStepRunner",
]
