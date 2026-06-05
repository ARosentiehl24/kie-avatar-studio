"""Tests de `ImageJobRunner`: state machine + persistencia + idempotencia + revalidación de refs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from kie_avatar_studio.app_layer.image_job_runner import ImageJobRunner
from kie_avatar_studio.domain.models import (
    GeneratedImage,
    ImageAssetKind,
    ImageAssetRef,
    ImageGenerationSettings,
    ImageJob,
    ImageJobStatus,
    UploadedImage,
)
from kie_avatar_studio.infra.generated_images_db import GeneratedImagesDB
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB
from kie_avatar_studio.infra.images_db import ImagesDB
from kie_avatar_studio.infra.kie_client import KieClient


def _client_with_handler(tmp_settings, handler) -> KieClient:
    client = KieClient(tmp_settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    return client


@pytest.fixture
async def jobs_repo(tmp_path) -> ImageJobsDB:
    d = ImageJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def generated_store(tmp_path) -> GeneratedImagesDB:
    d = GeneratedImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def uploaded_store(tmp_path) -> ImagesDB:
    d = ImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _success_handler() -> Any:
    state = {"phase": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if "createTask" in req.url.path:
            return httpx.Response(200, json={"data": {"taskId": "nb_t_1"}})
        state["phase"] += 1
        if state["phase"] < 2:
            return httpx.Response(200, json={"data": {"state": "running"}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "state": "success",
                    "resultJson": '{"resultUrls":["https://tempfile.redpandaai.co/kieai/x.png"]}',
                }
            },
        )

    return handler


def _make_job(**kwargs) -> ImageJob:
    base = {
        "id": "img_test_1",
        "label": "atardecer",
        "prompt": "un atardecer con palmeras",
    }
    base.update(kwargs)
    return ImageJob(**base)


# --- happy path ----------------------------------------------------------


async def test_run_happy_path_persists_completed(
    tmp_settings,
    jobs_repo: ImageJobsDB,
    generated_store: GeneratedImagesDB,
    uploaded_store: ImagesDB,
) -> None:
    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    client = _client_with_handler(tmp_settings, _success_handler())
    runner = ImageJobRunner(tmp_settings, client, jobs_repo, generated_store, uploaded_store)
    job = _make_job()
    await jobs_repo.upsert(job)

    result = await runner.run(job)

    assert result.status == ImageJobStatus.COMPLETED
    assert result.task_id == "nb_t_1"
    assert result.kie_url == "https://tempfile.redpandaai.co/kieai/x.png"
    assert result.kie_file_path == "kieai/x.png"
    assert result.error is None
    fetched_job = await jobs_repo.get(job.id)
    assert fetched_job is not None
    assert fetched_job.status == ImageJobStatus.COMPLETED
    fetched_image = await generated_store.get(job.id)
    assert fetched_image is not None
    assert fetched_image.kie_url == result.kie_url
    assert fetched_image.refs_count == 0
    await client.aclose()


async def test_run_sends_settings_in_payload(
    tmp_settings,
    jobs_repo: ImageJobsDB,
    generated_store: GeneratedImagesDB,
    uploaded_store: ImagesDB,
) -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if "createTask" in req.url.path:
            captured["body"] = json.loads(req.read())
            return httpx.Response(200, json={"data": {"taskId": "nb_t_settings"}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "state": "success",
                    "resultJson": '{"resultUrls":["https://tempfile.redpandaai.co/x.png"]}',
                }
            },
        )

    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    client = _client_with_handler(tmp_settings, handler)
    runner = ImageJobRunner(tmp_settings, client, jobs_repo, generated_store, uploaded_store)
    job = _make_job(
        settings_json=ImageGenerationSettings(
            aspect_ratio="16:9", resolution="2K", output_format="png"
        ).model_dump_json()
    )
    await jobs_repo.upsert(job)

    await runner.run(job)
    await client.aclose()

    body = captured["body"]
    assert body["model"] == "nano-banana-2"
    assert body["input"]["aspect_ratio"] == "16:9"
    assert body["input"]["resolution"] == "2K"
    assert body["input"]["output_format"] == "png"
    assert body["input"]["image_input"] == []


# --- validation failures -------------------------------------------------


async def test_run_marks_failed_on_invalid_prompt(
    tmp_settings,
    jobs_repo: ImageJobsDB,
    generated_store: GeneratedImagesDB,
    uploaded_store: ImagesDB,
) -> None:
    client = _client_with_handler(tmp_settings, lambda req: httpx.Response(500))
    runner = ImageJobRunner(tmp_settings, client, jobs_repo, generated_store, uploaded_store)
    job = _make_job(prompt="")  # vacío → ImageGenerationValidationError
    await jobs_repo.upsert(job)

    result = await runner.run(job)
    assert result.status == ImageJobStatus.FAILED
    assert "prompt" in (result.error or "")
    await client.aclose()


# --- refs revalidation (CRÍTICO) -----------------------------------------


async def test_run_fails_when_uploaded_ref_no_longer_in_store(
    tmp_settings,
    jobs_repo: ImageJobsDB,
    generated_store: GeneratedImagesDB,
    uploaded_store: ImagesDB,
) -> None:
    """Si una ref UPLOADED fue borrada del store después de encolar, el job debe fallar
    sin pegarle a Kie (evita 422 críptico)."""
    client = _client_with_handler(tmp_settings, lambda req: httpx.Response(500))
    runner = ImageJobRunner(tmp_settings, client, jobs_repo, generated_store, uploaded_store)
    refs = [
        ImageAssetRef(
            kind=ImageAssetKind.UPLOADED,
            id="ghost",
            label="ghost",
            kie_url="https://tempfile.redpandaai.co/ghost.png",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    ]
    job = _make_job(refs_json=json.dumps([r.model_dump(mode="json") for r in refs]))
    await jobs_repo.upsert(job)

    result = await runner.run(job)
    assert result.status == ImageJobStatus.FAILED
    assert "ghost" in (result.error or "")
    await client.aclose()


async def test_run_fails_when_uploaded_ref_expired_in_kie(
    tmp_settings,
    jobs_repo: ImageJobsDB,
    generated_store: GeneratedImagesDB,
    uploaded_store: ImagesDB,
) -> None:
    """Si una ref UPLOADED venció su TTL de 24h, el job debe fallar antes de Kie."""
    client = _client_with_handler(tmp_settings, lambda req: httpx.Response(500))
    runner = ImageJobRunner(tmp_settings, client, jobs_repo, generated_store, uploaded_store)
    # Ref en el store pero con uploaded_at en el pasado lejano → expirada.
    old_uploaded = UploadedImage(
        id="old_img",
        label="old",
        local_path="/tmp/old.png",
        kie_url="https://tempfile.redpandaai.co/old.png",
        kie_file_path="kieai/old.png",
        file_size=100,
        mime_type="image/png",
        uploaded_at=datetime.now(UTC) - timedelta(hours=48),
    )
    await uploaded_store.upsert(old_uploaded)
    refs = [
        ImageAssetRef(
            kind=ImageAssetKind.UPLOADED,
            id="old_img",
            label="old",
            kie_url=old_uploaded.kie_url,
            expires_at=datetime.now(UTC) - timedelta(hours=24),
        )
    ]
    job = _make_job(refs_json=json.dumps([r.model_dump(mode="json") for r in refs]))
    await jobs_repo.upsert(job)

    result = await runner.run(job)
    assert result.status == ImageJobStatus.FAILED
    assert "expiró" in (result.error or "")
    await client.aclose()


async def test_run_succeeds_with_fresh_generated_ref(
    tmp_settings,
    jobs_repo: ImageJobsDB,
    generated_store: GeneratedImagesDB,
    uploaded_store: ImagesDB,
) -> None:
    """Mix de generated ref fresh + happy path."""
    fresh_gen = GeneratedImage(
        id="gen_fresh",
        label="fresh",
        prompt="x",
        kie_url="https://tempfile.redpandaai.co/fresh.png",
        kie_file_path="kieai/fresh.png",
    )
    await generated_store.upsert(fresh_gen)
    refs = [
        ImageAssetRef(
            kind=ImageAssetKind.GENERATED,
            id="gen_fresh",
            label="fresh",
            kie_url=fresh_gen.kie_url,
            expires_at=datetime.now(UTC) + timedelta(days=14),
        )
    ]
    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    client = _client_with_handler(tmp_settings, _success_handler())
    runner = ImageJobRunner(tmp_settings, client, jobs_repo, generated_store, uploaded_store)
    job = _make_job(refs_json=json.dumps([r.model_dump(mode="json") for r in refs]))
    await jobs_repo.upsert(job)

    result = await runner.run(job)
    assert result.status == ImageJobStatus.COMPLETED
    stored = await generated_store.get(job.id)
    assert stored is not None
    assert stored.refs_count == 1
    await client.aclose()


# --- resume con task_id existente ----------------------------------------


async def test_resume_with_existing_task_id_skips_create(
    tmp_settings,
    jobs_repo: ImageJobsDB,
    generated_store: GeneratedImagesDB,
    uploaded_store: ImagesDB,
) -> None:
    """Job en POLLING con task_id ya no debe llamar createTask (no doble cobro)."""
    create_calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "createTask" in req.url.path:
            create_calls.append(req.url.path)
            return httpx.Response(500, text="should not be called")
        return httpx.Response(
            200,
            json={
                "data": {
                    "state": "success",
                    "resultJson": '{"resultUrls":["https://tempfile.redpandaai.co/r.png"]}',
                }
            },
        )

    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    client = _client_with_handler(tmp_settings, handler)
    runner = ImageJobRunner(tmp_settings, client, jobs_repo, generated_store, uploaded_store)
    job = _make_job(status=ImageJobStatus.POLLING, task_id="nb_existing")
    await jobs_repo.upsert(job)

    result = await runner.run(job)
    assert result.status == ImageJobStatus.COMPLETED
    assert result.task_id == "nb_existing"
    assert create_calls == []
    await client.aclose()
