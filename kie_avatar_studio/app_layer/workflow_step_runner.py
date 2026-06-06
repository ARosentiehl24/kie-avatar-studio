"""`WorkflowStepRunner`: ejecuta UN `WorkflowStep` end-to-end.

Maneja los 3 tipos de step con métodos separados (CR-3.1: SRP):

- `_run_a_roll`: scene_image (opcional) + audio TTS + Avatar Pro → final.mp4
  (con audio embebido por Avatar Pro, NO se descarga audio aparte).
- `_run_b_roll_with_audio`: scene_image + audio TTS + i2v silencioso →
  video.mp4 + audio.mp3 (descargas en paralelo).
- `_run_b_roll_silent`: scene_image + i2v silencioso → video.mp4 (sin TTS).

Cada path comparte helpers para preparar imagen / audio / descargar.

### Contrato con el `WorkflowRunner`

El step runner NO escribe directamente a la DB ni al manifest. Recibe
un callback `on_transition(step)` que el `WorkflowRunner` provee y que
serializa (lock por workflow_id) la persistencia + manifest regen +
notificación al listener. Esto evita lost updates entre steps paralelos.

### Capacity limiter

Para los image/audio sub-jobs, usa `CapacityLimitedExecutor` (que
adquiere el limiter global antes de delegar al runner).
Para Avatar Pro y i2v (llamadas Kie directas sin runner), usa el
limiter manualmente en los helpers de `workflow_kie_helpers`.
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
    VoiceSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from ..domain.policies import (
    KIE_GENERATED_RETENTION_DAYS,
    expected_progress_keys_for_step,
)
from ..domain.ports import (
    AudioJobRepository,
    AudioStore,
    GeneratedImageStore,
    ImageJobRepository,
    ImageStore,
    KieGateway,
)
from .audio_job_runner import AudioJobRunner
from .ids import new_audio_id, new_image_job_id
from .image_job_runner import ImageJobRunner
from .workflow_kie_helpers import (
    download_kie_asset,
    render_avatar_video,
    render_i2v_video,
)

DEFAULT_TURBO_MODEL: Final[str] = "elevenlabs/text-to-speech-turbo-v2-5"
SCENE_IMAGE_FILENAME: Final[str] = "scene.png"
AUDIO_FILENAME: Final[str] = "audio.mp3"
A_ROLL_VIDEO_FILENAME: Final[str] = "final.mp4"
B_ROLL_VIDEO_FILENAME: Final[str] = "video.mp4"
DEFAULT_I2V_DURATION: Final[int] = 5

StepTransition = Callable[[WorkflowStep], Awaitable[None]]


class WorkflowExecutionContext:
    """Contexto compartido por todos los steps de UN workflow ejecutándose.

    Centraliza referencias inmutables durante la ejecución (audio_language,
    voice settings resueltos, imagen base) para que el step runner no
    tenga que volver a resolverlos por step.
    """

    def __init__(
        self,
        *,
        audio_language: str | None,
        voice_id: str,
        voice_settings: VoiceSettings | None,
        base_image_ref: ImageAssetRef,
        output_dir: Path,
    ) -> None:
        self.audio_language = audio_language
        self.voice_id = voice_id
        self.voice_settings = voice_settings
        self.base_image_ref = base_image_ref
        self.output_dir = output_dir

    @property
    def tts_model(self) -> str | None:
        """Devuelve el modelo TTS apropiado para esta ejecución.

        Si `audio_language` no es `None`, fuerza turbo (acepta `language_code`).
        Si es `None`, deja `None` para que `KieClient` use el multilingual
        default (que NO acepta `language_code` y respondería 422).
        """
        return DEFAULT_TURBO_MODEL if self.audio_language else None

    def step_dir(self, step: WorkflowStep) -> Path:
        """`output_dir / step_NN_<slug>/` para un step dado."""
        folder = f"step_{step.step:02d}_{step.scene_slug}"
        return self.output_dir / folder

    def resolved_voice_settings(self) -> VoiceSettings | None:
        """Devuelve voice_settings con `language_code` ajustado al `audio_language`."""
        if self.audio_language is None:
            return self.voice_settings
        # Si ya tiene language_code, lo respetamos; si no, lo seteamos.
        base = self.voice_settings or VoiceSettings()
        if base.language_code:
            return base
        return base.model_copy(update={"language_code": self.audio_language})


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
        uploaded_images_store: ImageStore,
        audio_jobs_repo: AudioJobRepository,
        audios_store: AudioStore,
    ) -> None:
        self._settings = settings
        self._client = client
        self._limiter = capacity_limiter
        self._image_jobs_repo = image_jobs_repo
        self._generated_images_store = generated_images_store
        self._uploaded_images_store = uploaded_images_store
        self._audio_jobs_repo = audio_jobs_repo
        self._audios_store = audios_store

    async def run(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> WorkflowStep:
        """Ejecuta el step y devuelve el resultado.

        Cada transición pasa por `on_transition` que el caller usa para
        persistir + regenerar el manifest. Si el step falla, se marca
        FAILED y se devuelve sin re-levantar.
        """
        _initialize_progress(step)
        step.started_at = datetime.now(UTC)
        try:
            if step.type == StepType.A_ROLL:
                await self._run_a_roll(step, context, on_transition)
            elif step.text:
                await self._run_b_roll_with_audio(step, context, on_transition)
            else:
                await self._run_b_roll_silent(step, context, on_transition)
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
            _mark_remaining_progress_failed(step)
            await on_transition(step)
        return step

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
        _set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.RUNNING)
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
        _set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        _set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.RUNNING)
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
        _set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.COMPLETED)

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
        _set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await self._render_i2v_and_download_audio(step, context, scene_ref, audio_url, on_transition)

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

        async def _render() -> None:
            task_id, path = await render_i2v_video(
                client=self._client,
                settings=self._settings,
                limiter=self._limiter,
                image_url=scene_ref.kie_url,
                prompt=step.prompt,
                output_path=video_path,
                duration=DEFAULT_I2V_DURATION,
                existing_task_id=step.video_task_id,
            )
            step.video_task_id = task_id
            step.video_path = path

        async def _download_audio() -> None:
            await download_kie_asset(client=self._client, url=audio_url, output_path=audio_path)
            step.audio_path = str(audio_path)

        step.status = WorkflowStepStatus.DOWNLOADING
        _set_progress(step, WorkflowProgressKey.DOWNLOAD_VIDEO, WorkflowProgressStatus.RUNNING)
        _set_progress(step, WorkflowProgressKey.DOWNLOAD_AUDIO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await asyncio.gather(_render(), _download_audio())
        _set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        _set_progress(step, WorkflowProgressKey.DOWNLOAD_VIDEO, WorkflowProgressStatus.COMPLETED)
        _set_progress(step, WorkflowProgressKey.DOWNLOAD_AUDIO, WorkflowProgressStatus.COMPLETED)

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
        _set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.RUNNING)
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
        _set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        _set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        task_id, video_path = await render_i2v_video(
            client=self._client,
            settings=self._settings,
            limiter=self._limiter,
            image_url=scene_ref.kie_url,
            prompt=step.prompt,
            output_path=output_path,
            duration=DEFAULT_I2V_DURATION,
            existing_task_id=step.video_task_id,
        )
        step.video_task_id = task_id
        step.video_path = video_path
        _set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.COMPLETED)

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
        _set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        if not step.change_background:
            await download_kie_asset(
                client=self._client,
                url=context.base_image_ref.kie_url,
                output_path=scene_path,
            )
            step.scene_image_path = str(scene_path)
            _set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED)
            return context.base_image_ref
        return await self._generate_scene_image(step, context, scene_path, on_transition)

    async def _generate_scene_image(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_path: Path,
        on_transition: StepTransition,
    ) -> ImageAssetRef:
        prompt = _build_scene_prompt(step)
        image_job = ImageJob(
            id=step.bg_image_job_id or new_image_job_id(),
            label=f"[wf]{step.scene_slug}",
            prompt=prompt,
            settings_json=ImageGenerationSettings().model_dump_json(exclude_none=True),
            refs_json=json.dumps(
                [_ref_dict(context.base_image_ref)],
                ensure_ascii=False,
            ),
            status=ImageJobStatus.QUEUED,
        )
        await self._image_jobs_repo.upsert(image_job)
        step.bg_image_job_id = image_job.id
        await on_transition(step)
        runner = self._build_image_runner()
        async with self._limiter:
            await runner.run(image_job)
        if image_job.status != ImageJobStatus.COMPLETED or not image_job.kie_url:
            raise WorkflowStepError(
                f"step {step.step}: falló la generación de scene_image "
                f"({image_job.error or 'sin mensaje'})"
            )
        generated = await self._generated_images_store.get(image_job.id)
        if generated is None:
            raise WorkflowStepError(
                f"step {step.step}: scene_image generada no apareció en el store"
            )
        ref = ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id=generated.id,
            label=generated.label,
            kie_url=generated.kie_url,
            expires_at=generated.expires_at(KIE_GENERATED_RETENTION_DAYS),
        )
        await download_kie_asset(client=self._client, url=ref.kie_url, output_path=scene_path)
        step.scene_image_path = str(scene_path)
        _set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED)
        return ref

    def _build_image_runner(self) -> ImageJobRunner:
        return ImageJobRunner(
            self._settings,
            self._client,
            self._image_jobs_repo,
            self._generated_images_store,
            self._uploaded_images_store,
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
        voice_settings = context.resolved_voice_settings()
        settings_json: str | None = None
        if voice_settings is not None and not voice_settings.is_empty():
            settings_json = voice_settings.model_dump_json(exclude_none=True)
        audio_job = AudioJob(
            id=step.audio_job_id or new_audio_id(),
            label=f"[wf]{step.scene_slug}",
            script=step.text,
            voice_id=context.voice_id,
            voice_settings_json=settings_json,
            status=AudioJobStatus.QUEUED,
        )
        await self._audio_jobs_repo.upsert(audio_job)
        step.audio_job_id = audio_job.id
        _set_progress(step, WorkflowProgressKey.AUDIO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        runner = self._build_audio_runner(context)
        async with self._limiter:
            await runner.run(audio_job)
        if audio_job.status != AudioJobStatus.COMPLETED or not audio_job.kie_url:
            raise WorkflowStepError(
                f"step {step.step}: falló la generación de audio "
                f"({audio_job.error or 'sin mensaje'})"
            )
        _set_progress(step, WorkflowProgressKey.AUDIO, WorkflowProgressStatus.COMPLETED)
        return audio_job.kie_url

    def _build_audio_runner(self, context: WorkflowExecutionContext) -> AudioJobRunner:
        return AudioJobRunner(
            self._settings,
            self._client,
            self._audio_jobs_repo,
            self._audios_store,
            tts_model=context.tts_model,
        )

    # --- helpers ----------------------------------------------------------


# --- module-level helpers (no state) --------------------------------------


def _initialize_progress(step: WorkflowStep) -> None:
    """Rellena `step.progress` con todas las keys esperadas a PENDING."""
    expected = expected_progress_keys_for_step(step)
    for key in expected:
        if key not in step.progress:
            step.progress[key] = WorkflowProgressStatus.PENDING


def _set_progress(
    step: WorkflowStep, key: WorkflowProgressKey, status: WorkflowProgressStatus
) -> None:
    """Actualiza una key del progress. Crea la entry si no existía."""
    step.progress[key] = status


def _mark_remaining_progress_failed(step: WorkflowStep) -> None:
    """Marca como FAILED cualquier key que quedó RUNNING/PENDING al fallar."""
    not_terminal = (WorkflowProgressStatus.PENDING, WorkflowProgressStatus.RUNNING)
    for key, value in list(step.progress.items()):
        if value in not_terminal:
            step.progress[key] = WorkflowProgressStatus.FAILED


def _build_scene_prompt(step: WorkflowStep) -> str:
    """Concatena background_description + prompt para Nano Banana refit."""
    parts = []
    if step.background_description.strip():
        parts.append(step.background_description.strip())
    parts.append(step.prompt.strip())
    return ". ".join(parts)


def _ref_dict(ref: ImageAssetRef) -> dict[str, object]:
    """Serializa el ref para `image_jobs.refs_json` (con mode='json' para datetimes)."""
    return ref.model_dump(mode="json")


__all__ = [
    "AUDIO_FILENAME",
    "A_ROLL_VIDEO_FILENAME",
    "B_ROLL_VIDEO_FILENAME",
    "DEFAULT_TURBO_MODEL",
    "SCENE_IMAGE_FILENAME",
    "WorkflowExecutionContext",
    "WorkflowStepRunner",
]
