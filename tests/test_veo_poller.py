from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kie_avatar_studio.app_layer import veo_poller
from kie_avatar_studio.app_layer.veo_poller import poll_veo_task_for_url
from kie_avatar_studio.domain.errors import KieError, KieTimeoutError
from kie_avatar_studio.domain.policies import (
    VEO_STATUS_FAILED,
    VEO_STATUS_GENERATING,
    VEO_STATUS_SUCCESS,
    VEO_STATUS_UPSTREAM_FAILED,
)
from kie_avatar_studio.domain.ports import ExternalJsonObject


class _FakeGateway:
    def __init__(self, responses: list[ExternalJsonObject]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def get_veo_task_detail(self, task_id: str) -> ExternalJsonObject:
        self.calls.append(task_id)
        if len(self.calls) <= len(self._responses):
            return self._responses[len(self.calls) - 1]
        return self._responses[-1]


async def test_poll_veo_task_for_url_polls_until_success() -> None:
    gateway = _FakeGateway(
        [
            {"data": {"successFlag": VEO_STATUS_GENERATING}},
            {
                "data": {
                    "successFlag": VEO_STATUS_SUCCESS,
                    "response": {"resultUrls": ["https://cdn.example/video.mp4"]},
                }
            },
        ]
    )
    fake_sleep = AsyncMock()

    with patch.object(veo_poller.asyncio, "sleep", new=fake_sleep):
        result = await poll_veo_task_for_url(
            gateway,
            "veo_123",
            interval_seconds=2,
            timeout_seconds=10,
        )

    assert result == "https://cdn.example/video.mp4"
    assert gateway.calls == ["veo_123", "veo_123"]
    fake_sleep.assert_awaited_once_with(2)


async def test_poll_veo_task_for_url_raises_on_failed_status() -> None:
    gateway = _FakeGateway(
        [{"data": {"successFlag": VEO_STATUS_FAILED, "errorCode": "quota_exceeded"}}]
    )

    with pytest.raises(KieError, match="quota_exceeded"):
        await poll_veo_task_for_url(gateway, "veo_123", interval_seconds=1, timeout_seconds=5)


async def test_poll_veo_task_for_url_raises_on_upstream_failed_status() -> None:
    gateway = _FakeGateway(
        [{"data": {"successFlag": VEO_STATUS_UPSTREAM_FAILED, "errorCode": "model_down"}}]
    )

    with pytest.raises(KieError, match="upstream"):
        await poll_veo_task_for_url(gateway, "veo_123", interval_seconds=1, timeout_seconds=5)


async def test_poll_veo_task_for_url_raises_when_success_has_no_urls() -> None:
    gateway = _FakeGateway(
        [{"data": {"successFlag": VEO_STATUS_SUCCESS, "response": {"resultUrls": []}}}]
    )

    with pytest.raises(KieError, match="sin resultUrls"):
        await poll_veo_task_for_url(gateway, "veo_123", interval_seconds=1, timeout_seconds=5)


async def test_poll_veo_task_for_url_times_out() -> None:
    gateway = _FakeGateway([{"data": {"successFlag": VEO_STATUS_GENERATING}}])
    fake_sleep = AsyncMock()

    with (
        patch.object(veo_poller.asyncio, "sleep", new=fake_sleep),
        pytest.raises(KieTimeoutError, match="excedió 3s"),
    ):
        await poll_veo_task_for_url(gateway, "veo_123", interval_seconds=1, timeout_seconds=3)

    assert gateway.calls == ["veo_123", "veo_123", "veo_123"]
    assert fake_sleep.await_count == 3
