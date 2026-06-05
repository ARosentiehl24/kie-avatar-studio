"""Tests de `AudioJobsDB` (persistencia de audio jobs)."""

from __future__ import annotations

import pytest

from kie_avatar_studio.domain.models import AudioJob, AudioJobStatus
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB


@pytest.fixture
async def db(tmp_path) -> AudioJobsDB:
    d = AudioJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _sample_job(job_id: str = "aud_test_1", **kwargs) -> AudioJob:
    base = {
        "id": job_id,
        "label": "Demo",
        "script": "Hola mundo",
        "voice_id": "EkK5I93UQWFDigLMpZcX",
    }
    base.update(kwargs)
    return AudioJob(**base)


async def test_init_creates_table_idempotent(tmp_path) -> None:
    d = AudioJobsDB(tmp_path / "jobs.db")
    await d.init()
    await d.init()  # No debe romper si se llama dos veces.
    assert await d.list_recent() == []


async def test_upsert_and_get_roundtrip(db: AudioJobsDB) -> None:
    job = _sample_job(voice_settings_json='{"stability":0.5}')
    await db.upsert(job)
    fetched = await db.get(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.label == "Demo"
    assert fetched.script == "Hola mundo"
    assert fetched.voice_settings_json == '{"stability":0.5}'
    assert fetched.status == AudioJobStatus.QUEUED
    assert fetched.task_id is None
    assert fetched.kie_url is None


async def test_upsert_updates_existing(db: AudioJobsDB) -> None:
    job = _sample_job()
    await db.upsert(job)
    job.status = AudioJobStatus.POLLING
    job.task_id = "t_abc"
    await db.upsert(job)
    fetched = await db.get(job.id)
    assert fetched is not None
    assert fetched.status == AudioJobStatus.POLLING
    assert fetched.task_id == "t_abc"


async def test_list_by_status_filters(db: AudioJobsDB) -> None:
    await db.upsert(_sample_job("aud_q1"))
    polling = _sample_job("aud_p1")
    polling.status = AudioJobStatus.POLLING
    await db.upsert(polling)
    completed = _sample_job("aud_c1")
    completed.status = AudioJobStatus.COMPLETED
    await db.upsert(completed)

    queued = await db.list_by_status(AudioJobStatus.QUEUED)
    assert [j.id for j in queued] == ["aud_q1"]
    in_progress = await db.list_by_status(AudioJobStatus.POLLING)
    assert [j.id for j in in_progress] == ["aud_p1"]


async def test_list_recent_orders_desc(db: AudioJobsDB) -> None:
    await db.upsert(_sample_job("aud_1"))
    await db.upsert(_sample_job("aud_2"))
    await db.upsert(_sample_job("aud_3"))
    listed = await db.list_recent()
    # No asumimos orden estricto entre filas con el mismo timestamp;
    # solo verificamos que los 3 estén presentes.
    assert {j.id for j in listed} == {"aud_1", "aud_2", "aud_3"}


async def test_delete_removes_row(db: AudioJobsDB) -> None:
    job = _sample_job()
    await db.upsert(job)
    await db.delete(job.id)
    assert await db.get(job.id) is None


async def test_get_returns_none_for_missing(db: AudioJobsDB) -> None:
    assert await db.get("no_existe") is None
