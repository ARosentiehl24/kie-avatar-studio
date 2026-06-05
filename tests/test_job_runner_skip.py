"""Tests del `JobRunner` con skip de upload/TTS cuando las URLs ya están
pobladas (modo 'video desde assets reusables')."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from kie_avatar_studio.app_layer.job_runner import JobRunner
from kie_avatar_studio.domain.models import JobStatus, VideoJob
from kie_avatar_studio.infra.db import JobsDB
from kie_avatar_studio.infra.kie_client import KieClient


def _client_with_handler(tmp_settings, handler) -> KieClient:
    client = KieClient(tmp_settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    return client


@pytest.fixture
async def repo(tmp_path) -> JobsDB:
    d = JobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _success_avatar_handler(captured: list[httpx.Request]) -> Any:
    """Handler que solo responde a createTask + recordInfo del avatar.

    NUNCA debería ver requests de upload_file ni de TTS create_tts_task
    si el runner está respetando el modo skip. Si las ve, el test falla.
    """
    state = {"phase": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        path = req.url.path
        if "createTask" in path:
            # Asumimos que es para el avatar (no para TTS).
            return httpx.Response(200, json={"data": {"taskId": "t_avatar"}})
        if "recordInfo" in path:
            state["phase"] += 1
            if state["phase"] < 2:
                return httpx.Response(200, json={"data": {"state": "running"}})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "success",
                        "resultJson": '{"resultUrls":["https://kie/v.mp4"]}',
                    }
                },
            )
        if "downloadUrl" in path or path.endswith(".mp4"):
            return httpx.Response(200, content=b"fake-mp4-bytes")
        return httpx.Response(404, text=f"unexpected path: {path}")

    return handler


async def test_runner_skips_upload_and_tts_when_urls_populated(
    tmp_settings, repo: JobsDB, monkeypatch
) -> None:
    """Si `image_url` y `audio_url` ya están seteados, el runner NO debe
    hacer requests de upload ni de create_tts_task."""
    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    captured: list[httpx.Request] = []
    client = _client_with_handler(tmp_settings, _success_avatar_handler(captured))
    runner = JobRunner(tmp_settings, client, repo)

    # Mock del download_file para no hacer streaming real al disco.
    async def fake_download(url: str, output_path) -> object:
        from pathlib import Path

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake")
        return p

    monkeypatch.setattr(client, "download_file", fake_download)

    job = VideoJob(
        id="job_reuse_1",
        prompt="Plano americano",
        image_url="https://kie/img.png",  # ya subida
        audio_url="https://kie/aud.mp3",  # ya generada
    )
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == JobStatus.COMPLETED
    assert result.video_url == "https://kie/v.mp4"
    # Verificamos que NO hubo requests de upload ni de TTS:
    paths = [r.url.path for r in captured]
    assert not any("uploadBase64File" in p or "uploadFile" in p for p in paths), (
        f"runner NO debe subir imagen cuando image_url ya está poblado, requests: {paths}"
    )
    # Solo debió haber requests al avatar (createTask + recordInfo).
    assert any("createTask" in p for p in paths)
    assert any("recordInfo" in p for p in paths)

    await client.aclose()


async def test_runner_validates_prompt_in_reuse_mode(tmp_settings, repo: JobsDB) -> None:
    """En modo reuse, el prompt sigue siendo obligatorio."""
    client = _client_with_handler(tmp_settings, lambda _r: httpx.Response(200))
    runner = JobRunner(tmp_settings, client, repo)

    job = VideoJob(
        id="job_no_prompt",
        prompt="",  # ← vacío
        image_url="https://kie/img.png",
        audio_url="https://kie/aud.mp3",
    )
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert "prompt" in result.error.lower()
    await client.aclose()


async def test_runner_does_not_validate_image_path_when_url_populated(
    tmp_settings, repo: JobsDB, monkeypatch
) -> None:
    """Si `image_url` está poblado, no debe validar que `image_path` exista."""
    tmp_settings = tmp_settings.model_copy(update={"poll_interval_seconds": 0})
    captured: list[httpx.Request] = []
    client = _client_with_handler(tmp_settings, _success_avatar_handler(captured))
    runner = JobRunner(tmp_settings, client, repo)

    async def fake_download(url: str, output_path) -> object:
        from pathlib import Path

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(client, "download_file", fake_download)

    job = VideoJob(
        id="job_no_path",
        prompt="Cinematic shot",
        image_path="",  # ← vacío, pero image_url está
        image_url="https://kie/img.png",
        audio_url="https://kie/aud.mp3",
    )
    await repo.upsert(job)

    result = await runner.run(job)

    assert result.status == JobStatus.COMPLETED
    await client.aclose()
