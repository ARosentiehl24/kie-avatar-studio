"""Test crítico de paralelismo cross-queue: el semáforo compartido limita las 3 colas.

Hallazgo #9 del rubber-duck: como hay 3 `QueueManager` (video, audio,
image) compartiendo el mismo `Semaphore(max_parallel_jobs)`, ningún
combo de jobs puede ejecutar más runners simultáneos que el límite
configurado. Sin el semáforo compartido, cada cola tendría su propio
contador y podríamos llegar a 3x el límite real.
"""

from __future__ import annotations

import asyncio

import pytest

from kie_avatar_studio.app_layer.audio_job_lifecycle import AudioJobLifecycle
from kie_avatar_studio.app_layer.image_job_lifecycle import ImageJobLifecycle
from kie_avatar_studio.app_layer.queue_manager import QueueManager
from kie_avatar_studio.app_layer.video_job_lifecycle import VideoJobLifecycle
from kie_avatar_studio.domain.events import AudioJobUpdated, ImageJobUpdated, JobUpdated
from kie_avatar_studio.domain.models import AudioJob, ImageJob, VideoJob
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB
from kie_avatar_studio.infra.db import JobsDB
from kie_avatar_studio.infra.image_jobs_db import ImageJobsDB


class _BlockingRunner:
    """Runner que bloquea hasta que se le permite avanzar. Cuenta
    cuántos jobs están "dentro" simultáneamente para detectar
    violaciones del límite global."""

    def __init__(self, shared_counter: dict[str, int]) -> None:
        self._counter = shared_counter
        self._gate = asyncio.Event()
        self._max_concurrent = 0

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def release(self) -> None:
        self._gate.set()

    async def run(self, job) -> object:
        self._counter["active"] += 1
        self._max_concurrent = max(self._max_concurrent, self._counter["active"])
        try:
            await self._gate.wait()
        finally:
            self._counter["active"] -= 1
        return job


@pytest.fixture
async def jobs_repo(tmp_path) -> JobsDB:
    d = JobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def audio_repo(tmp_path) -> AudioJobsDB:
    d = AudioJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


@pytest.fixture
async def image_repo(tmp_path) -> ImageJobsDB:
    d = ImageJobsDB(tmp_path / "jobs.db")
    await d.init()
    return d


async def test_shared_semaphore_caps_total_concurrency(
    tmp_settings, jobs_repo, audio_repo, image_repo
) -> None:
    """Con `max_parallel_jobs=1` y un job de cada tipo en cola, el máximo
    simultáneo NUNCA debe superar 1 — sin importar de qué cola venga.

    Es la propiedad crítica del semáforo compartido en `app.py`. Si no
    está bien cableado, este test sería el primero en romperse en
    producción cuando el usuario tenga jobs simultáneos de los tres tipos.
    """
    # Ajustar el límite a 1 para forzar serialización estricta.
    tight_settings = tmp_settings.model_copy(update={"max_parallel_jobs": 1})
    semaphore = asyncio.Semaphore(1)
    counter = {"active": 0}

    video_runner = _BlockingRunner(counter)
    audio_runner = _BlockingRunner(counter)
    image_runner = _BlockingRunner(counter)

    video_queue: QueueManager[VideoJob, JobUpdated] = QueueManager(
        tight_settings,
        video_runner,
        event_factory=JobUpdated,
        lifecycle=VideoJobLifecycle(jobs_repo),
        capacity_limiter=semaphore,
    )
    audio_queue: QueueManager[AudioJob, AudioJobUpdated] = QueueManager(
        tight_settings,
        audio_runner,
        event_factory=AudioJobUpdated,
        lifecycle=AudioJobLifecycle(audio_repo),
        capacity_limiter=semaphore,
    )
    image_queue: QueueManager[ImageJob, ImageJobUpdated] = QueueManager(
        tight_settings,
        image_runner,
        event_factory=ImageJobUpdated,
        lifecycle=ImageJobLifecycle(image_repo),
        capacity_limiter=semaphore,
    )

    video_job = VideoJob(id="v1", prompt="p", script="s", image_path="/tmp/i.png", voice="V")
    audio_job = AudioJob(id="a1", label="a", script="s", voice_id="V")
    image_job = ImageJob(id="i1", label="i", prompt="p")

    await jobs_repo.upsert(video_job)
    await audio_repo.upsert(audio_job)
    await image_repo.upsert(image_job)

    video_queue.enqueue(video_job)
    audio_queue.enqueue(audio_job)
    image_queue.enqueue(image_job)

    # Cedemos el loop para que se programen las tres tareas y entren al
    # semáforo. Solo una puede pasar; las otras dos quedan colgadas.
    await asyncio.sleep(0.05)

    # Liberamos los tres runners (la primera tarea termina, la segunda
    # entra, etc).
    video_runner.release()
    audio_runner.release()
    image_runner.release()

    await video_queue.drain()
    await audio_queue.drain()
    await image_queue.drain()

    # CRÍTICO: el counter nunca debe haber pasado de 1, sin importar qué
    # cola corra primero. Si rompemos el semáforo compartido, alguno
    # llegaría a 2 o 3.
    total_max = max(
        video_runner.max_concurrent,
        audio_runner.max_concurrent,
        image_runner.max_concurrent,
    )
    assert total_max <= 1, (
        f"el semáforo compartido falló: max concurrent observado = {total_max} "
        "(video={}, audio={}, image={})".format(
            video_runner.max_concurrent,
            audio_runner.max_concurrent,
            image_runner.max_concurrent,
        )
    )
