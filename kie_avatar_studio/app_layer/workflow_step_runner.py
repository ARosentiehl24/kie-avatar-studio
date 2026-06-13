"""`WorkflowStepRunner`: ejecuta UN `WorkflowStep` end-to-end.

Maneja los 3 tipos de step con métodos separados (CR-3.1: SRP):

- `_run_a_roll`: scene_image (opcional) + audio TTS + Avatar Pro → final.mp4
  (con audio embebido) + audio.mp3 separado para post-producción.
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
construir runners ad-hoc y los wrappa con limiters dedicados. Para
Avatar Pro/i2v y descargas usa limiters separados para no mezclar
TTS, imagen y video en el mismo cuello de botella.

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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from loguru import logger

from ..config import Settings
from ..domain.errors import StepAwaitingApprovalSignal, WorkflowStepError
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
from ..domain.policies import KIE_GENERATED_RETENTION_DAYS, MAX_I2V_PROMPT_CHARS, is_path_inside
from ..domain.ports import GeneratedImageStore, ImageJobRepository, KieGateway
from .ids import new_audio_id, new_image_job_id
from .runner_factories import WorkflowRunnerFactory
from .visual_prompt_guard import append_video_visual_guard
from .workflow_execution_context import (
    WorkflowExecutionContext,
    build_scene_prompt,
    initialize_progress,
    is_b_roll_native_sound,
    is_b_roll_with_audio,
    mark_remaining_progress_failed,
    needs_scene_generation,
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


@dataclass(frozen=True, slots=True)
class _ArollRenderPlan:
    step: WorkflowStep
    context: WorkflowExecutionContext
    scene_ref: ImageAssetRef
    audio_url: str


class WorkflowStepRunner:
    """Ejecuta UN `WorkflowStep` siguiendo el path adecuado a su tipo."""

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
        except StepAwaitingApprovalSignal:
            # NO es error: el step generó la scene_image y se queda en
            # AWAITING_APPROVAL hasta que el usuario apruebe. El estado
            # ya fue seteado en `_prepare_scene_image` antes del raise.
            # NO seteamos `completed_at` (el step no terminó). NO marcamos
            # progress como FAILED. Re-raise para que el WorkflowRunner
            # detecte la pausa y propague el status al workflow.
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
        elif is_b_roll_native_sound(step):
            # voiceover=false: Kling 3.0 genera sound effects ambient embebidos
            # en el video; sin TTS aparte. El video ya viene "completo".
            await self._run_b_roll_native_sound(step, context, on_transition)
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
        await self._render_avatar_to_step(
            _ArollRenderPlan(
                step=step,
                context=context,
                scene_ref=scene_ref,
                audio_url=audio_url,
            ),
            on_transition,
        )

    async def _render_avatar_to_step(
        self,
        plan: _ArollRenderPlan,
        on_transition: StepTransition,
    ) -> None:
        step = plan.step
        output_path, audio_path = self._a_roll_output_paths(plan)
        # El step permanece en RENDERING / VIDEO=RUNNING mientras se pollea/genera el video en Kie.
        render_result, _ = await asyncio.gather(
            render_avatar_video(
                client=self._client,
                settings=self._settings,
                limiter=self._video_limiter,
                download_limiter=self._download_limiter,
                image_url=plan.scene_ref.kie_url,
                audio_url=plan.audio_url,
                prompt=append_video_visual_guard(step.prompt),
                output_path=output_path,
                existing_task_id=step.video_task_id,
            ),
            self._download_audio_only(step, plan.audio_url, audio_path),
        )
        task_id, video_path = render_result
        step.video_task_id = task_id
        step.video_path = video_path

        # Una vez generado y descargado exitosamente, hacemos la transición de progreso.
        step.status = WorkflowStepStatus.DOWNLOADING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.RUNNING)
        await on_transition(step)

        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.COMPLETED)

    def _a_roll_output_paths(self, plan: _ArollRenderPlan) -> tuple[Path, Path]:
        step_dir = plan.context.step_dir(plan.step)
        output_path = step_dir / A_ROLL_VIDEO_FILENAME
        audio_path = step_dir / AUDIO_FILENAME
        if not is_path_inside(output_path, self._settings.outputs_dir) or not is_path_inside(
            audio_path, self._settings.outputs_dir
        ):
            raise WorkflowStepError(f"step {plan.step.step}: output fuera de outputs_dir")
        step_dir.mkdir(parents=True, exist_ok=True)
        return output_path, audio_path

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
        video_path, audio_path = self._b_roll_output_paths(step, context, with_audio=True)

        # El step permanece en RENDERING / VIDEO=RUNNING mientras se pollea/genera el video en Kie.
        # Ejecutamos la generación y descarga en background.
        await asyncio.gather(
            self._render_i2v(step, context, scene_ref, video_path),
            self._download_audio_only(step, audio_url, audio_path),
        )

        # Hacemos la transición oficial a DOWNLOADING tras el renderizado exitoso.
        step.status = WorkflowStepStatus.DOWNLOADING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD_VIDEO, WorkflowProgressStatus.RUNNING)
        set_progress(step, WorkflowProgressKey.DOWNLOAD_AUDIO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)

        set_progress(step, WorkflowProgressKey.DOWNLOAD_VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD_AUDIO, WorkflowProgressStatus.COMPLETED)

    async def _render_i2v(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_ref: ImageAssetRef,
        video_path: Path,
        *,
        sound: bool = False,
    ) -> None:
        # Resolución de duración con fallback 3 niveles. Ver docstring de
        # `WorkflowExecutionContext.resolve_i2v_duration` para el orden de
        # precedencia (override del modal > step.duration_seconds > default
        # de Settings).
        duration = context.resolve_i2v_duration(
            step, default=self._settings.default_i2v_duration_seconds
        )
        # Log explícito para post-mortems: cuando un b-roll sale con
        # duración o sound inesperado, este debug evita tener que cruzar
        # manifest + entry original para inferir qué nivel del fallback ganó.
        logger.debug(
            "workflow.step.i2v duration_resolved step={} override={} "
            "step_value={} default={} -> {} sound={}",
            step.step,
            context.i2v_duration_seconds_override,
            step.duration_seconds,
            self._settings.default_i2v_duration_seconds,
            duration,
            sound,
        )
        task_id, path = await render_i2v_video(
            client=self._client,
            settings=self._settings,
            limiter=self._video_limiter,
            download_limiter=self._download_limiter,
            image_url=scene_ref.kie_url,
            prompt=append_video_visual_guard(step.prompt, max_chars=MAX_I2V_PROMPT_CHARS),
            output_path=video_path,
            duration=duration,
            sound=sound,
            existing_task_id=step.video_task_id,
        )
        step.video_task_id = task_id
        step.video_path = path

    async def _download_audio_only(
        self, step: WorkflowStep, audio_url: str, audio_path: Path
    ) -> None:
        async with self._download_limiter:
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
        await self._render_i2v_to_step(step, context, scene_ref, on_transition, sound=False)

    # --- b-roll native sound (Kling 3.0 sound effects embebidos) ---------

    async def _run_b_roll_native_sound(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> None:
        """B-roll con `voiceover=false`: Kling 3.0 genera sound effects nativos.

        Mismo flow que `_run_b_roll_silent` pero pasamos `sound=true` al i2v:
        Kling embebe sound effects ambientales en el video basados en el
        prompt. NO se llama a TTS ni se descarga audio.mp3 aparte.
        """
        step.status = WorkflowStepStatus.PREPARING
        await on_transition(step)
        scene_ref = await self._prepare_scene_image(step, context, on_transition)
        step.status = WorkflowStepStatus.RENDERING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.RUNNING)
        await on_transition(step)
        await self._render_i2v_to_step(step, context, scene_ref, on_transition, sound=True)

    async def _render_i2v_to_step(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        scene_ref: ImageAssetRef,
        on_transition: StepTransition,
        *,
        sound: bool = False,
    ) -> None:
        (output_path,) = self._b_roll_output_paths(step, context, with_audio=False)

        # El step permanece en RENDERING mientras se pollea la generación de video en Kie.
        await self._render_i2v(step, context, scene_ref, output_path, sound=sound)

        # Transicionamos de RENDERING a DOWNLOADING tras render exitoso.
        step.status = WorkflowStepStatus.DOWNLOADING
        set_progress(step, WorkflowProgressKey.VIDEO, WorkflowProgressStatus.COMPLETED)
        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.RUNNING)
        await on_transition(step)

        set_progress(step, WorkflowProgressKey.DOWNLOAD, WorkflowProgressStatus.COMPLETED)

    def _b_roll_output_paths(
        self, step: WorkflowStep, context: WorkflowExecutionContext, *, with_audio: bool
    ) -> tuple[Path, ...]:
        step_dir = context.step_dir(step)
        video_path = step_dir / B_ROLL_VIDEO_FILENAME
        paths = (video_path, step_dir / AUDIO_FILENAME) if with_audio else (video_path,)
        if any(not is_path_inside(path, self._settings.outputs_dir) for path in paths):
            raise WorkflowStepError(f"step {step.step}: output b-roll fuera de outputs_dir")
        step_dir.mkdir(parents=True, exist_ok=True)
        return paths

    # --- scene image preparation ------------------------------------------

    async def _prepare_scene_image(
        self,
        step: WorkflowStep,
        context: WorkflowExecutionContext,
        on_transition: StepTransition,
    ) -> ImageAssetRef:
        """Genera (o reusa) la imagen scene del step y la descarga local.

        Casos (la generación con Nano Banana se dispara si `change_scene` O
        `include_product` — ver `needs_scene_generation`):
        - Ni cambia escena ni incluye producto: reusa la imagen base de la
          modelo (no gasta Nano Banana).
        - Genera scene Y `scene_image_approved_at` seteado (resume tras
          aprobación humana): reusa la scene_image ya generada en
          `bg_image_job_id` (no gasta Nano Banana de nuevo).
        - Genera scene Y NO aprobado Y modo=AUTO: genera nueva imagen Nano
          Banana 2 y continúa al render (comportamiento clásico).
        - Genera scene Y NO aprobado Y modo=MANUAL (solo b-roll): genera
          nueva imagen Nano Banana 2, marca el step en AWAITING_APPROVAL y
          lanza `StepAwaitingApprovalSignal` para que el `WorkflowRunner`
          pause el workflow.
        """
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
        # Resume tras aprobación humana: ya tenemos el bg_image_job_id +
        # scene_image_path; no regeneramos.
        if step.scene_image_approved_at is not None and step.bg_image_job_id:
            ref = await self._reload_scene_ref(step, scene_path)
            set_progress(step, WorkflowProgressKey.SCENE_IMAGE, WorkflowProgressStatus.COMPLETED)
            return ref
        # Step que ya estaba esperando aprobación de un run previo (multi-step
        # MANUAL: aprobamos otro step y re-encolamos; este sigue awaiting).
        # NO debemos regenerar — ya gastamos Nano Banana en la run anterior.
        # Re-emitimos el signal para que el workflow vuelva a pausar SIN tocar
        # créditos. Es CRÍTICO: sin este branch, cada aprobación de un step
        # cuesta regenerar todos los demás pendientes (bug grave).
        if step.bg_image_job_id and context.requires_scene_approval(step):
            ref = await self._reload_scene_ref(step, scene_path)
            # CRÍTICO: el parent (`_run_b_roll_*`) ya pisó el status con
            # PREPARING antes de llamar acá. Tenemos que restaurar
            # AWAITING_APPROVAL explícitamente, sino el workflow queda
            # bricked: el header dirá AWAITING_APPROVAL pero ningún step
            # estará en ese status, y `pending_approval_step()` devolverá
            # None → el modal de aprobación no podrá encontrar el step.
            # Cubierto por test_b_roll_repause_without_regeneration_keeps_step_in_awaiting_approval.
            step.status = WorkflowStepStatus.AWAITING_APPROVAL
            await on_transition(step)
            raise StepAwaitingApprovalSignal(
                f"step {step.step}: scene_image previa todavía pendiente de "
                "aprobación (reanudado sin regenerar)"
            )
        # Generación fresca con Nano Banana 2.
        ref = await self._generate_scene_image(step, context, scene_path, on_transition)
        # Si el modo es MANUAL, pausamos acá. El step ya tiene
        # bg_image_job_id + scene_image_path persistidos; cuando el
        # usuario apruebe, este mismo método entrará por el branch de
        # resume y reusará todo.
        if context.requires_scene_approval(step):
            step.status = WorkflowStepStatus.AWAITING_APPROVAL
            await on_transition(step)
            raise StepAwaitingApprovalSignal(
                f"step {step.step}: scene_image generada, esperando aprobación humana"
            )
        return ref

    async def _reload_scene_ref(self, step: WorkflowStep, scene_path: Path) -> ImageAssetRef:
        """Recupera el `ImageAssetRef` de una scene_image ya generada.

        Usado para resume tras aprobación humana: el `bg_image_job_id`
        está persistido en el step, lo buscamos en el store y
        reconstruimos el ref. Si el `scene_path` local no existe (ej.
        el usuario borró outputs/), lo re-descargamos del `kie_url`
        (vive 14 días).
        """
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
        # Validamos expires_at ANTES de descargar para fail-fast con un error
        # tipado del dominio (el usuario aprobó hace >14 días → el kie_url
        # ya no es accesible; mejor pedirle regenerar que dar un download
        # error genérico de Kie).
        if ref.expires_at <= datetime.now(UTC):
            raise WorkflowStepError(
                f"step {step.step}: scene_image (id={step.bg_image_job_id}) "
                f"expirada en Kie ({ref.expires_at.isoformat()}); usá Regenerar "
                "desde el modal de aprobación para crear una nueva"
            )
        if not scene_path.exists():  # noqa: ASYNC240 - check sync trivial
            async with self._download_limiter:
                await download_kie_asset(
                    client=self._client, url=ref.kie_url, output_path=scene_path
                )
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
        # Refs para Nano Banana: la base (modelo) solo si include_model=True.
        # El producto global se agrega si include_product=True.
        refs = []
        if step.include_model:
            refs.append(ref_dict(context.base_image_ref))
        if step.include_product and context.product_image_ref is not None:
            refs.append(ref_dict(context.product_image_ref))

        # Usamos el aspect ratio: sobrescribe el del step si está configurado,
        # sino usa el global del workflow.
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

    def _build_audio_job(self, step: WorkflowStep, context: WorkflowExecutionContext) -> AudioJob:
        """Construye el AudioJob a procesar.

        TODO(Fase 4): cuando el step está en re-pause por MANUAL multi-step
        (scene_image_approved_at=None pero workflow re-encolado para revisar
        otro step), este método crea un AudioJob FRESH sin preservar el
        `task_id` del job persistido previamente, lo que regenera TTS en
        Kie y gasta créditos extra (O(N²) para N steps b-roll con texto).
        Fix futuro: inyectar `AudioJobRepository` al step runner y cargar
        el job persistido para preservar `task_id` cuando exista.
        """
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
        async with self._audio_limiter:
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
