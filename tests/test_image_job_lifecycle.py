"""Tests de `ImageJobLifecycle`: política de cancel/retry + persistencia."""

from __future__ import annotations

import pytest

from kie_avatar_studio.app_layer.image_job_lifecycle import ImageJobLifecycle
from kie_avatar_studio.domain.models import ImageJob, ImageJobStatus
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB


@pytest.fixture
async def repo(tmp_path) -> ImageJobsDB:
    d = ImageJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _job(status: ImageJobStatus = ImageJobStatus.QUEUED) -> ImageJob:
    return ImageJob(id="img_lc", label="x", prompt="x", status=status)


def test_is_cancellable_for_non_terminal(repo: ImageJobsDB) -> None:
    lc = ImageJobLifecycle(repo)
    for status in (
        ImageJobStatus.QUEUED,
        ImageJobStatus.VALIDATING,
        ImageJobStatus.CREATING,
        ImageJobStatus.POLLING,
    ):
        assert lc.is_cancellable(_job(status=status)) is True


def test_is_not_cancellable_for_terminal(repo: ImageJobsDB) -> None:
    lc = ImageJobLifecycle(repo)
    for status in (
        ImageJobStatus.COMPLETED,
        ImageJobStatus.FAILED,
        ImageJobStatus.CANCELLED,
    ):
        assert lc.is_cancellable(_job(status=status)) is False


def test_is_retryable_only_from_failed_or_cancelled(repo: ImageJobsDB) -> None:
    lc = ImageJobLifecycle(repo)
    assert lc.is_retryable(_job(status=ImageJobStatus.FAILED))
    assert lc.is_retryable(_job(status=ImageJobStatus.CANCELLED))
    assert not lc.is_retryable(_job(status=ImageJobStatus.COMPLETED))
    assert not lc.is_retryable(_job(status=ImageJobStatus.QUEUED))


async def test_mark_cancelled_persists(repo: ImageJobsDB) -> None:
    lc = ImageJobLifecycle(repo)
    job = _job(status=ImageJobStatus.POLLING)
    await repo.upsert(job)
    await lc.mark_cancelled(job)
    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.status == ImageJobStatus.CANCELLED


async def test_reset_for_retry_clears_task_id_and_error(repo: ImageJobsDB) -> None:
    lc = ImageJobLifecycle(repo)
    job = _job(status=ImageJobStatus.FAILED)
    job.task_id = "old_task"
    job.error = "previous failure"
    await repo.upsert(job)
    await lc.reset_for_retry(job)
    fetched = await repo.get(job.id)
    assert fetched is not None
    assert fetched.status == ImageJobStatus.QUEUED
    # task_id se limpia para que el retry cree un task nuevo en Kie (sino
    # se podría caer sobre un task expirado).
    assert fetched.task_id is None
    assert fetched.error is None
