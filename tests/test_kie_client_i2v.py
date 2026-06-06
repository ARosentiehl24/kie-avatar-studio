"""Tests del método `KieClient.create_image_to_video_task` (Kling 2.6 i2v)."""

from __future__ import annotations

import json

import httpx
import pytest

from kie_avatar_studio.infra.kie_client import (
    DEFAULT_I2V_DURATION,
    DEFAULT_I2V_MODEL,
    KieClient,
)


async def test_create_image_to_video_task_posts_expected_body(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    client, captured = mock_kie_client
    # Override del handler para devolver un taskId fijo.
    captured.clear()
    client._client._transport = httpx.MockTransport(
        lambda req: (
            captured.append(req),
            httpx.Response(200, json={"data": {"taskId": "tk_i2v_1"}}),
        )[1]
    )
    result = await client.create_image_to_video_task(
        image_url="https://tempfile.kie.ai/scene.png",
        prompt="A woman holding a probiotic bottle in a kitchen",
    )
    assert result.task_id == "tk_i2v_1"
    request = captured[-1]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/jobs/createTask"
    body = json.loads(request.content)
    assert body == {
        "model": DEFAULT_I2V_MODEL,
        "input": {
            "image_url": "https://tempfile.kie.ai/scene.png",
            "prompt": "A woman holding a probiotic bottle in a kitchen",
            "duration": DEFAULT_I2V_DURATION,
        },
    }


async def test_create_image_to_video_task_accepts_custom_duration(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    client, captured = mock_kie_client
    captured.clear()
    client._client._transport = httpx.MockTransport(
        lambda req: (
            captured.append(req),
            httpx.Response(200, json={"data": {"taskId": "tk_long"}}),
        )[1]
    )
    await client.create_image_to_video_task(
        image_url="https://tempfile.kie.ai/x.png",
        prompt="prompt",
        duration=10,
    )
    body = json.loads(captured[-1].content)
    assert body["input"]["duration"] == 10


async def test_create_image_to_video_task_accepts_custom_model(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    """Permitir override del modelo facilita futuras versiones (kling-2.7, etc.)."""
    client, captured = mock_kie_client
    captured.clear()
    client._client._transport = httpx.MockTransport(
        lambda req: (captured.append(req), httpx.Response(200, json={"data": {"taskId": "tk_x"}}))[
            1
        ]
    )
    await client.create_image_to_video_task(
        image_url="https://tempfile.kie.ai/x.png",
        prompt="prompt",
        model="kling-2.7/image-to-video",
    )
    body = json.loads(captured[-1].content)
    assert body["model"] == "kling-2.7/image-to-video"


async def test_create_image_to_video_task_propagates_4xx_as_client_error(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    from kie_avatar_studio.domain.errors import KieClientError

    client, _captured = mock_kie_client
    client._client._transport = httpx.MockTransport(
        lambda req: httpx.Response(400, json={"error": "image_url inválido"})
    )
    with pytest.raises(KieClientError):
        await client.create_image_to_video_task(
            image_url="https://nope.example",
            prompt="prompt",
        )
