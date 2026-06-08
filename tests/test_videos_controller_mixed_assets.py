"""Tests del refactor a `ImageAssetRef`: videos pueden usar uploaded o generated.

Cobertura específica de hallazgos #1 y #2 del rubber-duck:
- Encolar video con `GeneratedImage` (no solo `UploadedImage`).
- Colisión de IDs entre uploaded y generated → discriminadas por `kind`.
- TTL distinto se aplica correctamente: 24h para uploaded, 14d para
  generated. Una generated de 10 días sigue siendo válida (no debería
  fallar como si fuese upload de 10 días).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kie_avatar_studio.app_layer.image_catalog_controller import ImageCatalogController
from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.video_job_lifecycle import VideoJobLifecycle
from kie_avatar_studio.app_layer.videos_controller import VideosController
from kie_avatar_studio.domain.errors import (
    GeneratedImageExpiredError,
    ImageExpiredError,
)
from kie_avatar_studio.domain.events import JobUpdated
from kie_avatar_studio.domain.models import (
    GeneratedAudio,
    GeneratedImage,
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
    def __init__(self) -> None:
        self.processed: list[VideoJob] = []

    async def run(self, job: VideoJob) -> VideoJob:
        self.processed.append(job)
        job.status = JobStatus.COMPLETED
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


def _sample_audio() -> GeneratedAudio:
    return GeneratedAudio(
        id="aud_x",
        label="audio",
        script="x",
        voice_id="vx",
        kie_url="https://tempfile.redpandaai.co/kieai/aud_x.mp3",
        kie_file_path="kieai/aud_x.mp3",
    )


def _build_controller(
    tmp_settings, jobs_repo, catalog, audios_store
) -> tuple[VideosController, _RecordingRunner, QueueManager[VideoJob, JobUpdated]]:
    runner = _RecordingRunner()
    queue: QueueManager[VideoJob, JobUpdated] = QueueManager(
        tmp_settings,
        runner,
        event_factory=JobUpdated,
        lifecycle=VideoJobLifecycle(jobs_repo),
    )
    ctl = VideosController(jobs_repo, catalog, audios_store, queue)
    return ctl, runner, queue


# --- usar GeneratedImage como input ---------------------------------------


async def test_enqueue_with_generated_image_succeeds(
    tmp_settings, jobs_repo, catalog, audios_store, generated_images_store
) -> None:
    """Una `GeneratedImage` no expirada debe poder usarse como imagen del video."""
    gen = GeneratedImage(
        id="gen_avatar",
        label="avatar generado",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/kieai/gen_avatar.png",
        kie_file_path="kieai/gen_avatar.png",
    )
    await generated_images_store.upsert(gen)
    await audios_store.upsert(_sample_audio())

    ctl, runner, queue = _build_controller(tmp_settings, jobs_repo, catalog, audios_store)
    ref = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id=gen.id,
        label=gen.label,
        kie_url=gen.kie_url,
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )

    job = await ctl.enqueue_from_assets(ref, "aud_x", "plano corto")
    assert job.image_url == gen.kie_url
    assert job.audio_url == "https://tempfile.redpandaai.co/kieai/aud_x.mp3"
    await queue.drain()
    assert runner.processed[0].image_url == gen.kie_url


# --- TTL correcto por kind ------------------------------------------------


async def test_generated_image_10_days_old_still_valid(
    tmp_settings, jobs_repo, catalog, audios_store, generated_images_store
) -> None:
    """Una imagen generada hace 10 días debe ser válida (TTL 14d), NO fallar
    como si fuera un upload (TTL 24h). Test crítico del DTO discriminado."""
    gen = GeneratedImage(
        id="gen_old_but_valid",
        label="x",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/kieai/gen_old.png",
        kie_file_path="kieai/gen_old.png",
        generated_at=datetime.now(UTC) - timedelta(days=10),
    )
    await generated_images_store.upsert(gen)
    await audios_store.upsert(_sample_audio())

    ctl, _runner, _queue = _build_controller(tmp_settings, jobs_repo, catalog, audios_store)
    ref = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id=gen.id,
        label=gen.label,
        kie_url=gen.kie_url,
        expires_at=datetime.now(UTC) + timedelta(days=4),
    )

    # No debe lanzar.
    job = await ctl.enqueue_from_assets(ref, "aud_x", "p")
    assert job.status == JobStatus.QUEUED


async def test_generated_image_expired_raises_typed_error(
    tmp_settings, jobs_repo, catalog, audios_store, generated_images_store
) -> None:
    gen = GeneratedImage(
        id="gen_dead",
        label="x",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/kieai/gen_dead.png",
        kie_file_path="kieai/gen_dead.png",
        generated_at=datetime.now(UTC) - timedelta(days=20),
    )
    await generated_images_store.upsert(gen)
    await audios_store.upsert(_sample_audio())

    ctl, _runner, _queue = _build_controller(tmp_settings, jobs_repo, catalog, audios_store)
    ref = ImageAssetRef(
        kind=ImageAssetKind.GENERATED,
        id=gen.id,
        label=gen.label,
        kie_url=gen.kie_url,
        expires_at=datetime.now(UTC) - timedelta(days=6),
    )

    with pytest.raises(GeneratedImageExpiredError, match="expiró"):
        await ctl.enqueue_from_assets(ref, "aud_x", "p")


async def test_uploaded_image_25_hours_old_expires(
    tmp_settings, jobs_repo, catalog, audios_store, images_store
) -> None:
    """TTL de upload es 24h: 25h debe fallar con ImageExpiredError."""
    img = UploadedImage(
        id="up_dead",
        label="x",
        local_path="/tmp/x.png",
        kie_url="https://tempfile.redpandaai.co/kieai/up_dead.png",
        kie_file_path="kieai/up_dead.png",
        file_size=100,
        mime_type="image/png",
        uploaded_at=datetime.now(UTC) - timedelta(hours=25),
    )
    await images_store.upsert(img)
    await audios_store.upsert(_sample_audio())

    ctl, _runner, _queue = _build_controller(tmp_settings, jobs_repo, catalog, audios_store)
    ref = ImageAssetRef(
        kind=ImageAssetKind.UPLOADED,
        id=img.id,
        label=img.label,
        kie_url=img.kie_url,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    with pytest.raises(ImageExpiredError, match="expiró"):
        await ctl.enqueue_from_assets(ref, "aud_x", "p")


# --- colisión de IDs entre stores -----------------------------------------


async def test_colliding_ids_resolved_correctly_by_kind(
    tmp_settings,
    jobs_repo,
    catalog,
    audios_store,
    images_store,
    generated_images_store,
) -> None:
    """Si por casualidad un id coincide entre uploaded y generated, el
    `kind` discrimina correctamente y cada uno apunta a su propia URL.
    """
    same_id = "shared_id"
    uploaded = UploadedImage(
        id=same_id,
        label="upload",
        local_path="/tmp/u.png",
        kie_url="https://a.example/u.png",
        kie_file_path="kieai/u.png",
        file_size=100,
        mime_type="image/png",
    )
    generated = GeneratedImage(
        id=same_id,
        label="generated",
        prompt="x",
        kie_url="https://a.example/g.png",
        kie_file_path="kieai/g.png",
    )
    await images_store.upsert(uploaded)
    await generated_images_store.upsert(generated)
    await audios_store.upsert(_sample_audio())

    ctl, _runner, _queue = _build_controller(tmp_settings, jobs_repo, catalog, audios_store)

    job_upload = await ctl.enqueue_from_assets(
        ImageAssetRef(
            kind=ImageAssetKind.UPLOADED,
            id=same_id,
            label=uploaded.label,
            kie_url=uploaded.kie_url,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        ),
        "aud_x",
        "p",
    )
    assert job_upload.image_url == "https://a.example/u.png"

    job_gen = await ctl.enqueue_from_assets(
        ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id=same_id,
            label=generated.label,
            kie_url=generated.kie_url,
            expires_at=datetime.now(UTC) + timedelta(days=14),
        ),
        "aud_x",
        "p",
    )
    assert job_gen.image_url == "https://a.example/g.png"
    # Ids de los VideoJob son distintos (cada enqueue genera id propio).
    assert job_upload.id != job_gen.id
