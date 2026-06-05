"""Tests del `BatchController`: orquestación de scan + enqueue + resumen.

No tocan filesystem real para que sean rápidos: el `scan_loader` se
inyecta como callable que devuelve la lista que cada test arma a mano.
La integración real (loader → scan_batch_dir) está cubierta por
`test_batch_loader.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kie_avatar_studio.app_layer.batch_controller import BatchController, BatchEnqueueResult
from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.video_job_lifecycle import VideoJobLifecycle
from kie_avatar_studio.app_layer.videos_controller import VideosController
from kie_avatar_studio.domain.errors import JobValidationError
from kie_avatar_studio.domain.events import JobUpdated
from kie_avatar_studio.domain.models import BatchEntry, JobStatus, VideoJob
from kie_avatar_studio.infra.audios_db import AudiosDB
from kie_avatar_studio.infra.db import JobsDB
from kie_avatar_studio.infra.images_db import ImagesDB


class _RecordingRunner:
    def __init__(self) -> None:
        self.processed: list[VideoJob] = []

    async def run(self, job: VideoJob) -> VideoJob:
        self.processed.append(job)
        job.status = JobStatus.COMPLETED
        return job


def _make_image(path: Path) -> Path:
    """PNG mínimo válido (no vacío, extensión soportada)."""
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)
    return path


@pytest.fixture
async def videos_controller(tmp_settings, tmp_path) -> VideosController:
    repo = JobsDB(tmp_path / "jobs.db")
    await repo.init()
    images = ImagesDB(tmp_path / "jobs.db")
    await images.init()
    audios = AudiosDB(tmp_path / "jobs.db")
    await audios.init()
    queue: QueueManager[VideoJob, JobUpdated] = QueueManager(
        tmp_settings,
        _RecordingRunner(),
        event_factory=JobUpdated,
        lifecycle=VideoJobLifecycle(repo),
    )
    return VideosController(repo, images, audios, queue)


def _valid_entry(name: str, tmp_path: Path) -> BatchEntry:
    img = _make_image(tmp_path / f"{name}.png")
    return BatchEntry(
        name=name,
        path=tmp_path / name,
        script="hola mundo",
        image_path=img,
        prompt="prompt",
        voice="VOICE_X",
    )


def _invalid_entry(name: str, tmp_path: Path) -> BatchEntry:
    return BatchEntry(
        name=name,
        path=tmp_path / name,
        script="",
        image_path=None,
        prompt="",
        voice="",
        errors=["falta script.txt", "falta modelo.<png>"],
    )


async def test_list_entries_caches_until_refresh(
    videos_controller: VideosController, tmp_path: Path
) -> None:
    calls = {"n": 0}

    async def loader() -> list[BatchEntry]:
        calls["n"] += 1
        return [_valid_entry("v1", tmp_path)]

    ctl = BatchController(scan_loader=loader, videos_controller=videos_controller)
    await ctl.list_entries()
    await ctl.list_entries()
    assert calls["n"] == 1
    await ctl.list_entries(refresh=True)
    assert calls["n"] == 2


async def test_enqueue_entry_valid_creates_video_job(
    videos_controller: VideosController, tmp_path: Path
) -> None:
    entry = _valid_entry("v1", tmp_path)

    async def loader() -> list[BatchEntry]:
        return [entry]

    ctl = BatchController(scan_loader=loader, videos_controller=videos_controller)
    job = await ctl.enqueue_entry(entry)
    assert job.status == JobStatus.QUEUED
    assert job.script == "hola mundo"
    assert job.image_path == str(entry.image_path)
    assert job.voice == "VOICE_X"


async def test_enqueue_entry_invalid_raises(
    videos_controller: VideosController, tmp_path: Path
) -> None:
    entry = _invalid_entry("bad", tmp_path)

    async def loader() -> list[BatchEntry]:
        return [entry]

    ctl = BatchController(scan_loader=loader, videos_controller=videos_controller)
    with pytest.raises(JobValidationError) as exc:
        await ctl.enqueue_entry(entry)
    assert "bad" in str(exc.value)


async def test_enqueue_all_valid_skips_invalid_and_counts(
    videos_controller: VideosController, tmp_path: Path
) -> None:
    valid_a = _valid_entry("a", tmp_path)
    valid_b = _valid_entry("b", tmp_path)
    invalid = _invalid_entry("bad", tmp_path)

    async def loader() -> list[BatchEntry]:
        return [valid_a, invalid, valid_b]

    ctl = BatchController(scan_loader=loader, videos_controller=videos_controller)
    result = await ctl.enqueue_all_valid()
    assert isinstance(result, BatchEnqueueResult)
    assert len(result.enqueued_ids) == 2
    assert result.skipped_invalid == 1
    assert result.errors == []


async def test_enqueue_all_valid_records_partial_failures(
    videos_controller: VideosController, tmp_path: Path
) -> None:
    """Si una entry pasa la validación de loader pero falla en
    `enqueue_from_scratch` (imagen demasiado grande, etc.), el error
    se acumula sin interrumpir el resto del lote."""
    good = _valid_entry("ok", tmp_path)
    # Entry con image_path apuntando a un archivo que NO existe — pasa
    # como "valid" del loader (errors vacío) pero falla en
    # validate_image_path al encolar.
    bad = BatchEntry(
        name="will_fail",
        path=tmp_path / "will_fail",
        script="script",
        image_path=tmp_path / "nope.png",
        prompt="prompt",
        voice="VOICE_X",
    )

    async def loader() -> list[BatchEntry]:
        return [good, bad]

    ctl = BatchController(scan_loader=loader, videos_controller=videos_controller)
    result = await ctl.enqueue_all_valid()
    assert len(result.enqueued_ids) == 1
    assert len(result.errors) == 1
    assert result.errors[0][0] == "will_fail"
    assert result.skipped_invalid == 0


async def test_enqueue_all_valid_with_empty_list_returns_empty_summary(
    videos_controller: VideosController,
) -> None:
    async def loader() -> list[BatchEntry]:
        return []

    ctl = BatchController(scan_loader=loader, videos_controller=videos_controller)
    result = await ctl.enqueue_all_valid()
    assert result.enqueued_ids == []
    assert result.errors == []
    assert result.skipped_invalid == 0
    assert result.total_attempted == 0
