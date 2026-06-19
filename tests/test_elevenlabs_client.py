from __future__ import annotations

import httpx
import pytest

import kie_avatar_studio.infra.elevenlabs_client as elevenlabs_module
from kie_avatar_studio.domain.errors import (
    ElevenLabsClientError,
    ElevenLabsInsufficientCreditsError,
    ElevenLabsServerError,
)
from kie_avatar_studio.infra.elevenlabs_client import ElevenLabsClient


async def _build_client(api_key: str, transport: httpx.MockTransport) -> ElevenLabsClient:
    client = ElevenLabsClient(api_key)
    await client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://api.elevenlabs.io",
        transport=transport,
        headers={"xi-api-key": api_key},
    )
    return client


async def test_list_voices_happy(mock_transport_factory: callable) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert request.url.path == "/v2/voices"
        assert request.url.params["page_size"] == "100"
        assert request.url.params["voice_type"] == "professional"
        assert request.url.params["search"] == "ana"
        return httpx.Response(
            200,
            json={"voices": [{"voice_id": "v_1", "name": "Ana"}]},
            request=request,
        )

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        voices = await client.list_voices(voice_type="professional", search="ana")
    finally:
        await client.aclose()

    assert voices == [{"voice_id": "v_1", "name": "Ana"}]
    assert captured[0].headers["xi-api-key"] == "el-key"


async def test_speech_to_speech_happy(mock_transport_factory: callable) -> None:
    audio = b"fake-audio"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/speech-to-speech/voice_123"
        assert request.url.params["output_format"] == "aac_44100"
        body = request.read()
        assert b'name="model_id"' in body
        assert b"custom-model" in body
        assert b'name="remove_background_noise"' in body
        assert b"true" in body
        assert b'name="audio"; filename="audio.mp3"' in body
        assert audio in body
        return httpx.Response(200, content=b"converted-audio", request=request)

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        result = await client.speech_to_speech(
            "voice_123",
            audio,
            model_id="custom-model",
            remove_background_noise=True,
            output_format="aac_44100",
        )
    finally:
        await client.aclose()

    assert result == b"converted-audio"


async def test_list_models_happy(mock_transport_factory: callable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json=[{"model_id": "m_1"}], request=request)

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        models = await client.list_models()
    finally:
        await client.aclose()

    assert models == [{"model_id": "m_1"}]


async def test_list_voices_402_raises_insufficient_credits(
    mock_transport_factory: callable,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={"detail": {"message": "insufficient credits"}},
            request=request,
        )

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        with pytest.raises(ElevenLabsInsufficientCreditsError, match="insufficient credits"):
            await client.list_voices()
    finally:
        await client.aclose()


async def test_list_models_404_raises_client_error(mock_transport_factory: callable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "missing"}, request=request)

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        with pytest.raises(ElevenLabsClientError, match="HTTP 404"):
            await client.list_models()
    finally:
        await client.aclose()


async def test_list_voices_429_retries_then_succeeds(
    mock_transport_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(elevenlabs_module, "_BACKOFF_BASE_SECONDS", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"detail": "slow down"}, request=request)
        return httpx.Response(200, json={"voices": [{"voice_id": "ok"}]}, request=request)

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        voices = await client.list_voices()
    finally:
        await client.aclose()

    assert voices == [{"voice_id": "ok"}]
    assert calls["n"] == 3


async def test_list_models_500_retries_then_raises_server_error(
    mock_transport_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(elevenlabs_module, "_BACKOFF_BASE_SECONDS", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"detail": "boom"}, request=request)

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        with pytest.raises(ElevenLabsServerError, match="HTTP 500"):
            await client.list_models()
    finally:
        await client.aclose()

    assert calls["n"] == 3


async def test_speech_to_speech_timeout_retries_then_succeeds(
    mock_transport_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(elevenlabs_module, "_BACKOFF_BASE_SECONDS", 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, content=b"ok", request=request)

    client = await _build_client("el-key", mock_transport_factory(handler))
    try:
        result = await client.speech_to_speech("voice_123", b"audio")
    finally:
        await client.aclose()

    assert result == b"ok"
    assert calls["n"] == 3
