"""Tests del nuevo método `create_nano_banana_task` en `KieClient`."""

from __future__ import annotations

import json
from typing import Any

import httpx

from kie_avatar_studio.infra.kie_client import KieClient


def _swap_transport(client: KieClient, transport: httpx.MockTransport, api_key: str) -> None:
    """Reemplaza el cliente httpx interno de KieClient con uno que use MockTransport.

    Patrón existente en `test_kie_client.py`: en lugar de inyectar el transport
    al construir, lo reemplazamos post-construcción. Mantiene el cliente real
    sin contratos nuevos solo para tests.
    """
    client._client = httpx.AsyncClient(
        transport=transport,
        headers={"Authorization": f"Bearer {api_key}"},
    )


async def test_nano_banana_minimal_payload(tmp_settings) -> None:
    """Llamada sin refs: payload debe usar defaults y `image_input: []`."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/jobs/createTask"
        captured["body"] = json.loads(req.read())
        return httpx.Response(200, json={"data": {"taskId": "nb_task_1"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    _swap_transport(client, httpx.MockTransport(handler), tmp_settings.kie_api_key)

    try:
        result = await client.create_nano_banana_task("un atardecer")
    finally:
        await client.aclose()

    assert result.task_id == "nb_task_1"
    body = captured["body"]
    assert body["model"] == "nano-banana-2"
    assert body["input"]["prompt"] == "un atardecer"
    assert body["input"]["image_input"] == []
    assert body["input"]["aspect_ratio"] == "auto"
    assert body["input"]["resolution"] == "1K"
    assert body["input"]["output_format"] == "jpg"


async def test_nano_banana_with_refs_and_settings(tmp_settings) -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.read())
        return httpx.Response(200, json={"data": {"taskId": "nb_task_2"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    _swap_transport(client, httpx.MockTransport(handler), tmp_settings.kie_api_key)

    refs = [
        "https://tempfile.redpandaai.co/a.png",
        "https://tempfile.redpandaai.co/b.png",
    ]
    try:
        result = await client.create_nano_banana_task(
            "comic poster",
            image_input=refs,
            aspect_ratio="16:9",
            resolution="2K",
            output_format="png",
        )
    finally:
        await client.aclose()

    assert result.task_id == "nb_task_2"
    body = captured["body"]
    assert body["input"]["image_input"] == refs
    assert body["input"]["aspect_ratio"] == "16:9"
    assert body["input"]["resolution"] == "2K"
    assert body["input"]["output_format"] == "png"


async def test_nano_banana_uses_authorization_header(tmp_settings) -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"data": {"taskId": "nb_task_3"}})

    client = KieClient(tmp_settings)
    await client.aclose()
    _swap_transport(client, httpx.MockTransport(handler), tmp_settings.kie_api_key)

    try:
        await client.create_nano_banana_task("x")
    finally:
        await client.aclose()

    assert captured["auth"] == f"Bearer {tmp_settings.kie_api_key}"
