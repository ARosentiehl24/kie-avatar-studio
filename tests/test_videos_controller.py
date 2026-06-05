"""Tests de `VideosController`: enqueue_from_assets + acciones + wait_for_job.

Tras el refactor a `ImageAssetRef`, los assertions de "tipo de imagen
correcto" están cubiertos en `test_videos_controller_mixed_assets.py`.
Acá testeamos el contrato base con UploadedImage como kind por defecto
y los caminos de error compartidos.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from kie_avatar_studio.app_layer.image_catalog_controller import ImageCatalogController
from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.video_job_lifecycle import VideoJobLifecycle
from kie_avatar_studio.app_layer.videos_controller import VideosController
from kie_avatar_studio.domain.errors import (
    AudioExpiredError,
    AudioNotFoundError,
    ImageExpiredError,
    ImageNotFoundError,
    JobValidationError,
)
from kie_avatar_studio.domain.events import JobUpdated
from kie_avatar_studio.domain.models import (
    GeneratedAudio,
    ImageAssetKind,
    ImageAssetRef,
    JobStatus,
    UploadedImage,
    VideoJob,
)
from kie_avatar_studio.infra.audios_db import AudiosDB
from kie_avatar_studio.infra.db import JobsDB
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.images_db import ImagesDB


class _RecordingRunner:
    """Runner fake que registra jobs procesados y simula transición."""

    def __init__(self, target_status: JobStatus = JobStatus.COMPLETED) -> None:
        self.target_status = target_status
        self.processed: list[VideoJob] = []

    async def run(self, job: VideoJob) -> VideoJob:
        self.processed.append(job)
        job.status = self.target_status
        if self.target_status == JobStatus.FAILED:
            job.error = "simulado"
        return job


@pytest.fixture
async def jobs_repo(tmp_path) -> JobsDB:
    d = JobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def images_store(tmp_path) -> ImagesDB:
    d = ImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def generated_images_store(tmp_path) -> GeneratedImagesDB:
    d = GeneratedImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def audios_store(tmp_path) -> AudiosDB:
    d = AudiosDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
def catalog(images_store, generated_images_store) -> ImageCatalogController:
    return ImageCatalogController(images_store, generated_images_store)


def _build_queue(
    tmp_settings, repo: JobsDB, runner: _RecordingRunner
) -> QueueManager[VideoJob, JobUpdated]:
    return QueueManager(
        tmp_settings,
        runner,
        event_factory=JobUpdated,
        lifecycle=VideoJobLifecycle(repo),
    )


def _sample_image(image_id: str = "img-1", *, uploaded_at: datetime | None = None) -> UploadedImage:
    return UploadedImage(
        id=image_id,
        label="avatar Maria",
        local_path="/tmp/maria.png",
        kie_url=f"https://tempfile.redpandaai.co/kieai/{image_id}.png",
        kie_file_path=f"kieai/{image_id}.png",
        file_size=12345,
        mime_type="image/png",
        uploaded_at=uploaded_at or datetime.now(UTC),
    )


def _sample_audio(
    audio_id: str = "aud-1", *, generated_at: datetime | None = None
) -> GeneratedAudio:
    return GeneratedAudio(
        id=audio_id,
        label="saludo Maria",
        script="Hola, soy María",
        voice_id="EkK5I93UQWFDigLMpZcX",
        kie_url=f"https://tempfile.redpandaai.co/kieai/{audio_id}.mp3",
        kie_file_path=f"kieai/{audio_id}.mp3",
        generated_at=generated_at or datetime.now(UTC),
    )


def _uploaded_ref(image_id: str = "img-1") -> ImageAssetRef:
    return ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id=image_id,
        label="avatar Maria",
        kie_url=f"https://tempfile.redpandaai.co/kieai/{image_id}.png",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )


# --- enqueue_from_assets ---------------------------------------------------


async def test_enqueue_from_assets_happy_path(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    await images_store.upsert(_sample_image())
    await audios_store.upsert(_sample_audio())
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    job = await ctl.enqueue_from_assets(_uploaded_ref(), "aud-1", "Plano medio, luz suave")

    assert job.status == JobStatus.QUEUED
    assert job.prompt == "Plano medio, luz suave"
    assert job.image_url == "https://tempfile.redpandaai.co/kieai/img-1.png"
    assert job.audio_url == "https://tempfile.redpandaai.co/kieai/aud-1.mp3"
    assert job.script == "Hola, soy María"
    assert job.voice == "EkK5I93UQWFDigLMpZcX"
    fetched = await jobs_repo.get(job.id)
    assert fetched is not None
    await queue.drain()
    assert len(runner.processed) == 1
    assert runner.processed[0].id == job.id


async def test_enqueue_from_assets_rejects_missing_image(
    tmp_settings,
    jobs_repo: JobsDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    await audios_store.upsert(_sample_audio())
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    with pytest.raises(ImageNotFoundError):
        await ctl.enqueue_from_assets(_uploaded_ref("ghost"), "aud-1", "x")
    assert runner.processed == []


async def test_enqueue_from_assets_rejects_missing_audio(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    await images_store.upsert(_sample_image())
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    with pytest.raises(AudioNotFoundError):
        await ctl.enqueue_from_assets(_uploaded_ref(), "ghost", "x")
    assert runner.processed == []


async def test_enqueue_from_assets_rejects_expired_image(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    old = datetime.now(UTC) - timedelta(days=20)
    await images_store.upsert(_sample_image(uploaded_at=old))
    await audios_store.upsert(_sample_audio())
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    with pytest.raises(ImageExpiredError, match="expiró"):
        await ctl.enqueue_from_assets(_uploaded_ref(), "aud-1", "x")


async def test_enqueue_from_assets_rejects_expired_audio(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    old = datetime.now(UTC) - timedelta(days=20)
    await images_store.upsert(_sample_image())
    await audios_store.upsert(_sample_audio(generated_at=old))
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    with pytest.raises(AudioExpiredError, match="expiró"):
        await ctl.enqueue_from_assets(_uploaded_ref(), "aud-1", "x")


async def test_enqueue_from_assets_rejects_empty_prompt(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    await images_store.upsert(_sample_image())
    await audios_store.upsert(_sample_audio())
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    with pytest.raises(JobValidationError, match="prompt"):
        await ctl.enqueue_from_assets(_uploaded_ref(), "aud-1", "   ")


# --- wait_for_job + subscribe ----------------------------------------------


async def test_wait_for_job_resolves_on_terminal(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    await images_store.upsert(_sample_image())
    await audios_store.upsert(_sample_audio())
    runner = _RecordingRunner(target_status=JobStatus.COMPLETED)
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    job = await ctl.enqueue_from_assets(_uploaded_ref(), "aud-1", "p")
    terminal = await asyncio.wait_for(ctl.wait_for_job(job.id), timeout=2.0)

    assert terminal.id == job.id
    assert terminal.status == JobStatus.COMPLETED


async def test_subscribe_returns_unsubscribe(
    tmp_settings,
    jobs_repo: JobsDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)
    initial = len(queue._listeners)  # type: ignore[attr-defined]

    unsubscribe = ctl.subscribe(lambda _e: None)
    assert len(queue._listeners) == initial + 1  # type: ignore[attr-defined]
    unsubscribe()
    assert len(queue._listeners) == initial  # type: ignore[attr-defined]


# --- list/get/cancel/retry/delete -------------------------------------------


async def test_list_and_get_video_jobs(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    await images_store.upsert(_sample_image())
    await audios_store.upsert(_sample_audio())
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    job = await ctl.enqueue_from_assets(_uploaded_ref(), "aud-1", "p")
    await queue.drain()

    listed = await ctl.list_video_jobs()
    assert len(listed) == 1
    fetched = await ctl.get_video_job(job.id)
    assert fetched is not None
    assert fetched.id == job.id


async def test_delete_job_removes_from_repo(
    tmp_settings,
    jobs_repo: JobsDB,
    images_store: ImagesDB,
    audios_store: AudiosDB,
    catalog: ImageCatalogController,
) -> None:
    await images_store.upsert(_sample_image())
    await audios_store.upsert(_sample_audio())
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)

    job = await ctl.enqueue_from_assets(_uploaded_ref(), "aud-1", "p")
    await queue.drain()

    await ctl.delete_job(job.id)
    assert await jobs_repo.get(job.id) is None
