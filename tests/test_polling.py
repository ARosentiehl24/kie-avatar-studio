"""Tests del helper `polling.poll_task_for_url`.

Compartido entre `JobRunner` y `AudiosController`. Los tests de cada caller ya
cubren la integración; acá probamos la mecánica directa del helper para
detectar regresiones específicas si el shape de `recordInfo` cambia.
"""

from __future__ import annotations

from typing import Any

import pytest

from kie_avatar_studio.app_layer.polling import poll_task_for_url
from kie_avatar_studio.domain.errors import KieError, KieTimeoutError


class _FakeGateway:
    """Gateway in-memory que devuelve respuestas predefinidas en orden."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def get_task_detail(self, task_id: str) -> dict[str, Any]:
        self.calls.append(task_id)
        if not self._responses:
            raise AssertionError("Sin respuestas predefinidas")
        return self._responses.pop(0)


async def test_returns_url_when_first_response_is_success() -> None:
    gateway = _FakeGateway([{"data": {"status": "success", "audio_url": "https://a.mp3"}}])
    url = await poll_task_for_url(
        gateway,  # type: ignore[arg-type]
        "t1",
        kind="audio",
        interval_seconds=1,
        timeout_seconds=10,
    )
    assert url == "https://a.mp3"
    assert gateway.calls == ["t1"]


async def test_polls_until_success() -> None:
    gateway = _FakeGateway(
        [
            {"data": {"status": "pending"}},
            {"data": {"status": "running"}},
            {"data": {"status": "success", "video_url": "https://v.mp4"}},
        ]
    )
    url = await poll_task_for_url(
        gateway,  # type: ignore[arg-type]
        "t1",
        kind="video",
        interval_seconds=0,  # se clampa a 1
        timeout_seconds=10,
    )
    assert url == "https://v.mp4"
    assert len(gateway.calls) == 3


async def test_raises_on_failed_status() -> None:
    gateway = _FakeGateway([{"data": {"status": "failed", "error": "boom"}}])
    with pytest.raises(KieError, match="audio task t1 fallido"):
        await poll_task_for_url(
            gateway,  # type: ignore[arg-type]
            "t1",
            kind="audio",
            interval_seconds=1,
            timeout_seconds=10,
        )


async def test_raises_on_success_without_url() -> None:
    gateway = _FakeGateway([{"data": {"status": "success"}}])
    with pytest.raises(KieError, match="terminado sin URL"):
        await poll_task_for_url(
            gateway,  # type: ignore[arg-type]
            "t1",
            kind="audio",
            interval_seconds=1,
            timeout_seconds=10,
        )


async def test_raises_timeout_when_never_succeeds() -> None:
    gateway = _FakeGateway(
        [
            {"data": {"status": "running"}},
            {"data": {"status": "running"}},
            {"data": {"status": "running"}},
        ]
    )
    with pytest.raises(KieTimeoutError, match="excedió"):
        await poll_task_for_url(
            gateway,  # type: ignore[arg-type]
            "t1",
            kind="audio",
            interval_seconds=1,
            timeout_seconds=2,
        )


async def test_accepts_output_url_shape() -> None:
    """Variante `output.url` también debe extraerse correctamente."""
    gateway = _FakeGateway([{"data": {"status": "success", "output": {"url": "https://x.mp3"}}}])
    url = await poll_task_for_url(
        gateway,  # type: ignore[arg-type]
        "t1",
        kind="audio",
        interval_seconds=1,
        timeout_seconds=10,
    )
    assert url == "https://x.mp3"
