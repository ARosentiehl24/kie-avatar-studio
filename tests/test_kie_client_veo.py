from __future__ import annotations

import json

import httpx
import pytest

import kie_avatar_studio.infra.kie_client as kie_client_module
from kie_avatar_studio.domain.errors import KieClientError, KieServerError


async def test_create_veo_video_task_happy(
    tmp_settings,
    mock_transport_factory: callable,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/veo/generate"
        assert request.headers["authorization"] == f"Bearer {tmp_settings.kie_api_key}"
        assert json.loads(request.read()) == {
            "prompt": "avatar hablando",
            "model": "veo3_lite",
            "generationType": "REFERENCE_2_VIDEO",
            "aspect_ratio": "16:9",
            "resolution": "1080p",
            "duration": 8,
            "enableTranslation": False,
            "imageUrls": ["https://img/1.png", "https://img/2.png"],
            "watermark": "KIE",
        }
        return httpx.Response(200, json={"data": {"taskId": "veo_123"}}, request=request)

    from kie_avatar_studio.infra.kie_client import KieClient

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=mock_transport_factory(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        result = await client.create_veo_video_task(
            "avatar hablando",
            image_urls=["https://img/1.png", "https://img/2.png"],
            model="veo3_lite",
            generation_type="REFERENCE_2_VIDEO",
            aspect_ratio="16:9",
            resolution="1080p",
            duration=8,
            enable_translation=False,
            watermark="KIE",
        )
    finally:
        await client.aclose()

    assert result.task_id == "veo_123"
    assert len(captured) == 1


async def test_get_veo_task_detail_happy(mock_kie_client) -> None:
    client, captured = mock_kie_client

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/veo/record-info"
        assert request.url.params["taskId"] == "veo_123"
        return httpx.Response(
            200,
            json={
                "data": {"successFlag": 1, "response": {"resultUrls": ["https://cdn/video.mp4"]}}
            },
            request=request,
        )

    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-key"},
    )
    try:
        result = await client.get_veo_task_detail("veo_123")
    finally:
        await client.aclose()

    assert result["data"]["successFlag"] == 1
    assert result["data"]["response"]["resultUrls"] == ["https://cdn/video.mp4"]


async def test_create_veo_video_task_4xx_raises_client_error(
    tmp_settings,
    mock_transport_factory: callable,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"msg": "bad params"}, request=request)

    from kie_avatar_studio.infra.kie_client import KieClient

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=mock_transport_factory(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        with pytest.raises(KieClientError, match="HTTP 422"):
            await client.create_veo_video_task("avatar")
    finally:
        await client.aclose()


async def test_create_veo_video_task_5xx_retries_then_raises_server_error(
    tmp_settings,
    mock_transport_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kie_client_module, "_BACKOFF_BASE_SECONDS", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"msg": "busy"}, request=request)

    from kie_avatar_studio.infra.kie_client import KieClient

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=mock_transport_factory(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        with pytest.raises(KieServerError, match="HTTP 503"):
            await client.create_veo_video_task("avatar")
    finally:
        await client.aclose()

    assert calls["n"] == 3


async def test_get_veo_task_detail_timeout_retries_then_succeeds(
    tmp_settings,
    mock_transport_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kie_client_module, "_BACKOFF_BASE_SECONDS", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"data": {"successFlag": 0}}, request=request)

    from kie_avatar_studio.infra.kie_client import KieClient

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=mock_transport_factory(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        result = await client.get_veo_task_detail("veo_123")
    finally:
        await client.aclose()

    assert result == {"data": {"successFlag": 0}}
    assert calls["n"] == 3
