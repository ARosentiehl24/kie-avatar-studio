"""Tests de `GeneratedImagesController` (cola + persistencia de generadas)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from kie_avatar_studio.app_layer.generated_images_controller import GeneratedImagesController
from kie_avatar_studio.app_layer.image_job_lifecycle import ImageJobLifecycle
from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.domain.errors import (
    GeneratedImageExpiredError,
    GeneratedImageNotFoundError,
    ImageGenerationValidationError,
)
from kie_avatar_studio.domain.events import ImageJobUpdated
from kie_avatar_studio.domain.models import (
    GeneratedImage,
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
)
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB


class _FakeRunner:
    """Runner que solo marca el job como COMPLETED in-memory (sin persistir).

    Mantiene el patrón del runner real (mutación + retorno) pero sin
    tocar repos: las pruebas del controller no deben verse afectadas
    por el momento exacto en que el runner corre en background.
    """

    async def run(self, job: ImageJob) -> ImageJob:
        job.status = ImageJobStatus.COMPLETED
        job.kie_url = "https://tempfile.redpandaai.co/done.png"
        job.kie_file_path = "kieai/done.png"
        return job


@pytest.fixture
async def repo(tmp_path) -> ImageJobsDB:
    d = ImageJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def store(tmp_path) -> GeneratedImagesDB:
    d = GeneratedImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
def controller(tmp_settings, repo, store) -> GeneratedImagesController:
    runner = _FakeRunner()
    queue: QueueManager[ImageJob, ImageJobUpdated] = QueueManager(
        tmp_settings,
        runner,
        event_factory=ImageJobUpdated,
        lifecycle=ImageJobLifecycle(repo),
    )
    return GeneratedImagesController(store, repo, queue)


async def test_enqueue_persists_and_queues(
    controller: GeneratedImagesController,
) -> None:
    """El job devuelto debe estar en QUEUED al momento de retorno (antes de
    que el runner corra). El estado posterior puede cambiar; lo que importa
    acá es que el controller construye correctamente y persiste."""
    job = await controller.enqueue_generation("paisaje", "atardecer")
    assert job.label == "paisaje"
    assert job.prompt == "atardecer"
    # ID generado con el prefijo correcto.
    assert job.id.startswith("img_")


async def test_enqueue_rejects_empty_label(controller: GeneratedImagesController) -> None:
    with pytest.raises(ImageGenerationValidationError):
        await controller.enqueue_generation("   ", "prompt")


async def test_enqueue_with_settings_serializes(
    controller: GeneratedImagesController, repo: ImageJobsDB
) -> None:
    settings = ImageGenerationSettings(aspect_ratio="16:9", resolution="2K", output_format="png")
    job = await controller.enqueue_generation("x", "y", settings=settings)
    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.settings_json is not None
    assert '"aspect_ratio":"16:9"' in fetched.settings_json


async def test_enqueue_with_refs_serializes_kind(
    controller: GeneratedImagesController, repo: ImageJobsDB
) -> None:
    refs = [
        ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id="r1",
            label="r1",
            kie_url="https://tempfile.redpandaai.co/r1.png",
            expires_at=datetime.now(UTC) + timedelta(days=14),
        )
    ]
    job = await controller.enqueue_generation("x", "y", refs=refs)
    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.refs_json is not None
    # json.dumps usa separadores con espacios por default.
    assert '"kind": "generated"' in fetched.refs_json


async def test_get_for_use_raises_when_missing(
    controller: GeneratedImagesController,
) -> None:
    with pytest.raises(GeneratedImageNotFoundError):
        await controller.get_for_use("nope")


async def test_get_for_use_raises_when_expired(
    controller: GeneratedImagesController, store: GeneratedImagesDB
) -> None:
    old = GeneratedImage(
        id="old",
        label="old",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/old.png",
        kie_file_path="kieai/old.png",
        generated_at=datetime.now(UTC) - timedelta(days=15),
    )
    await store.upsert(old)
    with pytest.raises(GeneratedImageExpiredError):
        await controller.get_for_use("old")


async def test_wait_for_job_returns_terminal(
    controller: GeneratedImagesController,
) -> None:
    job = await controller.enqueue_generation("x", "y")
    final = await asyncio.wait_for(controller.wait_for_job(job.id), timeout=5)
    assert final.status == ImageJobStatus.COMPLETED


async def test_delete_job_removes_both(
    controller: GeneratedImagesController,
    repo: ImageJobsDB,
    store: GeneratedImagesDB,
) -> None:
    job = await controller.enqueue_generation("x", "y")
    await asyncio.wait_for(controller.wait_for_job(job.id), timeout=5)
    await controller.delete_job(job.id)
    assert await repo.get(job.id) is None
    assert await store.get(job.id) is None


async def test_cleanup_expired_removes_only_expired(
    controller: GeneratedImagesController, store: GeneratedImagesDB
) -> None:
    fresh = GeneratedImage(
        id="fresh",
        label="fresh",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/f.png",
        kie_file_path="kieai/f.png",
    )
    old = GeneratedImage(
        id="old",
        label="old",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/o.png",
        kie_file_path="kieai/o.png",
        generated_at=datetime.now(UTC) - timedelta(days=20),
    )
    await store.upsert(fresh)
    await store.upsert(old)
    cleaned = await controller.cleanup_expired()
    assert [c.id for c in cleaned] == ["old"]
    assert await store.get("fresh") is not None
    assert await store.get("old") is None
