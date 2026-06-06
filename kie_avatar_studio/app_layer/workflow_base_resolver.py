"""`WorkflowBaseResolver`: resuelve voice + imagen base de un workflow.

Extraído de `workflow_runner.py` para CR-3.2 (≤300 líneas) y separar la
responsabilidad de "preparación pre-ejecución" de la orquestación de
steps. El resolver:

1. Mapea `pre_settings.voice_preset_id` → (voice_id, voice_settings)
   consultando el `VoicePresetStore`.
2. Resuelve `pre_settings.model_creation` según `method`:
   - `prompt` → genera con Nano Banana 2 vía `ImageJobRunner` ad-hoc.
   - `local` → sube el archivo local con `KieGateway.upload_file` (con
     revalidación pre-upload del path).
   - `catalog` → busca en `ImageStore`/`GeneratedImageStore`.
3. Descarga `base.png` eager al output_dir antes de empezar steps.

NO mutea status del workflow ni emite eventos: solo devuelve los
artefactos resueltos. El `WorkflowRunner` se encarga de las transiciones.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from ..config import Settings
from ..domain.errors import WorkflowValidationError
from ..domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
    ModelCreation,
    ModelCreationMethod,
    VoicePreset,
    VoiceSettings,
    WorkflowJob,
)
from ..domain.policies import (
    KIE_GENERATED_RETENTION_DAYS,
    KIE_UPLOAD_RETENTION_HOURS,
    validate_image_path,
)
from ..domain.ports import (
    GeneratedImageStore,
    ImageJobRepository,
    ImageStore,
    KieGateway,
    VoicePresetStore,
)
from .ids import new_image_job_id
from .runner_factories import WorkflowRunnerFactory

BASE_IMAGE_FILENAME: Final[str] = "base.png"


class WorkflowBaseResolver:
    """Resuelve voice + imagen base de un workflow antes de ejecutar los steps."""

    def __init__(
        self,
        settings: Settings,
        client: KieGateway,
        presets_store: VoicePresetStore,
        uploaded_images: ImageStore,
        generated_images: GeneratedImageStore,
        image_jobs_repo: ImageJobRepository,
        capacity_limiter: asyncio.Semaphore,
        runner_factory: WorkflowRunnerFactory,
    ) -> None:
        self._settings = settings
        self._client = client
        self._presets_store = presets_store
        self._uploaded_images = uploaded_images
        self._generated_images = generated_images
        self._image_jobs_repo = image_jobs_repo
        self._capacity_limiter = capacity_limiter
        self._runner_factory = runner_factory

    async def resolve_voice(
        self, workflow: WorkflowJob
    ) -> tuple[str, VoiceSettings | None]:
        """Devuelve `(voice_id, voice_settings)` resueltos desde el preset.

        Si `voice_preset_id` está vacío, usa `settings.default_voice` como
        fallback (CR-3.3: no hardcodear ids duplicados). Si está seteado
        pero el preset no existe, falla con `WorkflowValidationError`.
        """
        preset_id = workflow.pre_settings.voice_preset_id
        if not preset_id:
            return self._settings.default_voice, None
        preset = await self._presets_store.get(preset_id)
        if preset is None:
            raise WorkflowValidationError(
                f"voice_preset '{preset_id}' no existe en el catálogo "
                "(revisá los presets configurados)."
            )
        return _voice_from_preset(preset)

    async def resolve_base_image(self, workflow: WorkflowJob) -> ImageAssetRef:
        """Resuelve la imagen base según `pre_settings.model_creation.method`."""
        creation = workflow.pre_settings.model_creation
        if creation.method == ModelCreationMethod.PROMPT:
            return await self._resolve_from_prompt(workflow, creation)
        if creation.method == ModelCreationMethod.LOCAL:
            return await self._resolve_from_local(creation)
        return await self._resolve_from_catalog(creation)

    async def download_base_locally(
        self, ref: ImageAssetRef, output_dir: Path
    ) -> None:
        """Descarga la imagen base a `output_dir/base.png` para uso del usuario."""
        target = output_dir / BASE_IMAGE_FILENAME
        await self._client.download_file(ref.kie_url, target)

    # --- method=prompt ---------------------------------------------------

    async def _resolve_from_prompt(
        self, workflow: WorkflowJob, creation: ModelCreation
    ) -> ImageAssetRef:
        if not creation.prompt:
            raise WorkflowValidationError(
                "model_creation.method='prompt' requiere prompt"
            )
        image_job = self._build_base_image_job(workflow, creation.prompt)
        await self._image_jobs_repo.upsert(image_job)
        runner = self._runner_factory.make_image_runner()
        async with self._capacity_limiter:
            await runner.run(image_job)
        if image_job.status != ImageJobStatus.COMPLETED or not image_job.kie_url:
            raise WorkflowValidationError(
                f"falló la generación de la imagen base "
                f"({image_job.error or 'sin mensaje'})"
            )
        return await self._make_ref_from_completed_job(image_job, creation)

    def _build_base_image_job(self, workflow: WorkflowJob, prompt: str) -> ImageJob:
        return ImageJob(
            id=new_image_job_id(),
            label=f"[wf-base]{workflow.slug}",
            prompt=prompt,
            settings_json=ImageGenerationSettings().model_dump_json(exclude_none=True),
            refs_json=json.dumps([]),
            status=ImageJobStatus.QUEUED,
        )

    async def _make_ref_from_completed_job(
        self, image_job: ImageJob, creation: ModelCreation
    ) -> ImageAssetRef:
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

    # --- method=local ----------------------------------------------------

    async def _resolve_from_local(self, creation: ModelCreation) -> ImageAssetRef:
        if not creation.local_path:
            raise WorkflowValidationError(
                "model_creation.method='local' requiere local_path"
            )
        path = Path(creation.local_path)
        # Revalidación: el archivo puede haber sido movido/borrado entre
        # la validación inicial y el momento del upload.
        validate_image_path(path)
        result = await self._client.upload_file(path)
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

    # --- method=catalog --------------------------------------------------

    async def _resolve_from_catalog(self, creation: ModelCreation) -> ImageAssetRef:
        if creation.asset_kind is None or not creation.asset_id:
            raise WorkflowValidationError(
                "model_creation.method='catalog' requiere asset_kind y asset_id"
            )
        if creation.asset_kind == ImageAssetKind.UPLOADED:
            return await self._resolve_uploaded(creation)
        return await self._resolve_generated(creation)

    async def _resolve_uploaded(self, creation: ModelCreation) -> ImageAssetRef:
        if creation.asset_id is None:
            raise WorkflowValidationError(
                "model_creation.method='catalog' requiere asset_id"
            )
        uploaded = await self._uploaded_images.get(creation.asset_id)
        if uploaded is None:
            raise WorkflowValidationError(
                f"imagen subida '{creation.asset_id}' no existe en el catálogo"
            )
        ref = ImageAssetRef(
            kind=ImageAssetKind.UPLOADED,
            id=uploaded.id,
            label=uploaded.label,
            kie_url=uploaded.kie_url,
            expires_at=uploaded.expires_at(KIE_UPLOAD_RETENTION_HOURS),
        )
        creation.resolved_image_ref = ref
        return ref

    async def _resolve_generated(self, creation: ModelCreation) -> ImageAssetRef:
        if creation.asset_id is None:
            raise WorkflowValidationError(
                "model_creation.method='catalog' requiere asset_id"
            )
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


def _voice_from_preset(preset: VoicePreset) -> tuple[str, VoiceSettings | None]:
    return preset.voice_id, preset.voice_settings


__all__ = ["BASE_IMAGE_FILENAME", "WorkflowBaseResolver"]
