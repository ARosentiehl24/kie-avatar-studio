"""Tests de `AudioJobLifecycle`: reglas de cancellable/retryable + persistencia."""

from __future__ import annotations

import pytest

from kie_avatar_studio.app_layer.audio_job_lifecycle import AudioJobLifecycle
from kie_avatar_studio.domain.models import AudioJob, AudioJobStatus
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB


@pytest.fixture
async def repo(tmp_path) -> AudioJobsDB:
    d = AudioJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _job(status: AudioJobStatus, **extras) -> AudioJob:
    base: dict = {
        "id": "aud_1",
        "label": "X",
        "script": "Hola",
        "voice_id": "EkK5I93UQWFDigLMpZcX",
        "status": status,
    }
    base.update(extras)
    return AudioJob(**base)


async def test_cancellable_states(repo: AudioJobsDB) -> None:
    lifecycle = AudioJobLifecycle(repo)
    for s in (
        AudioJobStatus.QUEUED,
        AudioJobStatus.VALIDATING,
        AudioJobStatus.CREATING,
        AudioJobStatus.POLLING,
    ):
        assert lifecycle.is_cancellable(_job(s)) is True


async def test_non_cancellable_terminal_states(repo: AudioJobsDB) -> None:
    lifecycle = AudioJobLifecycle(repo)
    for s in (AudioJobStatus.COMPLETED, AudioJobStatus.FAILED, AudioJobStatus.CANCELLED):
        assert lifecycle.is_cancellable(_job(s)) is False


async def test_retryable_only_from_failed_or_cancelled(repo: AudioJobsDB) -> None:
    lifecycle = AudioJobLifecycle(repo)
    assert lifecycle.is_retryable(_job(AudioJobStatus.FAILED)) is True
    assert lifecycle.is_retryable(_job(AudioJobStatus.CANCELLED)) is True
    for s in (
        AudioJobStatus.QUEUED,
        AudioJobStatus.POLLING,
        AudioJobStatus.COMPLETED,
    ):
        assert lifecycle.is_retryable(_job(s)) is False


async def test_mark_cancelled_persists_before_returning(repo: AudioJobsDB) -> None:
    lifecycle = AudioJobLifecycle(repo)
    job = _job(AudioJobStatus.POLLING, task_id="t_xyz")
    await repo.upsert(job)

    await lifecycle.mark_cancelled(job)

    assert job.status == AudioJobStatus.CANCELLED
    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.status == AudioJobStatus.CANCELLED


async def test_reset_for_retry_clears_error_and_task_id(repo: AudioJobsDB) -> None:
    lifecycle = AudioJobLifecycle(repo)
    job = _job(AudioJobStatus.FAILED, task_id="t_old", error="timeout")
    await repo.upsert(job)

    await lifecycle.reset_for_retry(job)

    assert job.status == AudioJobStatus.QUEUED
    assert job.error is None
    assert job.task_id is None
    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.status == AudioJobStatus.QUEUED
    assert fetched.task_id is None
