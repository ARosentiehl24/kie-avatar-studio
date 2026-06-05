"""Tests de `ImageJobsDB` (persistencia de jobs de generación de imagen)."""

from __future__ import annotations

import pytest

from kie_avatar_studio.domain.models import ImageJob, ImageJobStatus
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB


@pytest.fixture
async def db(tmp_path) -> ImageJobsDB:
    d = ImageJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _sample_job(job_id: str = "img_test_1", **kwargs) -> ImageJob:
    base = {
        "id": job_id,
        "label": "Paisaje",
        "prompt": "un atardecer con palmeras",
    }
    base.update(kwargs)
    return ImageJob(**base)


async def test_init_creates_table_idempotent(tmp_path) -> None:
    d = ImageJobsDB(tmp_path / "jobs.db")
    await d.init()
    await d.init()  # No debe romper si se llama dos veces.
    assert await d.list_recent() == []


async def test_upsert_and_get_roundtrip(db: ImageJobsDB) -> None:
    job = _sample_job(
        settings_json='{"aspect_ratio":"16:9","resolution":"2K","output_format":"png"}',
        refs_json='[{"kind":"uploaded","id":"u1","label":"u","kie_url":"https://x.com/u.png",'
        '"expires_at":"2026-06-06T12:00:00+00:00"}]',
    )
    await db.upsert(job)
    fetched = await db.get(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.label == "Paisaje"
    assert fetched.prompt == "un atardecer con palmeras"
    assert '"aspect_ratio":"16:9"' in (fetched.settings_json or "")
    assert '"kind":"uploaded"' in (fetched.refs_json or "")
    assert fetched.status == ImageJobStatus.QUEUED
    assert fetched.task_id is None
    assert fetched.kie_url is None


async def test_upsert_updates_existing(db: ImageJobsDB) -> None:
    job = _sample_job()
    await db.upsert(job)
    job.status = ImageJobStatus.POLLING
    job.task_id = "nb_task_1"
    await db.upsert(job)
    fetched = await db.get(job.id)
    assert fetched is not None
    assert fetched.status == ImageJobStatus.POLLING
    assert fetched.task_id == "nb_task_1"


async def test_list_recent_orders_desc(db: ImageJobsDB) -> None:
    j1 = _sample_job(job_id="img_a", label="primero")
    await db.upsert(j1)
    j2 = _sample_job(job_id="img_b", label="segundo")
    await db.upsert(j2)
    listed = await db.list_recent()
    # El más reciente primero (j2 se insertó después).
    assert [j.id for j in listed] == ["img_b", "img_a"]


async def test_list_by_status_filters(db: ImageJobsDB) -> None:
    queued = _sample_job(job_id="img_q")
    polling = _sample_job(job_id="img_p", status=ImageJobStatus.POLLING)
    failed = _sample_job(job_id="img_f", status=ImageJobStatus.FAILED, error="boom")
    for j in (queued, polling, failed):
        await db.upsert(j)

    listed_polling = await db.list_by_status(ImageJobStatus.POLLING)
    assert [j.id for j in listed_polling] == ["img_p"]

    listed_failed = await db.list_by_status(ImageJobStatus.FAILED)
    assert [j.id for j in listed_failed] == ["img_f"]
    assert listed_failed[0].error == "boom"


async def test_delete_removes_row(db: ImageJobsDB) -> None:
    job = _sample_job()
    await db.upsert(job)
    await db.delete(job.id)
    assert await db.get(job.id) is None


async def test_delete_missing_is_silent(db: ImageJobsDB) -> None:
    # Idempotente: borrar un id inexistente no debe lanzar.
    await db.delete("never_existed")


async def test_get_returns_none_when_missing(db: ImageJobsDB) -> None:
    assert await db.get("nope") is None
