"""Tests del `WorkflowBaseResolver` (resolución de imagen base).

Cubre el camino crítico: si `creation.resolved_image_ref` está poblado,
el resolver lo reusa SOLO si no expiró. Si expiró, cae al path normal
(regenera/re-sube). Esto es importante para retries donde el workflow
estuvo encolado mucho tiempo (>24h para uploaded, >14d para generated).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kie_avatar_studio.app_layer.runner_factories import (
    AudioRunnerDeps,
    ImageRunnerDeps,
    WorkflowRunnerFactory,
)
from kie_avatar_studio.app_layer.workflow_base_resolver import WorkflowBaseResolver
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import (
    ImageAssetKind,
    ImageAssetRef,
    ModelCreation,
    ModelCreationMethod,
    WorkflowJob,
    WorkflowPreSettings,
)


def _make_workflow(
    creation: ModelCreation,
    *,
    workflow_id: str = "wf_test",
    output_dir: str = "outputs/wf_test",
) -> WorkflowJob:
    return WorkflowJob(
        id=workflow_id,
        name="Test WF",
        slug="test_wf",
        source_json_path="workflows/test.json",
        output_dir=output_dir,
        pre_settings=WorkflowPreSettings(model_creation=creation),
        steps=[],  # los tests del resolver no miran los steps
    )


def _build_resolver(tmp_settings: Settings) -> WorkflowBaseResolver:
    """Construye un resolver con mocks de Kie + factory para tests aislados."""
    client = MagicMock()
    client.upload_file = AsyncMock()
    client.download_file = AsyncMock()
    presets_store = MagicMock()
    uploaded_images = MagicMock()
    uploaded_images.get = AsyncMock(return_value=None)
    uploaded_images.upsert = AsyncMock()
    generated_images = MagicMock()
    generated_images.get = AsyncMock(return_value=None)
    image_jobs_repo = MagicMock()
    capacity_limiter = asyncio.Semaphore(1)
    runner_factory = WorkflowRunnerFactory(
        image_deps=ImageRunnerDeps(
            settings=tmp_settings,
            client=client,
            image_jobs_repo=image_jobs_repo,
            generated_images_store=generated_images,
            uploaded_images_store=uploaded_images,
        ),
        audio_deps=AudioRunnerDeps(
            settings=tmp_settings,
            client=client,
            audio_jobs_repo=MagicMock(),
            audios_store=MagicMock(),
        ),
    )
    return WorkflowBaseResolver(
        tmp_settings,
        client,
        presets_store,
        uploaded_images,
        generated_images,
        image_jobs_repo,
        capacity_limiter,
        runner_factory,
    )


async def test_resolve_base_image_reuses_fresh_resolved_ref(
    tmp_settings: Settings,
) -> None:
    """Si `resolved_image_ref` está poblado y no expiró, se reusa sin tocar Kie."""
    resolver = _build_resolver(tmp_settings)
    fresh_ref = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id="img_fresh",
        label="fresh",
        kie_url="https://tempfile.kie.ai/fresh.png",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    creation = ModelCreation(
        method=ModelCreationMethod.PROMPT,
        prompt="ignored",
        resolved_image_ref=fresh_ref,
    )
    workflow = _make_workflow(creation)
    result = await resolver.resolve_base_image(workflow)
    assert result is fresh_ref
    # NO se debe llamar a Kie ni al store local.
    resolver._client.upload_file.assert_not_called()


async def test_resolve_base_image_drops_expired_resolved_ref_for_local(
    tmp_settings: Settings,
) -> None:
    """Si `resolved_image_ref` expiró, debe caer al path original (LOCAL → upload)."""
    resolver = _build_resolver(tmp_settings)
    expired_ref = ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id="uploads/old.png",
        label="old.png",
        kie_url="https://tempfile.kie.ai/old.png",
        # Expirado: 25h atrás (más que retención 24h).
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    # Necesitamos un archivo real para que validate_image_path pase.
    local_file = tmp_settings.inputs_dir / "fallback.png"
    local_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 200)
    creation = ModelCreation(
        method=ModelCreationMethod.LOCAL,
        local_path=str(local_file),
        resolved_image_ref=expired_ref,
    )
    workflow = _make_workflow(creation)

    # Mockeamos upload_file para devolver un resultado "fresco".
    upload_result = MagicMock()
    upload_result.file_path = "uploads/fresh.png"
    upload_result.download_url = "https://tempfile.kie.ai/fresh_uploaded.png"
    upload_result.file_size = 200
    upload_result.mime_type = "image/png"
    resolver._client.upload_file.return_value = upload_result  # type: ignore[attr-defined]

    result = await resolver.resolve_base_image(workflow)
    # El resolver detectó que el ref expiró y re-subió desde el local_path.
    assert result.kind == ImageAssetKind.UPLOADED
    assert result.kie_url == "https://tempfile.kie.ai/fresh_uploaded.png"
    # creation.resolved_image_ref se reseteó porque expiró + se reasignó.
    assert workflow.pre_settings.model_creation.resolved_image_ref is result
    resolver._client.upload_file.assert_called_once()  # type: ignore[attr-defined]


async def test_resolve_base_image_no_resolved_ref_goes_to_normal_path(
    tmp_settings: Settings,
) -> None:
    """Sin resolved_ref, sigue el path tradicional según method."""
    resolver = _build_resolver(tmp_settings)
    local_file = tmp_settings.inputs_dir / "modelo.png"
    local_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 200)
    creation = ModelCreation(
        method=ModelCreationMethod.LOCAL,
        local_path=str(local_file),
    )
    workflow = _make_workflow(creation)
    upload_result = MagicMock()
    upload_result.file_path = "uploads/modelo.png"
    upload_result.download_url = "https://tempfile.kie.ai/modelo.png"
    upload_result.file_size = 200
    upload_result.mime_type = "image/png"
    resolver._client.upload_file.return_value = upload_result  # type: ignore[attr-defined]
    result = await resolver.resolve_base_image(workflow)
    assert result.kind == ImageAssetKind.UPLOADED
    resolver._client.upload_file.assert_called_once()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "kind,retention_hours",
    [
        (ImageAssetKind.UPLOADED, 24),
        (ImageAssetKind.GENERATED, 14 * 24),
    ],
)
async def test_resolved_ref_just_about_to_expire_is_still_used(
    tmp_settings: Settings, kind: ImageAssetKind, retention_hours: int
) -> None:
    """Borde de expiración: 30min antes de vencer todavía se reusa."""
    resolver = _build_resolver(tmp_settings)
    ref = ImageAssetRef(
        kind=kind,
        id="x",
        label="x",
        kie_url="https://x",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    creation = ModelCreation(
        method=ModelCreationMethod.PROMPT,
        prompt="ignored",
        resolved_image_ref=ref,
    )
    workflow = _make_workflow(creation)
    result = await resolver.resolve_base_image(workflow)
    assert result is ref


async def test_upload_local_standalone_persists_to_uploaded_store(
    tmp_settings: Settings, tmp_path: Path
) -> None:
    """`upload_local_standalone` sube a Kie Y persiste en el uploaded store.

    Es necesario para que el `ImageJobRunner` revalide la ref cuando la
    imagen se use como input de Nano Banana (producto promocional o base
    method=local con change_scene). El id persistido == kie_file_path ==
    id de la ref devuelta.
    """
    from kie_avatar_studio.domain.models import KieUploadResult

    resolver = _build_resolver(tmp_settings)
    resolver._uploaded_images.upsert = AsyncMock()
    resolver._client.upload_file = AsyncMock(
        return_value=KieUploadResult(
            file_name="product.png",
            file_path="uploads/product.png",
            download_url="https://tempfile.kie.ai/product.png",
            file_size=1234,
            mime_type="image/png",
        )
    )
    product_file = tmp_path / "product.png"
    product_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)  # png header + relleno

    ref = await resolver.upload_local_standalone(product_file)

    # La ref devuelta usa kie_file_path como id.
    assert ref.id == "uploads/product.png"
    assert ref.kie_url == "https://tempfile.kie.ai/product.png"
    # Se persistió un UploadedImage con el mismo id (para revalidación).
    resolver._uploaded_images.upsert.assert_awaited_once()
    persisted = resolver._uploaded_images.upsert.await_args.args[0]
    assert persisted.id == "uploads/product.png"
    assert persisted.kie_url == "https://tempfile.kie.ai/product.png"


def test_build_base_image_job_uses_global_image_aspect_ratio(
    tmp_settings: Settings,
) -> None:
    """_build_base_image_job debe usar el aspect ratio global configurado en pre_settings."""
    resolver = _build_resolver(tmp_settings)
    creation = ModelCreation(method=ModelCreationMethod.PROMPT, prompt="A woman")
    workflow = _make_workflow(creation)
    workflow.pre_settings.image_aspect_ratio = "9:16"

    job = resolver._build_base_image_job(workflow, creation.prompt)
    assert job.settings_json is not None
    # Verificamos que se serializó el aspect_ratio global ("9:16") en los settings del job base.
    assert '"aspect_ratio":"9:16"' in job.settings_json
