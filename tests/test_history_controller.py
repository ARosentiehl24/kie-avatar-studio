"""Tests del `HistoryController`: agregación + suscripción multi-queue (video + audio + image)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kie_avatar_studio.app_layer.audio_job_lifecycle import AudioJobLifecycle
from kie_avatar_studio.app_layer.history_controller import HistoryController
from kie_avatar_studio.app_layer.image_job_lifecycle import ImageJobLifecycle
from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.video_job_lifecycle import VideoJobLifecycle
from kie_avatar_studio.domain.events import (
    AudioJobUpdated,
    HistoryEntry,
    ImageJobUpdated,
    JobUpdated,
)
from kie_avatar_studio.domain.models import (
    AudioJob,
    AudioJobStatus,
    ImageJob,
    ImageJobStatus,
    JobStatus,
    VideoJob,
)
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB
from kie_avatar_studio.infra.db import JobsDB
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB


class _NoopVideoRunner:
    async def run(self, job: VideoJob) -> VideoJob:
        return job


class _NoopAudioRunner:
    async def run(self, job: AudioJob) -> AudioJob:
        return job


class _NoopImageRunner:
    async def run(self, job: ImageJob) -> ImageJob:
        return job


@pytest.fixture
async def video_repo(tmp_path) -> JobsDB:
    d = JobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def audio_repo(tmp_path) -> AudioJobsDB:
    d = AudioJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def image_repo(tmp_path) -> ImageJobsDB:
    d = ImageJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _video_queue(tmp_settings, repo: JobsDB) -> QueueManager[VideoJob, JobUpdated]:
    return QueueManager(
        tmp_settings,
        _NoopVideoRunner(),
        event_factory=JobUpdated,
        lifecycle=VideoJobLifecycle(repo),
    )


def _audio_queue(tmp_settings, repo: AudioJobsDB) -> QueueManager[AudioJob, AudioJobUpdated]:
    return QueueManager(
        tmp_settings,
        _NoopAudioRunner(),
        event_factory=AudioJobUpdated,
        lifecycle=AudioJobLifecycle(repo),
    )


def _image_queue(tmp_settings, repo: ImageJobsDB) -> QueueManager[ImageJob, ImageJobUpdated]:
    return QueueManager(
        tmp_settings,
        _NoopImageRunner(),
        event_factory=ImageJobUpdated,
        lifecycle=ImageJobLifecycle(repo),
    )


def _video_job(job_id: str, *, when: datetime | None = None) -> VideoJob:
    return VideoJob(
        id=job_id,
        script="hola mundo",
        image_path="/tmp/img.png",
        prompt="describe esto",
        voice="V1",
        status=JobStatus.COMPLETED,
        created_at=when or datetime.now(UTC),
    )


def _audio_job(job_id: str, *, when: datetime | None = None) -> AudioJob:
    return AudioJob(
        id=job_id,
        label="audio test",
        script="texto del audio",
        voice_id="V2",
        status=AudioJobStatus.COMPLETED,
        created_at=when or datetime.now(UTC),
    )


def _image_job(job_id: str, *, when: datetime | None = None) -> ImageJob:
    return ImageJob(
        id=job_id,
        label="image test",
        prompt="atardecer",
        status=ImageJobStatus.COMPLETED,
        created_at=when or datetime.now(UTC),
    )


def _build_ctl(tmp_settings, video_repo, audio_repo, image_repo) -> HistoryController:
    return HistoryController(
        video_repo,
        audio_repo,
        image_repo,
        _video_queue(tmp_settings, video_repo),
        _audio_queue(tmp_settings, audio_repo),
        _image_queue(tmp_settings, image_repo),
    )


# --- list_recent_entries ----------------------------------------------------


async def test_list_recent_entries_merges_three_kinds(
    tmp_settings, video_repo, audio_repo, image_repo
) -> None:
    await video_repo.upsert(_video_job("v1"))
    await audio_repo.upsert(_audio_job("a1"))
    await image_repo.upsert(_image_job("i1"))
    ctl = _build_ctl(tmp_settings, video_repo, audio_repo, image_repo)

    entries = await ctl.list_recent_entries()

    assert len(entries) == 3
    kinds = {e.kind for e in entries}
    assert kinds == {"video", "audio", "image"}


async def test_list_recent_entries_orders_by_created_at_desc(
    tmp_settings, video_repo, audio_repo, image_repo
) -> None:
    now = datetime.now(UTC)
    await video_repo.upsert(_video_job("v_old", when=now - timedelta(hours=3)))
    await audio_repo.upsert(_audio_job("a_mid", when=now - timedelta(hours=2)))
    await image_repo.upsert(_image_job("i_recent", when=now - timedelta(hours=1)))
    await video_repo.upsert(_video_job("v_new", when=now))
    ctl = _build_ctl(tmp_settings, video_repo, audio_repo, image_repo)

    entries = await ctl.list_recent_entries()

    assert [e.id for e in entries] == ["v_new", "i_recent", "a_mid", "v_old"]


async def test_list_recent_entries_handles_empty_stores(
    tmp_settings, video_repo, audio_repo, image_repo
) -> None:
    ctl = _build_ctl(tmp_settings, video_repo, audio_repo, image_repo)
    entries = await ctl.list_recent_entries()
    assert entries == []


# --- subscribe --------------------------------------------------------------


async def test_subscribe_dispatches_video_events(
    tmp_settings, video_repo, audio_repo, image_repo
) -> None:
    vq = _video_queue(tmp_settings, video_repo)
    aq = _audio_queue(tmp_settings, audio_repo)
    iq = _image_queue(tmp_settings, image_repo)
    ctl = HistoryController(video_repo, audio_repo, image_repo, vq, aq, iq)
    received: list[HistoryEntry] = []

    unsubscribe = ctl.subscribe(received.append)
    try:
        for listener in list(vq._listeners):  # type: ignore[attr-defined]
            listener(JobUpdated(_video_job("v1")))
    finally:
        unsubscribe()

    assert len(received) == 1
    assert received[0].kind == "video"
    assert received[0].id == "v1"


async def test_subscribe_dispatches_audio_events(
    tmp_settings, video_repo, audio_repo, image_repo
) -> None:
    vq = _video_queue(tmp_settings, video_repo)
    aq = _audio_queue(tmp_settings, audio_repo)
    iq = _image_queue(tmp_settings, image_repo)
    ctl = HistoryController(video_repo, audio_repo, image_repo, vq, aq, iq)
    received: list[HistoryEntry] = []

    unsubscribe = ctl.subscribe(received.append)
    try:
        for listener in list(aq._listeners):  # type: ignore[attr-defined]
            listener(AudioJobUpdated(_audio_job("a1")))
    finally:
        unsubscribe()

    assert len(received) == 1
    assert received[0].kind == "audio"
    assert received[0].id == "a1"


async def test_subscribe_dispatches_image_events(
    tmp_settings, video_repo, audio_repo, image_repo
) -> None:
    vq = _video_queue(tmp_settings, video_repo)
    aq = _audio_queue(tmp_settings, audio_repo)
    iq = _image_queue(tmp_settings, image_repo)
    ctl = HistoryController(video_repo, audio_repo, image_repo, vq, aq, iq)
    received: list[HistoryEntry] = []

    unsubscribe = ctl.subscribe(received.append)
    try:
        for listener in list(iq._listeners):  # type: ignore[attr-defined]
            listener(ImageJobUpdated(_image_job("i1")))
    finally:
        unsubscribe()

    assert len(received) == 1
    assert received[0].kind == "image"
    assert received[0].id == "i1"


async def test_subscribe_unsubscribe_removes_from_all_queues(
    tmp_settings, video_repo, audio_repo, image_repo
) -> None:
    """Unsubscribe debe sacar los listeners de las TRES queues atómicamente."""
    vq = _video_queue(tmp_settings, video_repo)
    aq = _audio_queue(tmp_settings, audio_repo)
    iq = _image_queue(tmp_settings, image_repo)
    ctl = HistoryController(video_repo, audio_repo, image_repo, vq, aq, iq)
    initial_v = len(vq._listeners)  # type: ignore[attr-defined]
    initial_a = len(aq._listeners)  # type: ignore[attr-defined]
    initial_i = len(iq._listeners)  # type: ignore[attr-defined]

    unsubscribe = ctl.subscribe(lambda _entry: None)
    assert len(vq._listeners) == initial_v + 1  # type: ignore[attr-defined]
    assert len(aq._listeners) == initial_a + 1  # type: ignore[attr-defined]
    assert len(iq._listeners) == initial_i + 1  # type: ignore[attr-defined]

    unsubscribe()
    assert len(vq._listeners) == initial_v  # type: ignore[attr-defined]
    assert len(aq._listeners) == initial_a  # type: ignore[attr-defined]
    assert len(iq._listeners) == initial_i  # type: ignore[attr-defined]
