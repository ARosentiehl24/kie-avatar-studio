import httpx
import pytest

from kie_avatar_studio.domain.errors import KieClientError, KieServerError
from kie_avatar_studio.domain.models import VoiceSettings
from kie_avatar_studio.infra.kie_client import KieClient


def _client_with(transport: httpx.MockTransport, tmp_settings) -> KieClient:
    client = KieClient(tmp_settings)
    # reemplazamos el http client interno por uno con transporte mockeado
    # (los tests deben cerrarlo con aclose en finally del caller)
    import asyncio

    asyncio.get_event_loop().run_until_complete(client.aclose())
    client._client = httpx.AsyncClient(
        transport=transport,
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    return client


async def test_create_tts_task_happy(tmp_settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/jobs/createTask"
        body = req.read()
        assert b"elevenlabs" in body
        return httpx.Response(200, json={"data": {"taskId": "t_123"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        result = await client.create_tts_task("hola", "voiceA")
        assert result.task_id == "t_123"
    finally:
        await client.aclose()


async def test_4xx_raises_client_error(tmp_settings) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        with pytest.raises(KieClientError):
            await client.create_tts_task("x", "v")
    finally:
        await client.aclose()


async def test_5xx_retries_then_raises_server_error(tmp_settings) -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="upstream down")

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        with pytest.raises(KieServerError):
            await client.create_tts_task("x", "v")
        assert len(calls) == 3, f"esperaba 3 intentos, hubo {len(calls)}"
    finally:
        await client.aclose()


async def test_5xx_then_success(tmp_settings) -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, json={"data": {"taskId": "t_ok"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        result = await client.create_tts_task("x", "v")
        assert result.task_id == "t_ok"
        assert calls["n"] == 3
    finally:
        await client.aclose()


# --- create_tts_task: voice_settings opcionales (Fase 2.2c) -----------------


async def test_create_tts_task_without_voice_settings_omits_them(tmp_settings) -> None:
    """Body sin `voice_settings` ni claves extra dentro de `input` cuando es None."""
    captured: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.read())
        return httpx.Response(200, json={"data": {"taskId": "t_ok"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        await client.create_tts_task("hola", "voiceA")
    finally:
        await client.aclose()
    body = captured[0]
    assert b'"text":"hola"' in body
    assert b'"voice":"voiceA"' in body
    # Ningún ajuste extra leak-eado
    assert b'"stability"' not in body
    assert b'"similarity_boost"' not in body
    assert b'"speed"' not in body
    assert b'"voice_settings"' not in body


async def test_create_tts_task_with_partial_voice_settings(tmp_settings) -> None:
    """Solo los campos seteados deben aparecer planos en `input` (no anidados)."""
    captured: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.read())
        return httpx.Response(200, json={"data": {"taskId": "t_ok"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        await client.create_tts_task(
            "hola",
            "voiceA",
            voice_settings=VoiceSettings(stability=0.3),
        )
    finally:
        await client.aclose()
    body = captured[0]
    assert b'"stability":0.3' in body
    # Los no seteados no leak-ean
    assert b'"similarity_boost"' not in body
    assert b'"speed"' not in body
    # Plano, no anidado
    assert b'"voice_settings"' not in body


async def test_create_tts_task_with_full_voice_settings(tmp_settings) -> None:
    """Los 5 campos seteados aparecen planos dentro de `input`."""
    captured: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.read())
        return httpx.Response(200, json={"data": {"taskId": "t_ok"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        await client.create_tts_task(
            "hola",
            "voiceA",
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                style=0.0,
                speed=1.0,
                language_code="es",
            ),
        )
    finally:
        await client.aclose()
    body = captured[0]
    assert b'"stability":0.5' in body
    assert b'"similarity_boost":0.75' in body
    assert b'"style":0.0' in body
    assert b'"speed":1.0' in body
    # El language_code se debe haber quitado porque el modelo es el multilingual-v2 default
    assert b'"language_code"' not in body


async def test_create_tts_task_custom_model(tmp_settings) -> None:
    """Si se pasa `model` kw-only, debe usarse en lugar del default."""
    captured: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.read())
        return httpx.Response(200, json={"data": {"taskId": "t_ok"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        await client.create_tts_task(
            "hola",
            "voiceA",
            model="elevenlabs/text-to-speech-turbo-2-5",
        )
    finally:
        await client.aclose()
    # Debe haberse sobreescrito el modelo turbo por el multilingual-v2 default
    assert b'"model":"elevenlabs/text-to-speech-multilingual-v2"' in captured[0]


# --- get_account_credits + 402 handling (Fase 2.2c.fix créditos) -----------


async def test_get_account_credits_happy(tmp_settings) -> None:
    """GET /api/v1/chat/credit devuelve balance float."""
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"code": 200, "msg": "success", "data": 6.97})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        balance = await client.get_account_credits()
    finally:
        await client.aclose()

    assert balance == 6.97
    assert captured[0].url.path == "/api/v1/chat/credit"


async def test_get_account_credits_handles_int_response(tmp_settings) -> None:
    """Kie a veces devuelve `data: 100` (int) en lugar de float."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "msg": "success", "data": 100})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        balance = await client.get_account_credits()
    finally:
        await client.aclose()

    assert balance == 100.0
    assert isinstance(balance, float)


async def test_get_account_credits_propagates_401(tmp_settings) -> None:
    """Key inválida → 401 → KieClientError (no Insufficient)."""
    from kie_avatar_studio.domain.errors import KieClientError, KieInsufficientCreditsError

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        with pytest.raises(KieClientError) as exc_info:
            await client.get_account_credits()
        # KieInsufficientCreditsError es subclase de KieClientError; verificamos
        # que NO sea esa subclase.
        assert not isinstance(exc_info.value, KieInsufficientCreditsError)
    finally:
        await client.aclose()


async def test_402_status_raises_insufficient_credits(tmp_settings) -> None:
    """HTTP 402 directo → KieInsufficientCreditsError con mensaje accionable."""
    from kie_avatar_studio.domain.errors import KieInsufficientCreditsError

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "code": 402,
                "msg": "Credits insufficient",
            },
        )

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        with pytest.raises(KieInsufficientCreditsError) as exc_info:
            await client.create_tts_task("x", "voiceA")
        assert "Saldo insuficiente" in str(exc_info.value)
        assert "kie.ai/billing" in str(exc_info.value)
    finally:
        await client.aclose()


async def test_200_with_code_402_in_body_raises_insufficient_credits(tmp_settings) -> None:
    """HTTP 200 + code:402 en el body (forma vista en Kie real) → tipado correcto."""
    from kie_avatar_studio.domain.errors import KieInsufficientCreditsError

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 402,
                "msg": "Credits insufficient : Your current balance isn't enough",
                "data": None,
            },
        )

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        with pytest.raises(KieInsufficientCreditsError) as exc_info:
            await client.create_tts_task("x", "voiceA")
        assert "code:402" in str(exc_info.value)
    finally:
        await client.aclose()


async def test_200_with_code_200_does_not_trigger_credit_error(tmp_settings) -> None:
    """Regresión: éxito normal NO debe gatillar KieInsufficientCreditsError."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200, "data": {"taskId": "t_ok"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    try:
        result = await client.create_tts_task("x", "voiceA")
        assert result.task_id == "t_ok"
    finally:
        await client.aclose()


# --- upload_file: URL sanitization of space characters (Round 6) ------------


async def test_upload_file_sanitizes_spaces_in_download_url(tmp_settings, tmp_path) -> None:
    """Si el downloadUrl devuelto por Kie contiene espacios (ej. por nombre de archivo local),
    el cliente debe reemplazarlos por %20 para que sea una URL HTTP bien formada."""
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/file-stream-upload"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "msg": "success",
                "data": {
                    "fileName": "WhatsApp Image 2026-06-08 at 11.31.43.jpeg",
                    "filePath": "uploads/WhatsApp Image 2026-06-08 at 11.31.43.jpeg",
                    "downloadUrl": "https://tempfile.kie.ai/uploads/WhatsApp Image 2026-06-08 at 11.31.43.jpeg",
                    "fileSize": 1234,
                    "mimeType": "image/jpeg",
                },
            },
        )

    client = KieClient(tmp_settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {tmp_settings.kie_api_key}"},
    )
    local_file = tmp_path / "WhatsApp Image 2026-06-08 at 11.31.43.jpeg"
    local_file.write_bytes(b"dummy")

    try:
        result = await client.upload_file(local_file)
        # Los espacios internos en la download_url deben haberse reemplazado por %20.
        assert " " not in result.download_url
        assert (
            result.download_url
            == "https://tempfile.kie.ai/uploads/WhatsApp%20Image%202026-06-08%20at%2011.31.43.jpeg"
        )
    finally:
        await client.aclose()
