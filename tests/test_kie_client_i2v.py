"""Tests del método `KieClient.create_image_to_video_task` (Kling 3.0 video)."""

from __future__ import annotations

import json

import httpx
import pytest

from kie_avatar_studio.infra.kie_client import (
    DEFAULT_I2V_ASPECT_RATIO,
    DEFAULT_I2V_DURATION,
    DEFAULT_I2V_MODE,
    DEFAULT_I2V_MODEL,
    KieClient,
)


async def test_create_image_to_video_task_posts_expected_body(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    client, captured = mock_kie_client
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
            "prompt": "A woman holding a probiotic bottle in a kitchen",
            "image_urls": ["https://tempfile.kie.ai/scene.png"],
            "sound": False,
            "duration": str(DEFAULT_I2V_DURATION),
            "aspect_ratio": DEFAULT_I2V_ASPECT_RATIO,
            "mode": DEFAULT_I2V_MODE,
            "multi_shots": False,
            "multi_prompt": [],
            "kling_elements": [],
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
    assert body["input"]["duration"] == "10"


async def test_create_image_to_video_task_accepts_sound_true(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    """Cuando voiceover=false, el caller pasa sound=true para sound efx nativos."""
    client, captured = mock_kie_client
    captured.clear()
    client._client._transport = httpx.MockTransport(
        lambda req: (captured.append(req), httpx.Response(200, json={"data": {"taskId": "tk_s"}}))[
            1
        ]
    )
    await client.create_image_to_video_task(
        image_url="https://tempfile.kie.ai/x.png",
        prompt="ocean waves",
        sound=True,
    )
    body = json.loads(captured[-1].content)
    assert body["input"]["sound"] is True


async def test_create_image_to_video_task_accepts_custom_mode_and_aspect(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    client, captured = mock_kie_client
    captured.clear()
    client._client._transport = httpx.MockTransport(
        lambda req: (captured.append(req), httpx.Response(200, json={"data": {"taskId": "tk_m"}}))[
            1
        ]
    )
    await client.create_image_to_video_task(
        image_url="https://tempfile.kie.ai/x.png",
        prompt="prompt",
        mode="4K",
        aspect_ratio="9:16",
    )
    body = json.loads(captured[-1].content)
    assert body["input"]["mode"] == "4K"
    assert body["input"]["aspect_ratio"] == "9:16"


async def test_create_image_to_video_task_accepts_custom_model(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    """Permitir override del modelo facilita futuras versiones (kling-3.1, etc.)."""
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
        model="kling-3.1/video",
    )
    body = json.loads(captured[-1].content)
    assert body["model"] == "kling-3.1/video"


async def test_create_image_to_video_task_propagates_4xx_as_client_error(
    mock_kie_client: tuple[KieClient, list[httpx.Request]],
) -> None:
    from kie_avatar_studio.domain.errors import KieClientError

    client, _captured = mock_kie_client
    client._client._transport = httpx.MockTransport(
        lambda req: httpx.Response(400, json={"error": "image_urls inválido"})
    )
    with pytest.raises(KieClientError):
        await client.create_image_to_video_task(
            image_url="https://nope.example",
            prompt="prompt",
        )
