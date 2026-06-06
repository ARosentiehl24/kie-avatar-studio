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
    ModelCreationMethod,
    VoicePreset,
    WorkflowEntry,
    WorkflowJob,
    WorkflowStatus,
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

WorkflowScanLoader = Callable[[], Awaitable[list[WorkflowEntry]]]
WorkflowEntryBuilder = Callable[..., WorkflowJob]

WorkflowEventListener = (
    Callable[[WorkflowJobUpdated], None] | Callable[[WorkflowJobUpdated], Awaitable[None]]
)

_WORKFLOW_ID_TS_FMT: Final[str] = "%Y%m%d_%H%M%S"
_WORKFLOW_ID_SHORT_LEN: Final[int] = 6


class WorkflowController:
    """Casos de uso de la pantalla Automatización."""

    def __init__(
        self,
        settings: Settings,
        repository: WorkflowRepository,
        manifest_writer: WorkflowManifestWriter,
        queue: QueueManager[WorkflowJob, WorkflowJobUpdated],
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
    ) -> WorkflowJob:
        """Encola un workflow validando preset + archivos locales.

        Si `voice_preset_id` se pasa, sobreescribe el del JSON. Idem
        `audio_language`. La validación cruzada (preset existe en
        `VoicePresetStore`) se hace acá para fallar early sin gastar
        créditos.
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
        await self._validate_voice_preset(workflow)
        await self._validate_local_model_path(workflow)
        await self._persist_and_enqueue(workflow)
        return workflow

    async def _validate_voice_preset(self, workflow: WorkflowJob) -> VoicePreset | None:
        preset_id = workflow.pre_settings.voice_preset_id
        if not preset_id:
            return None
        preset = await self._presets_store.get(preset_id)
        if preset is None:
            raise WorkflowValidationError(
                f"voice_preset '{preset_id}' no existe en el catálogo. "
                "Creá uno en la pantalla Presets antes de ejecutar este workflow."
            )
        return preset

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
__all__ = ["WorkflowController", "WorkflowStatus", "get_or_raise_workflow"]
