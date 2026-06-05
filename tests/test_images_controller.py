from pathlib import Path

import httpx
import pytest

from kie_avatar_studio.app_layer.images_controller import ImagesController
from kie_avatar_studio.domain.errors import ImageValidationError, KieClientError
from kie_avatar_studio.infra.images_db import ImagesDB
from kie_avatar_studio.infra.kie_client import KieClient


def _client_with_handler(tmp_settings, handler) -> KieClient:
    client = KieClient(tmp_settings)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    return client


def _png(tmp_path: Path, name: str = "modelo.png", size: int = 1024) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (size - 8))
    return p


@pytest.fixture
async def store(tmp_path: Path) -> ImagesDB:
    d = ImagesDB(tmp_path / "jobs.db")
    await d.init()
    return d


async def test_upload_happy_path(tmp_path: Path, tmp_settings, store: ImagesDB) -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(
            200,
            json={
                "data": {
                    "fileName": "modelo.png",
                    "filePath": "kieai/modelo.png",
                    "downloadUrl": "https://tempfile.redpandaai.co/modelo.png",
                    "fileSize": 1024,
                    "mimeType": "image/png",
                }
            },
        )

    client = _client_with_handler(tmp_settings, handler)
    ctl = ImagesController(store, client)
    img = await ctl.upload(_png(tmp_path), "modelo principal")
    assert img.id == "modelo_principal"
    assert img.kie_url == "https://tempfile.redpandaai.co/modelo.png"
    assert img.file_size == 1024
    listed = await ctl.list_uploaded()
    assert len(listed) == 1
    assert len(captured) == 1
    await client.aclose()


async def test_upload_validates_path(tmp_path: Path, tmp_settings, store: ImagesDB) -> None:
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client)
    with pytest.raises(ImageValidationError, match="no encontrada"):
        await ctl.upload(tmp_path / "no-existe.png", "x")
    await client.aclose()


async def test_upload_rejects_empty_label(tmp_path: Path, tmp_settings, store: ImagesDB) -> None:
    client = _client_with_handler(tmp_settings, lambda r: httpx.Response(200))
    ctl = ImagesController(store, client)
    with pytest.raises(ImageValidationError, match="label"):
        await ctl.upload(_png(tmp_path), "   ")
    await client.aclose()


async def test_upload_propagates_4xx_from_kie(
    tmp_path: Path, tmp_settings, store: ImagesDB
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"error": "too big"})

    client = _client_with_handler(tmp_settings, handler)
    ctl = ImagesController(store, client)
    with pytest.raises(KieClientError, match="413"):
        await ctl.upload(_png(tmp_path), "modelo")
    # nada quedó persistido
    assert await ctl.list_uploaded() == []
    await client.aclose()


async def test_upload_propagates_5xx_after_retries(
    tmp_path: Path, tmp_settings, store: ImagesDB, monkeypatch
) -> None:
    # Bajamos el backoff a 0 para que el test sea instantáneo.
    monkeypatch.setattr("kie_avatar_studio.infra.kie_client._BACKOFF_BASE_SECONDS", 0.0)
    attempts = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, text="upstream down")

    client = _client_with_handler(tmp_settings, handler)
    ctl = ImagesController(store, client)
    from kie_avatar_studio.domain.errors import KieServerError

    with pytest.raises(KieServerError):
        await ctl.upload(_png(tmp_path), "modelo")
    assert attempts["n"] >= 3  # se reintentó
    await client.aclose()


async def test_delete_removes_from_store(tmp_path: Path, tmp_settings, store: ImagesDB) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "fileName": "x.png",
                    "filePath": "kieai/x.png",
                    "downloadUrl": "https://x",
                    "fileSize": 1,
                    "mimeType": "image/png",
                }
            },
        )

    client = _client_with_handler(tmp_settings, handler)
    ctl = ImagesController(store, client)
    img = await ctl.upload(_png(tmp_path), "modelo")
    await ctl.delete(img.id)
    assert await ctl.list_uploaded() == []
    await client.aclose()
