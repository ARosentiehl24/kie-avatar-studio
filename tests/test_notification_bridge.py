"""Tests del `JobNotificationBridge`: filtra terminales + idempotencia."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from kie_avatar_studio.app_layer.notification_bridge import JobNotificationBridge
from kie_avatar_studio.domain.events import AudioJobUpdated, JobUpdated
from kie_avatar_studio.domain.models import (
    AudioJob,
    AudioJobStatus,
    JobStatus,
    VideoJob,
)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    async def notify(self, *, title: str, message: str, success: bool) -> None:
        self.calls.append((title, message, success))


def _video(status: JobStatus, *, job_id: str = "v1", error: str | None = None) -> VideoJob:
    return VideoJob(
        id=job_id,
        script="hola mundo",
        image_path="/tmp/x.png",
        prompt="prompt",
        voice="V",
        status=status,
        output_path="/out/v1/final.mp4" if status == JobStatus.COMPLETED else None,
        error=error,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _audio(status: AudioJobStatus, *, job_id: str = "a1", error: str | None = None) -> AudioJob:
    return AudioJob(
        id=job_id,
        label="saludo",
        script="hola",
        voice_id="V",
        status=status,
        error=error,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def _drain(bridge: JobNotificationBridge) -> None:
    """Espera a que las tasks fire-and-forget del bridge terminen."""
    for _ in range(20):
        if not bridge._pending:
            return
        await asyncio.sleep(0.01)


@pytest.fixture
def bridge() -> tuple[JobNotificationBridge, _RecordingNotifier]:
    n = _RecordingNotifier()
    return JobNotificationBridge(n), n


async def test_video_completed_triggers_notification(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    b, notifier = bridge
    b.on_video_event(JobUpdated(job=_video(JobStatus.COMPLETED)))
    await _drain(b)
    assert len(notifier.calls) == 1
    title, message, success = notifier.calls[0]
    assert success is True
    assert "Video listo" in title
    assert "/out/v1/final.mp4" in message


async def test_video_failed_triggers_notification(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    b, notifier = bridge
    b.on_video_event(JobUpdated(job=_video(JobStatus.FAILED, error="explosión")))
    await _drain(b)
    assert len(notifier.calls) == 1
    title, message, success = notifier.calls[0]
    assert success is False
    assert "Video falló" in title
    assert "explosión" in message


async def test_video_cancelled_does_not_notify(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    """Cancelled lo inició el usuario — no notificar."""
    b, notifier = bridge
    b.on_video_event(JobUpdated(job=_video(JobStatus.CANCELLED)))
    await _drain(b)
    assert notifier.calls == []


async def test_video_in_progress_does_not_notify(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    b, notifier = bridge
    b.on_video_event(JobUpdated(job=_video(JobStatus.WAITING_VIDEO)))
    await _drain(b)
    assert notifier.calls == []


async def test_idempotent_per_job_id(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    """Mismo job_id en COMPLETED dos veces → un solo toast."""
    b, notifier = bridge
    job = _video(JobStatus.COMPLETED)
    b.on_video_event(JobUpdated(job=job))
    b.on_video_event(JobUpdated(job=job))
    b.on_video_event(JobUpdated(job=job))
    await _drain(b)
    assert len(notifier.calls) == 1


async def test_different_job_ids_each_notify(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    b, notifier = bridge
    b.on_video_event(JobUpdated(job=_video(JobStatus.COMPLETED, job_id="a")))
    b.on_video_event(JobUpdated(job=_video(JobStatus.COMPLETED, job_id="b")))
    await _drain(b)
    assert len(notifier.calls) == 2


async def test_audio_completed_triggers_notification(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    b, notifier = bridge
    b.on_audio_event(AudioJobUpdated(job=_audio(AudioJobStatus.COMPLETED)))
    await _drain(b)
    assert len(notifier.calls) == 1
    title, _, success = notifier.calls[0]
    assert success is True
    assert "Audio listo" in title


async def test_audio_failed_triggers_notification(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    b, notifier = bridge
    b.on_audio_event(AudioJobUpdated(job=_audio(AudioJobStatus.FAILED, error="kaboom")))
    await _drain(b)
    assert len(notifier.calls) == 1
    title, message, success = notifier.calls[0]
    assert success is False
    assert "Audio falló" in title
    assert "kaboom" in message


async def test_audio_and_video_share_no_id_namespace(
    bridge: tuple[JobNotificationBridge, _RecordingNotifier],
) -> None:
    """Mismo ID en video y audio (raro pero posible) cada uno notifica una vez."""
    b, notifier = bridge
    b.on_video_event(JobUpdated(job=_video(JobStatus.COMPLETED, job_id="x")))
    b.on_audio_event(AudioJobUpdated(job=_audio(AudioJobStatus.COMPLETED, job_id="x")))
    await _drain(b)
    assert len(notifier.calls) == 2
