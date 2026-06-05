"""Tests de `AudioJobRunner`: state machine + persistencia + idempotencia."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kie_avatar_studio.app_layer.audio_job_runner import AudioJobRunner
from kie_avatar_studio.domain.models import AudioJob, AudioJobStatus
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB
from kie_avatar_studio.infra.audios_db import AudiosDB
from kie_avatar_studio.infra.kie_client import KieClient


def _client_with_handler(tmp_settings, handler) -> KieClient:
    client = KieClient(tmp_settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    return client


@pytest.fixture
async def repo(tmp_path) -> AudioJobsDB:
    d = AudioJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def audio_store(tmp_path) -> AudiosDB:
    d = AudiosDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _success_handler() -> Any:
    state = {"phase": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "createTask" in req.url.path:
            return httpx.Response(200, json={"data": {"taskId": "t_new"}})
        state["phase"] += 1
        if state["phase"] < 2:
            return httpx.Response(200, json={"data": {"state": "running"}})
        # Shape real observado: data.state + resultJson string
        return httpx.Response(
            200,
            json={
                "data": {
                    "state": "success",
                    "resultJson": '{"resultUrls":["https://tempfile.redpandaai.co/kieai/abc.mp3"]}',
                }
            },
        )

    return handler


def _make_job(**kwargs) -> AudioJob:
    base = {
        "id": "aud_test_1",
        "label": "Demo",
        "script": "Hola mundo",
        "voice_id": "EkK5I93UQWFDigLMpZcX",
    }
    base.update(kwargs)
    return AudioJob(**base)


async def test_run_happy_path_persists_completed(
    tmp_settings, repo: AudioJobsDB, audio_store: AudiosDB
) -> None:
    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    client = _client_with_handler(tmp_settings, _success_handler())
    runner = AudioJobRunner(tmp_settings, client, repo, audio_store)
    job = _make_job()
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == AudioJobStatus.COMPLETED
    assert result.task_id == "t_new"
    assert result.kie_url == "https://tempfile.redpandaai.co/kieai/abc.mp3"
    assert result.kie_file_path == "kieai/abc.mp3"
    assert result.error is None
    # Persistido en audio_jobs y en generated_audios (mismo id).
    fetched_job = await repo.get(job.id)
    assert fetched_job is not None
    assert fetched_job.status == AudioJobStatus.COMPLETED
    fetched_audio = await audio_store.get(job.id)
    assert fetched_audio is not None
    assert fetched_audio.id == job.id
    assert fetched_audio.kie_url == result.kie_url
    await client.aclose()


async def test_run_rejects_invalid_script(
    tmp_settings, repo: AudioJobsDB, audio_store: AudiosDB
) -> None:
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    runner = AudioJobRunner(tmp_settings, client, repo, audio_store)
    job = _make_job(script="")
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == AudioJobStatus.FAILED
    assert result.error is not None
    assert "script" in result.error.lower()
    # No debe haber creado nada en generated_audios.
    assert await audio_store.get(job.id) is None
    await client.aclose()


async def test_run_marks_failed_on_4xx(
    tmp_settings, repo: AudioJobsDB, audio_store: AudiosDB
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = _client_with_handler(tmp_settings, handler)
    runner = AudioJobRunner(tmp_settings, client, repo, audio_store)
    job = _make_job()
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == AudioJobStatus.FAILED
    assert result.error is not None
    assert await audio_store.get(job.id) is None
    await client.aclose()


async def test_run_marks_failed_on_5xx(
    tmp_settings, repo: AudioJobsDB, audio_store: AudiosDB, monkeypatch
) -> None:
    monkeypatch.setattr("kie_avatar_studio.infra.kie_client._BACKOFF_BASE_SECONDS", 0.0)

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    client = _client_with_handler(tmp_settings, handler)
    runner = AudioJobRunner(tmp_settings, client, repo, audio_store)
    job = _make_job()
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == AudioJobStatus.FAILED
    await client.aclose()


async def test_run_resumes_existing_task_id_without_recreating(
    tmp_settings, repo: AudioJobsDB, audio_store: AudiosDB
) -> None:
    """Si el job ya tiene `task_id` (resume), reusamos el task; no creamos nuevo."""
    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    create_calls: list[httpx.Request] = []
    record_calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "createTask" in req.url.path:
            create_calls.append(req)
            return httpx.Response(200, json={"data": {"taskId": "t_new"}})
        record_calls.append(req)
        return httpx.Response(
            200,
            json={
                "data": {
                    "state": "success",
                    "resultJson": '{"resultUrls":["https://tempfile.redpandaai.co/kieai/x.mp3"]}',
                }
            },
        )

    client = _client_with_handler(tmp_settings, handler)
    runner = AudioJobRunner(tmp_settings, client, repo, audio_store)
    job = _make_job(task_id="t_existing", status=AudioJobStatus.POLLING)
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == AudioJobStatus.COMPLETED
    assert result.task_id == "t_existing"
    assert len(create_calls) == 0, "no debe re-crear el task si ya hay task_id"
    assert len(record_calls) >= 1
    await client.aclose()


async def test_run_idempotent_on_completed_audio(
    tmp_settings, repo: AudioJobsDB, audio_store: AudiosDB
) -> None:
    """Reintentar un job no duplica el `GeneratedAudio` (mismo id, upsert)."""
    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    client = _client_with_handler(tmp_settings, _success_handler())
    runner = AudioJobRunner(tmp_settings, client, repo, audio_store)
    job = _make_job()
    await repo.upsert(job)

    await runner.run(job)

    # Reset el job al estado QUEUED para simular un retry post-failure.
    job.status = AudioJobStatus.QUEUED
    job.task_id = None
    await repo.upsert(job)

    # Mismo handler (resetea state interno via closure → no compartible).
    # Para esta segunda corrida creamos otro handler con éxito desde el inicio.
    client2 = _client_with_handler(tmp_settings, _success_handler())
    runner2 = AudioJobRunner(tmp_settings, client2, repo, audio_store)
    await runner2.run(job)

    listed = await audio_store.list_recent()
    assert len(listed) == 1, "el reintento upsertea sobre el mismo id (no duplica)"
    assert listed[0].id == job.id
    await client.aclose()
    await client2.aclose()
