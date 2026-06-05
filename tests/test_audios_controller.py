"""Tests de `AudiosController`: enqueue_generation + wait_for_job + cleanup.

Después de la etapa 3 del refactor, el controller ya no genera audios
inline: encola un `AudioJob` y devuelve. La generación E2E (HTTP + state
machine) se testea en `test_audio_job_runner.py`. Acá probamos solo la
orquestación local: validación de label, persistencia del job,
suscripción a eventos para `wait_for_job`, y cleanup.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from kie_avatar_studio.app_layer.audio_job_lifecycle import AudioJobLifecycle
from kie_avatar_studio.app_layer.audios_controller import AudiosController
from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.domain.errors import (
    AudioExpiredError,
    AudioNotFoundError,
    AudioValidationError,
)
from kie_avatar_studio.domain.events import AudioJobUpdated
from kie_avatar_studio.domain.models import (
    AudioJob,
    AudioJobStatus,
    GeneratedAudio,
    VoiceSettings,
)
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB
from kie_avatar_studio.infra.audios_db import AudiosDB


class _RecordingRunner:
    """Runner fake que registra jobs procesados y simula transición."""

    def __init__(self, target_status: AudioJobStatus = AudioJobStatus.COMPLETED) -> None:
        self.target_status = target_status
        self.processed: list[AudioJob] = []

    async def run(self, job: AudioJob) -> AudioJob:
        self.processed.append(job)
        job.status = self.target_status
        if self.target_status == AudioJobStatus.FAILED:
            job.error = "simulado"
        return job


@pytest.fixture
async def audios_store(tmp_path) -> AudiosDB:
    d = AudiosDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def jobs_repo(tmp_path) -> AudioJobsDB:
    d = AudioJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _build_queue(
    tmp_settings,
    jobs_repo: AudioJobsDB,
    runner: _RecordingRunner,
) -> QueueManager[AudioJob, AudioJobUpdated]:
    return QueueManager(
        tmp_settings,
        runner,
        event_factory=AudioJobUpdated,
        lifecycle=AudioJobLifecycle(jobs_repo),
    )


# --- enqueue_generation -----------------------------------------------------


async def test_enqueue_generation_persists_and_enqueues(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)

    job = await ctl.enqueue_generation("Saludo", "Hola mundo", "EkK5I93UQWFDigLMpZcX")

    assert job.label == "Saludo"
    assert job.status == AudioJobStatus.QUEUED
    assert job.voice_settings_json is None
    # Persistido en el repo.
    fetched = await jobs_repo.get(job.id)
    assert fetched is not None
    assert fetched.label == "Saludo"
    # Procesado por el runner (queue dispara apenas se encola).
    await queue.drain()
    assert len(runner.processed) == 1
    assert runner.processed[0].id == job.id


async def test_enqueue_generation_serializes_voice_settings(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)
    settings = VoiceSettings(stability=0.3, speed=1.1)

    job = await ctl.enqueue_generation("X", "Hola", "EkK5I93UQWFDigLMpZcX", settings)

    assert job.voice_settings_json is not None
    assert '"stability":0.3' in job.voice_settings_json
    assert '"speed":1.1' in job.voice_settings_json
    await queue.drain()


async def test_enqueue_generation_rejects_empty_label(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)

    with pytest.raises(AudioValidationError, match="label"):
        await ctl.enqueue_generation("   ", "Hola", "EkK5I93UQWFDigLMpZcX")

    assert runner.processed == []


async def test_enqueue_generation_rejects_label_too_long(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)

    with pytest.raises(AudioValidationError, match="64"):
        await ctl.enqueue_generation("x" * 65, "Hola", "EkK5I93UQWFDigLMpZcX")


# --- wait_for_job -----------------------------------------------------------


async def test_wait_for_job_resolves_on_terminal(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner(target_status=AudioJobStatus.COMPLETED)
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)

    job = await ctl.enqueue_generation("X", "Hola", "EkK5I93UQWFDigLMpZcX")
    terminal = await asyncio.wait_for(ctl.wait_for_job(job.id), timeout=2.0)

    assert terminal.id == job.id
    assert terminal.status == AudioJobStatus.COMPLETED


async def test_wait_for_job_returns_immediately_if_already_terminal(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)

    # Insertamos un job ya completado directamente en el repo (sin pasar
    # por el queue). wait_for_job lo detecta por el lookup inicial.
    done = AudioJob(
        id="aud_done",
        label="done",
        script="x",
        voice_id="V",
        status=AudioJobStatus.COMPLETED,
    )
    await jobs_repo.upsert(done)

    result = await asyncio.wait_for(ctl.wait_for_job("aud_done"), timeout=2.0)
    assert result.status == AudioJobStatus.COMPLETED
    assert runner.processed == []


async def test_wait_for_job_resolves_on_failed(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner(target_status=AudioJobStatus.FAILED)
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)

    job = await ctl.enqueue_generation("X", "Hola", "EkK5I93UQWFDigLMpZcX")
    terminal = await asyncio.wait_for(ctl.wait_for_job(job.id), timeout=2.0)

    assert terminal.status == AudioJobStatus.FAILED
    assert terminal.error == "simulado"


# --- list_audio_jobs --------------------------------------------------------


async def test_list_audio_jobs_returns_recent(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    runner = _RecordingRunner()
    queue = _build_queue(tmp_settings, jobs_repo, runner)
    ctl = AudiosController(audios_store, jobs_repo, queue)

    await ctl.enqueue_generation("A", "1", "EkK5I93UQWFDigLMpZcX")
    await ctl.enqueue_generation("B", "2", "EkK5I93UQWFDigLMpZcX")
    await queue.drain()

    listed = await ctl.list_audio_jobs()
    assert len(listed) == 2
    assert {j.label for j in listed} == {"A", "B"}


# --- get_for_use + cleanup_expired (no dependen del queue) ------------------


def _persisted_audio(
    audio_id: str = "aud-old",
    *,
    generated_at: datetime | None = None,
) -> GeneratedAudio:
    return GeneratedAudio(
        id=audio_id,
        label="vieja",
        script="x",
        voice_id="V",
        kie_url=f"https://x/{audio_id}.mp3",
        kie_file_path=f"x/{audio_id}.mp3",
        generated_at=generated_at or datetime.now(UTC),
    )


async def test_get_for_use_returns_audio(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    await audios_store.upsert(_persisted_audio("aud-1"))
    queue = _build_queue(tmp_settings, jobs_repo, _RecordingRunner())
    ctl = AudiosController(audios_store, jobs_repo, queue)

    audio = await ctl.get_for_use("aud-1")

    assert audio.id == "aud-1"


async def test_get_for_use_raises_if_missing(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    queue = _build_queue(tmp_settings, jobs_repo, _RecordingRunner())
    ctl = AudiosController(audios_store, jobs_repo, queue)

    with pytest.raises(AudioNotFoundError):
        await ctl.get_for_use("ghost")


async def test_get_for_use_raises_if_expired(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    old = _persisted_audio("aud-old", generated_at=datetime.now(UTC) - timedelta(days=20))
    await audios_store.upsert(old)
    queue = _build_queue(tmp_settings, jobs_repo, _RecordingRunner())
    ctl = AudiosController(audios_store, jobs_repo, queue, retention_days=14)

    with pytest.raises(AudioExpiredError, match="expiró"):
        await ctl.get_for_use("aud-old")


async def test_cleanup_expired_removes_old_audios(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    now = datetime.now(UTC)
    await audios_store.upsert(_persisted_audio("aud-old", generated_at=now - timedelta(days=20)))
    await audios_store.upsert(_persisted_audio("aud-new", generated_at=now))
    queue = _build_queue(tmp_settings, jobs_repo, _RecordingRunner())
    ctl = AudiosController(audios_store, jobs_repo, queue, retention_days=14)

    removed = await ctl.cleanup_expired()

    assert [a.id for a in removed] == ["aud-old"]
    remaining = await ctl.list_generated()
    assert [a.id for a in remaining] == ["aud-new"]


async def test_cleanup_expired_is_idempotent(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    queue = _build_queue(tmp_settings, jobs_repo, _RecordingRunner())
    ctl = AudiosController(audios_store, jobs_repo, queue)

    assert await ctl.cleanup_expired() == []
    assert await ctl.cleanup_expired() == []


async def test_delete_removes_audio(
    tmp_settings, audios_store: AudiosDB, jobs_repo: AudioJobsDB
) -> None:
    await audios_store.upsert(_persisted_audio("aud-1"))
    queue = _build_queue(tmp_settings, jobs_repo, _RecordingRunner())
    ctl = AudiosController(audios_store, jobs_repo, queue)

    await ctl.delete("aud-1")

    assert await ctl.list_generated() == []
