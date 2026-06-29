"""`WorkflowController`: casos de uso de la pantalla Automatización.

Capa de aplicación que orquesta:
- Listar workflows del filesystem (`workflows/*.json`) merge con DB.
- Validar archivos locales y shape final antes de encolar.
- Encolar workflows en el `workflow_queue` (cola con su propio limiter
  `_workflows_limiter` distinto del global de Kie).
- Cancelar / reintentar / borrar.
- Suscribir listeners a los eventos.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from loguru import logger

from ..config import Settings
from ..domain.errors import (
    ImageValidationError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from ..domain.events import WorkflowJobUpdated
from ..domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ModelCreationMethod,
    ProductImage,
    SceneApprovalMode,
    VoiceChangerSettings,
    WorkflowEntry,
    WorkflowJob,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from ..domain.policies import (
    KIE_GENERATED_RETENTION_DAYS,
    KIE_UPLOAD_RETENTION_HOURS,
    is_path_inside,
    validate_image_path,
    validate_workflow_step,
)
from ..domain.ports import (
    GeneratedImageStore,
    ImageStore,
    WorkflowManifestWriter,
    WorkflowRepository,
)
from ..domain.workflow_artifacts import (
    LEGACY_FINAL_AUDIO_FILENAME,
    LEGACY_FINAL_VIDEO_FILENAME,
    LEGACY_VOICE_CHANGED_AUDIO_FILENAME,
    workflow_final_audio_candidates,
    workflow_final_audio_filename,
    workflow_final_video_candidates,
    workflow_final_video_filename,
    workflow_voice_changed_audio_filename,
)
from .ids import sanitize_filename
from .queue_manager import QueueManager
from .workflow_base_resolver import WorkflowBaseResolver

WorkflowScanLoader = Callable[[], Awaitable[list[WorkflowEntry]]]
WorkflowEntryBuilder = Callable[..., WorkflowJob]

WorkflowEventListener = (
    Callable[[WorkflowJobUpdated], None] | Callable[[WorkflowJobUpdated], Awaitable[None]]
)

_WORKFLOW_ID_TS_FMT: Final[str] = "%Y%m%d_%H%M%S"
_WORKFLOW_ID_SHORT_LEN: Final[int] = 6
_PREVIEWS_SUBDIR: Final[str] = "_previews"
# Con `_%f` (microsegundos) para evitar colisión cuando el usuario
# regenera varias veces seguidas dentro del mismo segundo.
_PREVIEW_TS_FMT: Final[str] = "%Y%m%d_%H%M%S_%f"
_PREVIEW_FILENAME_FMT: Final[str] = "base_{ts}.{ext}"
_RECREATABLE_WORKFLOW_STATUSES: Final[frozenset[WorkflowStatus]] = frozenset(
    {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.PARTIALLY_FAILED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    }
)
_LEGACY_FINAL_OUTPUT_FILENAMES: Final[tuple[str, ...]] = (
    LEGACY_FINAL_VIDEO_FILENAME,
    LEGACY_FINAL_AUDIO_FILENAME,
    LEGACY_VOICE_CHANGED_AUDIO_FILENAME,
)


class WorkflowController:
    """Casos de uso de la pantalla Automatización."""

    def __init__(
        self,
        settings: Settings,
        repository: WorkflowRepository,
        manifest_writer: WorkflowManifestWriter,
        queue: QueueManager[WorkflowJob, WorkflowJobUpdated],
        base_resolver: WorkflowBaseResolver,
        *,
        scan_loader: WorkflowScanLoader,
        entry_builder: WorkflowEntryBuilder,
        uploaded_images: ImageStore,
        generated_images: GeneratedImageStore,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._manifest_writer = manifest_writer
        self._queue = queue
        self._base_resolver = base_resolver
        self._scan_loader = scan_loader
        self._entry_builder = entry_builder
        self._uploaded_images = uploaded_images
        self._generated_images = generated_images
        self._entries_cache: list[WorkflowEntry] | None = None

    # --- listing ----------------------------------------------------------

    async def list_entries(self, *, refresh: bool = False) -> list[WorkflowEntry]:
        """Lista los archivos del filesystem (cache). Refresh re-escanea."""
        if refresh or self._entries_cache is None:
            self._entries_cache = await self._scan_loader()
        return list(self._entries_cache)

    async def list_workflows(self, limit: int = 50) -> list[WorkflowJob]:
        """Lista los workflows persistidos en la DB (recientes primero)."""
        return await self._repository.list_recent(limit)

    async def get_workflow(self, workflow_id: str) -> WorkflowJob | None:
        return await self._repository.get(workflow_id)

    # --- enqueue ----------------------------------------------------------

    async def enqueue_entry(
        self,
        entry: WorkflowEntry,
        *,
        audio_language: str | None = None,
        resolved_base_ref: ImageAssetRef | None = None,
        local_path: str | None = None,
        i2v_duration_override: int | None = None,
        scene_approval_mode: SceneApprovalMode | None = None,
        product_ref: ImageAssetRef | None = None,
        product_local_path: str | None = None,
        voice_changer: VoiceChangerSettings | None = None,
        set_voice_changer: bool = False,
    ) -> WorkflowJob:
        """Encola un workflow validando archivos locales.

        Si `audio_language` se pasa, sobreescribe el del JSON.

        Si `resolved_base_ref` se pasa, la UI ya resolvió la imagen base
        (preview aprobado para method=prompt, foto subida para
        method=local) y el runner la reusa sin volver a generar/subir
        (evita gastar créditos dos veces). `local_path` se persiste en
        `pre_settings.model_creation.local_path` para que la UI/manifest
        muestren la ruta que el usuario eligió y para que un retry con
        ref expirado pueda re-subir el mismo archivo (CR-6.1).

        `i2v_duration_override` fuerza esa duración en TODOS los b-roll
        del workflow (sobreescribe `step.duration_seconds` del JSON). Si
        es `None`, cada step usa su propio valor o el default global.

        `scene_approval_mode` override del modo de aprobación de
        scene_image. Si es `None`, se respeta lo que diga el JSON
        (default AUTO). Si el usuario lo selecciona desde el modal
        Configurar, se aplica acá.

        `product_ref` / `product_local_path`: cuando el workflow promociona
        un producto (`promote_product=true`), la UI elige la foto, la sube
        a Kie y pasa la ref + el path acá; se persisten en
        `pre_settings.product_image` para que el runner componga el producto
        en los steps con `include_product=true`.

        `voice_changer`: snapshot completo del selector del modal
        Configurar. Si `set_voice_changer=True`, este valor reemplaza el
        `pre_settings.voice_changer` del JSON (incluyendo `None` para
        desactivar la conversión).
        """
        if not entry.valid:
            raise WorkflowValidationError(
                f"entry '{entry.name}' no es válido: {'; '.join(entry.errors)}"
            )
        workflow_id = self._new_workflow_id()
        output_dir = self._build_output_dir(workflow_id)
        workflow = self._entry_builder(entry, workflow_id=workflow_id, output_dir=output_dir)
        if audio_language is not None:
            workflow.pre_settings.audio_language = audio_language
        if resolved_base_ref is not None:
            workflow.pre_settings.model_creation.resolved_image_ref = resolved_base_ref
        if local_path is not None:
            workflow.pre_settings.model_creation.local_path = local_path
        if i2v_duration_override is not None:
            workflow.pre_settings.i2v_duration_seconds = i2v_duration_override
        if scene_approval_mode is not None:
            workflow.pre_settings.scene_approval_mode = scene_approval_mode
        elif not _payload_has_scene_approval_mode(entry.workflow_payload):
            workflow.pre_settings.scene_approval_mode = SceneApprovalMode(
                self._settings.default_scene_approval_mode
            )
        if set_voice_changer:
            workflow.pre_settings.voice_changer = (
                voice_changer.model_copy(deep=True) if voice_changer is not None else None
            )
        self._apply_product_selection(workflow, product_ref, product_local_path)
        # Si la base ya fue resuelta por la UI, el runner la reusará sin
        # tocar el path local: skip de la revalidación del path.
        if resolved_base_ref is None:
            await self._validate_local_model_path(workflow)
        await self._persist_and_enqueue(workflow)
        return workflow

    @staticmethod
    def _apply_product_selection(
        workflow: WorkflowJob,
        product_ref: ImageAssetRef | None,
        product_local_path: str | None,
    ) -> None:
        """Persiste el producto resuelto en `pre_settings.product_image`.

        No hace nada si no hay ni ref ni path (el workflow no promociona
        producto, o la UI todavía no lo resolvió). Crea el `ProductImage`
        si no existía.
        """
        if product_ref is None and product_local_path is None:
            return
        product = workflow.pre_settings.product_image or ProductImage()
        if product_ref is not None:
            product.resolved_image_ref = product_ref
        if product_local_path is not None:
            product.local_path = product_local_path
        workflow.pre_settings.product_image = product

    # --- pre-enqueue base resolution (UI flow) ----------------------------

    async def preview_base_from_prompt(
        self,
        prompt: str,
        *,
        label_hint: str,
        settings: ImageGenerationSettings | None = None,
    ) -> tuple[ImageAssetRef, Path]:
        """Genera la imagen base con GPT Image 2 y la descarga local para previsualizarla.

        Devuelve `(ref, local_path)`. La UI muestra `local_path` y permite
        al usuario aprobar/regenerar antes de encolar el workflow real.
        Crea siempre un path nuevo bajo `outputs/_previews/<timestamp>.png`
        para que el usuario pueda regenerar y comparar.

        `settings` permite override de `aspect_ratio` / `resolution` /
        `output_format`. Si es `None`, usa defaults de generación base
        (auto / 1K / jpg) y fuerza modelo GPT Image 2.
        """
        preview_dir = self._settings.outputs_dir / _PREVIEWS_SUBDIR
        timestamp = datetime.now(UTC).strftime(_PREVIEW_TS_FMT)
        effective = settings or ImageGenerationSettings()
        local_path = preview_dir / _PREVIEW_FILENAME_FMT.format(
            ts=timestamp, ext=effective.output_format
        )
        ref = await self._base_resolver.generate_from_prompt_standalone(
            prompt,
            label_hint=label_hint,
            download_to=local_path,
            settings=effective,
        )
        return ref, local_path

    async def upload_local_base(self, path: Path) -> ImageAssetRef:
        """Sube una imagen local a Kie (caso method=local) y devuelve la ref."""
        return await self._base_resolver.upload_local_standalone(path)

    async def upload_local_product(self, path: Path) -> ImageAssetRef:
        """Sube la imagen del producto promocional a Kie y devuelve la ref.

        Mismo mecanismo que `upload_local_base` (TTL 24h en Kie); se separa
        por claridad semántica del flujo de selección de producto.
        """
        return await self._base_resolver.upload_local_standalone(path)

    # --- path validators --------------------------------------------------

    async def _validate_local_model_path(self, workflow: WorkflowJob) -> None:
        creation = workflow.pre_settings.model_creation
        if creation.method != ModelCreationMethod.LOCAL:
            return
        if not creation.local_path:
            raise WorkflowValidationError("model_creation.method='local' requiere local_path")
        validate_image_path(Path(creation.local_path))

    async def _persist_and_enqueue(self, workflow: WorkflowJob) -> None:
        await self._repository.upsert_workflow(workflow)
        await self._manifest_writer.write(workflow)
        self._queue.enqueue(workflow)
        logger.info(
            "WorkflowJob '{}' encolado (id={}, {} steps)",
            workflow.name,
            workflow.id,
            len(workflow.steps),
        )

    # --- subscribe / cancel / retry / delete -----------------------------

    def subscribe(self, callback: WorkflowEventListener) -> Callable[[], None]:
        return self._queue.add_listener(callback)

    async def cancel(self, workflow_id: str) -> bool:
        return await self._queue.cancel(workflow_id)

    async def retry(self, workflow_id: str) -> bool:
        workflow = await self._repository.get(workflow_id)
        if workflow is None:
            return False
        if workflow.status == WorkflowStatus.COMPLETED and await self._needs_postprocess_repair(
            workflow
        ):
            workflow.status = WorkflowStatus.QUEUED
            workflow.error = None
            await self._repository.update_workflow_header(workflow)
            await self._manifest_writer.write(workflow)
            self._queue.enqueue(workflow)
            logger.info(
                "Workflow {} reencolado para reprocesar artefactos finales faltantes",
                workflow.id,
            )
            return True
        return await self._queue.retry(workflow)

    async def recreate_step(self, workflow_id: str, step_number: int) -> WorkflowJob:
        """Recrea el render de video de un step terminal y reencola el workflow.

        Uso esperado: un workflow ya terminó, pero un clip salió con bug visual.
        Se conserva la `scene_image` ya aprobada/generada para no gastar otra
        imagen; se descarta la tarea VEO/video del step y los finales
        (`final.mp4`, `final_audio.mp3`, `voice_changed_audio.mp3`) para que el
        postproceso los reconstruya con el nuevo clip.
        """
        workflow = await self._repository.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"workflow {workflow_id!r} no existe")
        if workflow.status not in _RECREATABLE_WORKFLOW_STATUSES:
            raise WorkflowValidationError(
                "solo se puede recrear un step cuando el workflow está en estado terminal"
            )
        step = self._find_step(workflow, step_number)
        if step is None:
            raise WorkflowValidationError(f"workflow {workflow_id!r} no tiene step {step_number}")
        if step.status != WorkflowStepStatus.COMPLETED:
            raise WorkflowValidationError(
                f"solo se puede recrear un step completed (actual: {step.status.value})"
            )
        if step.video_task_id is None and step.video_path is None:
            raise WorkflowValidationError(f"step {step_number} no tiene video generado")

        await self._delete_step_video_if_safe(workflow, step)
        await self._delete_final_outputs_if_safe(workflow)

        self._reset_step_render_state(step)

        workflow.status = WorkflowStatus.QUEUED
        workflow.error = None
        workflow.manifest_write_failed = False
        await self._persist_workflow_and_step(workflow, step)
        self._queue.enqueue(workflow)
        logger.info(
            "Workflow {} step {}: render descartado, recreando step",
            workflow.id,
            step_number,
        )
        return workflow

    async def edit_step(
        self,
        workflow_id: str,
        step_number: int,
        *,
        scene_name: str,
        scene_description: str,
        prompt: str,
        product_prompt: str | None = None,
        text: str | None = None,
    ) -> WorkflowJob:
        """Edita textos de un step terminal y lo deja listo para reintento.

        No reencola automáticamente: guardar cambios no debe gastar créditos por
        sorpresa. Se descarta el render/finales previos para que el siguiente
        retry use los textos nuevos, conservando la `scene_image` existente.
        """
        workflow, step = await self._load_editable_step(workflow_id, step_number)
        self._apply_step_text_updates(
            step,
            scene_name=scene_name,
            scene_description=scene_description,
            prompt=prompt,
            product_prompt=product_prompt,
            text=text,
        )
        await self._prepare_step_retry_after_edit(workflow, step, step_number)
        await self._persist_workflow_and_step(workflow, step)
        self._queue.notify_external(workflow)
        logger.bind(job_id=workflow.id, step_number=step_number).info(
            "Workflow step editado; listo para reintento"
        )
        return workflow

    async def ensure_product_ready_for_retry(self, workflow_id: str) -> bool:
        """Asegura que el producto siga reutilizable antes de reintentar.

        Devuelve:
        - `True` si no hace falta producto, si la ref sigue vigente, o si
          pudo recargarla automáticamente desde `product.local_path`.
        - `False` si necesita intervención del usuario para volver a elegir
          el archivo del producto.
        """
        workflow = await self._repository.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"workflow {workflow_id!r} no existe")
        if not await self._workflow_requires_product_reload(workflow):
            return True
        product = workflow.pre_settings.product_image
        if product is None or not product.local_path:
            return False
        try:
            local_path = Path(product.local_path)
            validate_image_path(local_path)
        except ImageValidationError:
            return False
        product_ref = await self.upload_local_product(local_path)
        self._apply_product_selection(workflow, product_ref, str(local_path))
        await self._repository.upsert_workflow(workflow)
        await self._manifest_writer.write(workflow)
        logger.info(
            "Workflow {}: producto recargado automáticamente para retry",
            workflow.id,
        )
        return True

    async def replace_workflow_product(self, workflow_id: str, product_path: Path) -> WorkflowJob:
        """Reemplaza el producto de un workflow existente y lo persiste en DB."""
        workflow = await self._repository.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"workflow {workflow_id!r} no existe")
        if not workflow.pre_settings.promote_product:
            raise WorkflowValidationError(f"workflow {workflow_id!r} no usa promote_product=true")
        product_ref = await self.upload_local_product(product_path)
        self._apply_product_selection(workflow, product_ref, str(product_path))
        await self._repository.upsert_workflow(workflow)
        await self._manifest_writer.write(workflow)
        return workflow

    # --- approval flow (SceneApprovalMode.MANUAL) -------------------------

    async def approve_scene(self, workflow_id: str, step_number: int) -> WorkflowJob:
        """Aprueba la scene_image generada de un step y re-encola el workflow.

        Marca `step.scene_image_approved_at = now()` y vuelve a poner el
        step en QUEUED (el step runner re-ejecuta, detecta el timestamp
        y reusa el `bg_image_job_id` sin gastar otra Nano Banana). El
        workflow vuelve a QUEUED y entra al queue normalmente.

        Lanza `WorkflowNotFoundError` si el workflow no existe.
        Lanza `WorkflowValidationError` si el step no está en
        AWAITING_APPROVAL (idempotencia + protección contra clicks
        accidentales).
        """
        workflow = await self._load_step_for_approval(workflow_id, step_number)
        step = self._require_awaiting_step(workflow, step_number)
        step.scene_image_approved_at = datetime.now(UTC)
        step.status = WorkflowStepStatus.QUEUED
        step.completed_at = None
        step.error = None
        workflow.status = WorkflowStatus.QUEUED
        workflow.error = None
        await self._persist_workflow_and_step(workflow, step)
        self._queue.enqueue(workflow)
        logger.info(
            "Workflow {} step {}: scene_image aprobada por usuario, re-encolado",
            workflow.id,
            step_number,
        )
        return workflow

    async def regenerate_scene(
        self,
        workflow_id: str,
        step_number: int,
        *,
        scene_description: str | None = None,
        prompt: str | None = None,
        product_prompt: str | None = None,
        text: str | None = None,
    ) -> WorkflowJob:
        """Descarta la scene_image actual y re-encola el workflow para regenerar.

        Resetea `bg_image_job_id`, `scene_image_path`,
        `scene_image_approved_at` a None y pone el step en QUEUED. Cuando
        el workflow se reanude, el step runner generará una scene_image
        nueva con Nano Banana (gasta otro crédito) y volverá a pausar
        en AWAITING_APPROVAL.

        Si se pasan prompts editados, se persisten en el step antes de
        regenerar. También descarta video/finales para que, tras aprobar la
        nueva scene_image, el runner renderice VEO y concatene de nuevo en
        orden.
        """
        workflow = await self._load_step_for_approval(workflow_id, step_number)
        step = self._require_awaiting_step(workflow, step_number)
        self._apply_regenerate_prompt_updates(
            step,
            scene_description=scene_description,
            prompt=prompt,
            product_prompt=product_prompt,
            text=text,
        )
        # Cleanup del archivo viejo (best-effort; no bloqueante).
        if step.scene_image_path:
            scene_path = Path(step.scene_image_path)
            if is_path_inside(scene_path, self._settings.outputs_dir):
                await asyncio.to_thread(_unlink_silent, scene_path)
            else:
                logger.warning(
                    "Workflow {} step {}: no borro scene_image_path fuera de outputs_dir: {}",
                    workflow.id,
                    step_number,
                    scene_path,
                )
        await self._delete_step_video_if_safe(workflow, step)
        await self._delete_final_outputs_if_safe(workflow)
        step.bg_image_job_id = None
        step.scene_image_path = None
        step.scene_image_approved_at = None
        step.video_task_id = None
        step.video_path = None
        step.progress.clear()
        step.status = WorkflowStepStatus.QUEUED
        step.completed_at = None
        step.error = None
        workflow.status = WorkflowStatus.QUEUED
        workflow.error = None
        await self._persist_workflow_and_step(workflow, step)
        self._queue.enqueue(workflow)
        logger.info(
            "Workflow {} step {}: scene_image descartada, regenerando",
            workflow.id,
            step_number,
        )
        return workflow

    @staticmethod
    def _apply_regenerate_prompt_updates(
        step: WorkflowStep,
        *,
        scene_description: str | None,
        prompt: str | None,
        product_prompt: str | None,
        text: str | None,
    ) -> None:
        """Aplica los textos editados en el modal de regeneración."""
        WorkflowController._apply_step_text_updates(
            step,
            scene_name=None,
            scene_description=scene_description,
            prompt=prompt,
            product_prompt=product_prompt,
            text=text,
        )

    @staticmethod
    def _apply_step_text_updates(
        step: WorkflowStep,
        *,
        scene_name: str | None,
        scene_description: str | None,
        prompt: str | None,
        product_prompt: str | None,
        text: str | None,
    ) -> None:
        """Aplica textos editables y valida el shape final del step."""
        if scene_name is not None:
            cleaned_scene_name = scene_name.strip()
            if not cleaned_scene_name:
                raise WorkflowValidationError("scene_name no puede quedar vacío")
            step.scene_name = cleaned_scene_name
        if scene_description is not None:
            step.scene_description = scene_description.strip()
        if prompt is not None:
            cleaned_prompt = prompt.strip()
            if not cleaned_prompt:
                raise WorkflowValidationError("prompt no puede quedar vacío")
            step.prompt = cleaned_prompt
        if product_prompt is not None:
            step.product_prompt = product_prompt.strip()
        if text is not None:
            step.text = text.strip()
        validation_step = step.model_copy(update={"progress": {}})
        validate_workflow_step(validation_step)

    @staticmethod
    def _reset_step_render_state(step: WorkflowStep) -> None:
        """Limpia estado runtime del render sin tocar la scene_image existente."""
        step.status = WorkflowStepStatus.QUEUED
        step.error = None
        step.started_at = None
        step.completed_at = None
        step.progress.clear()
        step.audio_job_id = None
        step.audio_path = None
        step.video_task_id = None
        step.video_path = None

    async def _prepare_step_retry_after_edit(
        self, workflow: WorkflowJob, step: WorkflowStep, step_number: int
    ) -> None:
        await self._delete_step_video_if_safe(workflow, step)
        await self._delete_final_outputs_if_safe(workflow)
        self._reset_step_render_state(step)
        if workflow.status == WorkflowStatus.COMPLETED:
            workflow.status = WorkflowStatus.FAILED
        workflow.error = f"step {step_number} editado; usá Reintentar para renderizarlo de nuevo"
        workflow.manifest_write_failed = False

    async def cancel_step(self, workflow_id: str, step_number: int) -> WorkflowJob:
        """Cancela un step puntual (CANCELLED) sin abortar el workflow entero.

        Útil cuando una scene_image no convence y el usuario prefiere
        saltar ese step en vez de regenerar. El workflow continúa con
        los demás steps. Si era el último pendiente, se finaliza con
        PARTIALLY_FAILED (algunos completed + uno cancelled).
        """
        workflow = await self._load_step_for_approval(workflow_id, step_number)
        step = self._require_awaiting_step(workflow, step_number)
        step.status = WorkflowStepStatus.CANCELLED
        step.completed_at = datetime.now(UTC)
        step.error = "cancelado por usuario tras revisión de scene_image"
        workflow.status = WorkflowStatus.QUEUED
        workflow.error = None
        await self._persist_workflow_and_step(workflow, step)
        self._queue.enqueue(workflow)
        logger.info(
            "Workflow {} step {}: cancelado por usuario, workflow continúa",
            workflow.id,
            step_number,
        )
        return workflow

    async def _load_step_for_approval(self, workflow_id: str, step_number: int) -> WorkflowJob:
        workflow = await self._repository.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"workflow {workflow_id!r} no existe")
        if workflow.step_by_number(step_number) is None:
            raise WorkflowValidationError(f"workflow {workflow_id!r} no tiene step {step_number}")
        return workflow

    async def _load_editable_step(
        self, workflow_id: str, step_number: int
    ) -> tuple[WorkflowJob, WorkflowStep]:
        workflow = await self._repository.get(workflow_id)
        if workflow is None:
            raise WorkflowNotFoundError(f"workflow {workflow_id!r} no existe")
        if workflow.status not in _RECREATABLE_WORKFLOW_STATUSES:
            raise WorkflowValidationError(
                "solo se puede editar un step cuando el workflow está en estado terminal"
            )
        step = self._find_step(workflow, step_number)
        if step is None:
            raise WorkflowValidationError(f"workflow {workflow_id!r} no tiene step {step_number}")
        return workflow, step

    @staticmethod
    def _require_awaiting_step(workflow: WorkflowJob, step_number: int) -> WorkflowStep:
        step = workflow.step_by_number(step_number)
        if step is None:  # validado en _load_step_for_approval pero defensivo
            raise WorkflowValidationError(f"workflow {workflow.id!r} no tiene step {step_number}")
        if not step.is_awaiting_approval():
            raise WorkflowValidationError(
                f"step {step_number} no está esperando aprobación "
                f"(status actual: {step.status.value})"
            )
        return step

    async def _persist_workflow_and_step(self, workflow: WorkflowJob, step: WorkflowStep) -> None:
        await self._repository.upsert_step(workflow.id, step)
        await self._repository.update_workflow_header(workflow)
        await self._manifest_writer.write(workflow)

    @staticmethod
    def _find_step(workflow: WorkflowJob, step_number: int) -> WorkflowStep | None:
        for step in workflow.steps:
            if step.step == step_number:
                return step
        return None

    async def _delete_step_video_if_safe(self, workflow: WorkflowJob, step: WorkflowStep) -> None:
        if not step.video_path:
            return
        video_path = Path(step.video_path)
        if not is_path_inside(video_path, self._settings.outputs_dir):
            logger.warning(
                "Workflow {} step {}: no borro video_path fuera de outputs_dir: {}",
                workflow.id,
                step.step,
                video_path,
            )
            return
        await asyncio.to_thread(_unlink_silent, video_path)

    async def _delete_final_outputs_if_safe(self, workflow: WorkflowJob) -> None:
        output_dir = Path(workflow.output_dir)
        if not is_path_inside(output_dir, self._settings.outputs_dir):
            logger.warning(
                "Workflow {}: no borro finales porque output_dir queda fuera de outputs_dir: {}",
                workflow.id,
                output_dir,
            )
            return
        filenames = (
            workflow_final_video_filename(workflow.slug),
            workflow_final_audio_filename(workflow.slug),
            workflow_voice_changed_audio_filename(workflow.slug),
            *_LEGACY_FINAL_OUTPUT_FILENAMES,
        )
        for filename in filenames:
            await asyncio.to_thread(_unlink_silent, output_dir / filename)

    async def _needs_postprocess_repair(self, workflow: WorkflowJob) -> bool:
        """`True` si el workflow terminó pero faltan artefactos finales.

        Caso de recuperación para runs históricas marcadas `completed` pero sin
        `final.mp4`/`final_audio.mp3` pese a tener videos de steps en disco.
        """
        output_dir = Path(workflow.output_dir)
        if not is_path_inside(output_dir, self._settings.outputs_dir):
            return False
        has_final_video = await _any_file_exists(workflow_final_video_candidates(workflow))
        has_final_audio = await _any_file_exists(workflow_final_audio_candidates(workflow))
        if has_final_video and has_final_audio:
            return False

        for step in workflow.steps:
            if step.status != WorkflowStepStatus.COMPLETED or not step.video_path:
                continue
            video_path = Path(step.video_path)
            if not is_path_inside(video_path, self._settings.outputs_dir):
                continue
            if await asyncio.to_thread(video_path.is_file):
                return True
        return False

    async def _workflow_requires_product_reload(self, workflow: WorkflowJob) -> bool:
        """Indica si el retry necesita recargar la ref del producto.

        Solo aplica cuando aún hay steps pendientes (`status != COMPLETED`)
        que usan `include_product=true`.
        """
        pending_product_steps = any(
            step.include_product and step.status != WorkflowStepStatus.COMPLETED
            for step in workflow.steps
        )
        if not workflow.pre_settings.promote_product or not pending_product_steps:
            return False
        product = workflow.pre_settings.product_image
        if product is None or product.resolved_image_ref is None:
            return True
        ref = product.resolved_image_ref
        now = datetime.now(UTC)
        if ref.kind == ImageAssetKind.UPLOADED:
            uploaded = await self._uploaded_images.get(ref.id)
            return uploaded is None or uploaded.is_expired(KIE_UPLOAD_RETENTION_HOURS, now=now)
        generated = await self._generated_images.get(ref.id)
        return generated is None or generated.is_expired(KIE_GENERATED_RETENTION_DAYS, now=now)

    async def delete(self, workflow_id: str) -> None:
        await self._repository.delete(workflow_id)

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def _new_workflow_id() -> str:
        stamp = datetime.now(UTC).strftime(_WORKFLOW_ID_TS_FMT)
        return f"wf_{stamp}_{uuid.uuid4().hex[:_WORKFLOW_ID_SHORT_LEN]}"

    def _build_output_dir(self, workflow_id: str) -> Path:
        return self._settings.outputs_dir / sanitize_filename(workflow_id)


def get_or_raise_workflow(workflow: WorkflowJob | None, workflow_id: str) -> WorkflowJob:
    """Helper para uso de UI: convierte `None` a `WorkflowNotFoundError`."""
    if workflow is None:
        raise WorkflowNotFoundError(f"workflow '{workflow_id}' no existe")
    return workflow


# Sanity: WorkflowStatus se reexporta para la UI sin acoplarla al dominio.
def _unlink_silent(path: Path) -> None:
    """Borra `path` si existe, swallow OSError. Para ejecutar en `asyncio.to_thread`."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _payload_has_scene_approval_mode(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    pre_settings = payload.get("pre_settings")
    return isinstance(pre_settings, dict) and "scene_approval_mode" in pre_settings


async def _any_file_exists(paths: tuple[Path, ...]) -> bool:
    for path in paths:
        if await asyncio.to_thread(path.is_file):
            return True
    return False


__all__ = ["WorkflowController", "WorkflowStatus", "get_or_raise_workflow"]
