"""`WorkflowController`: casos de uso de la pantalla Automatización.

Capa de aplicación que orquesta:
- Listar workflows del filesystem (`workflows/*.json`) merge con DB.
- Validar (existencia del voice_preset, archivos locales, etc.) antes
  de encolar.
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
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from ..domain.events import WorkflowJobUpdated
from ..domain.models import (
    ImageAssetRef,
    ImageGenerationSettings,
    ModelCreationMethod,
    ProductImage,
    SceneApprovalMode,
    VoicePreset,
    WorkflowEntry,
    WorkflowJob,
    WorkflowStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from ..domain.policies import validate_image_path
from ..domain.ports import (
    GeneratedImageStore,
    ImageStore,
    VoicePresetStore,
    WorkflowManifestWriter,
    WorkflowRepository,
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
        presets_store: VoicePresetStore,
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
        self._presets_store = presets_store
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
        voice_preset_id: str | None = None,
        audio_language: str | None = None,
        resolved_base_ref: ImageAssetRef | None = None,
        local_path: str | None = None,
        i2v_duration_override: int | None = None,
        scene_approval_mode: SceneApprovalMode | None = None,
        product_ref: ImageAssetRef | None = None,
        product_local_path: str | None = None,
    ) -> WorkflowJob:
        """Encola un workflow validando preset + archivos locales.

        Si `voice_preset_id` se pasa, sobreescribe el del JSON. Idem
        `audio_language`. La validación cruzada (preset existe en
        `VoicePresetStore`) se hace acá para fallar early sin gastar
        créditos.

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
        """
        if not entry.valid:
            raise WorkflowValidationError(
                f"entry '{entry.name}' no es válido: {'; '.join(entry.errors)}"
            )
        workflow_id = self._new_workflow_id()
        output_dir = self._build_output_dir(workflow_id)
        workflow = self._entry_builder(entry, workflow_id=workflow_id, output_dir=output_dir)
        if voice_preset_id is not None:
            workflow.pre_settings.voice_preset_id = voice_preset_id
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
        self._apply_product_selection(workflow, product_ref, product_local_path)
        await self._validate_voice_preset(workflow)
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
        """Genera la imagen base con Nano Banana 2 y la descarga local para previsualizarla.

        Devuelve `(ref, local_path)`. La UI muestra `local_path` y permite
        al usuario aprobar/regenerar antes de encolar el workflow real.
        Crea siempre un path nuevo bajo `outputs/_previews/<timestamp>.png`
        para que el usuario pueda regenerar y comparar.

        `settings` permite override de `aspect_ratio` / `resolution` /
        `output_format`. Si es `None`, usa los defaults del catálogo
        Nano Banana 2 (auto / 1K / jpg).
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

    # --- preset / path validators ---------------------------------------

    async def _validate_voice_preset(self, workflow: WorkflowJob) -> VoicePreset | None:
        """Resuelve el `voice_preset` del workflow contra `VoicePresetStore`.

        Acepta tanto el `id` (slug) como el `label` (nombre humano) del
        preset. Si encuentra match por label, **normaliza el campo del
        workflow al id real** antes de persistir, así la DB siempre tiene
        el id canónico (y el runner no tiene que volver a hacer fuzzy
        matching al ejecutar).
        """
        preset_ref = workflow.pre_settings.voice_preset_id
        if not preset_ref:
            return None
        preset = await self._resolve_preset(preset_ref)
        if preset is None:
            raise WorkflowValidationError(
                f"voice_preset '{preset_ref}' no existe en el catálogo. "
                "Creá uno en la pantalla Presets o desde el modal 'Configurar y ejecutar' "
                "antes de ejecutar este workflow."
            )
        # Normalizamos al id real (puede ser distinto si el JSON usaba el label).
        workflow.pre_settings.voice_preset_id = preset.id
        return preset

    async def _resolve_preset(self, preset_ref: str) -> VoicePreset | None:
        """Match por id exacto > match por label exacto (case-insensitive)."""
        target = preset_ref.strip()
        if not target:
            return None
        direct = await self._presets_store.get(target)
        if direct is not None:
            return direct
        all_presets = await self._presets_store.list_all()
        target_lower = target.lower()
        for preset in all_presets:
            if preset.label.lower() == target_lower:
                return preset
        return None

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
        return await self._queue.retry(workflow)

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

    async def regenerate_scene(self, workflow_id: str, step_number: int) -> WorkflowJob:
        """Descarta la scene_image actual y re-encola el workflow para regenerar.

        Resetea `bg_image_job_id`, `scene_image_path`,
        `scene_image_approved_at` a None y pone el step en QUEUED. Cuando
        el workflow se reanude, el step runner generará una scene_image
        nueva con Nano Banana (gasta otro crédito) y volverá a pausar
        en AWAITING_APPROVAL.

        El archivo `scene.png` local se borra si existe (mejor evitar
        confusión con preview viejo); el `kie_url` del image_job viejo
        queda huérfano pero el TTL de Kie lo limpia solo.
        """
        workflow = await self._load_step_for_approval(workflow_id, step_number)
        step = self._require_awaiting_step(workflow, step_number)
        # Cleanup del archivo viejo (best-effort; no bloqueante).
        if step.scene_image_path:
            await asyncio.to_thread(_unlink_silent, Path(step.scene_image_path))
        step.bg_image_job_id = None
        step.scene_image_path = None
        step.scene_image_approved_at = None
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


__all__ = ["WorkflowController", "WorkflowStatus", "get_or_raise_workflow"]
