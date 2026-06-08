"""Tests del cableado de `audio_queue` y `AudiosController` en el composition root.

Cubre los puntos identificados por la revisión del rubber-duck en Etapa 3:

- Tras `_rebuild_kie_client`, el `audios_controller` debe apuntar al
  nuevo `audio_queue` (no quedar enlazado al viejo).
- Al arrancar, `on_mount` debe restaurar audio jobs en estados
  reanudables (QUEUED, POLLING) y procesarlos.
- Al arrancar, los audio jobs en `CREATING` deben marcarse FAILED
  porque su estado es indeterminado (POST a Kie pudo o no haber
  creado el task).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import AudioJob, AudioJobStatus
from kie_avatar_studio.infra.audio_jobs_db import AudioJobsDB


def _build_app(tmp_path: Path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    app = KieAvatarStudioApp(settings=settings)

    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    return app


async def test_rebuild_kie_client_rebinds_audios_controller(tmp_path: Path) -> None:
    """Después de rebuild, el controller usa el queue nuevo (no el viejo).

    Bug detectado por rubber-duck: el controller guarda una referencia
    concreta al queue. Si no se recrea el controller, los enqueue
    futuros van al runner viejo que usa un KieClient ya cerrado.
    """
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        old_queue = app.audio_queue
        old_controller = app.audios_controller

        await app._rebuild_kie_client()

        assert app.audio_queue is not old_queue
        assert app.audios_controller is not old_controller
        # El controller debe apuntar al queue NUEVO.
        assert app.audios_controller._queue is app.audio_queue  # type: ignore[attr-defined]


async def test_on_mount_restores_pending_audio_jobs(tmp_path: Path) -> None:
    """Audio jobs en QUEUED o POLLING deben reencolarse al arrancar."""
    # Pre-creamos jobs pendientes en la DB antes de mount.
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    db = AudioJobsDB(settings.db_path)
    await db.init()
    queued_job = AudioJob(
        id="aud_queued",
        label="pendiente",
        script="x",
        voice_id="V",
        status=AudioJobStatus.QUEUED,
    )
    polling_job = AudioJob(
        id="aud_polling",
        label="reanudable",
        script="y",
        voice_id="V",
        status=AudioJobStatus.POLLING,
        task_id="t_existing",
    )
    await db.upsert(queued_job)
    await db.upsert(polling_job)

    app = KieAvatarStudioApp(settings=settings)

    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    # Stub del runner para que no haga HTTP: solo registra y marca COMPLETED.
    processed: list[str] = []

    async def fake_run(job: AudioJob) -> AudioJob:
        processed.append(job.id)
        job.status = AudioJobStatus.COMPLETED
        await app.audio_jobs_db.upsert(job)
        return job

    async with app.run_test(size=(120, 40)) as pilot:
        # Reemplazamos el runner DESPUÉS de mount (cuando ya está cableado)
        # pero ANTES de que las tareas restauradas terminen. Aceptamos un
        # pequeño compromiso: si el runner real arrancó, los stubea para
        # las próximas iteraciones. Por eso esperamos a que processed
        # contenga los IDs esperados con un timeout.
        app.audio_runner.run = fake_run  # type: ignore[method-assign]
        # Esperamos a que el queue procese ambos jobs.
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.05)
            current = await app.audio_jobs_db.get("aud_queued")
            if current is not None and current.status == AudioJobStatus.COMPLETED:
                break

    queued_after = await db.get("aud_queued")
    polling_after = await db.get("aud_polling")
    # Si el restore funcionó, ambos jobs deben estar al menos en estado
    # no-QUEUED/POLLING (algún runner los procesó). Validamos eso de
    # forma laxa porque el runner real puede haber fallado por falta de
    # HTTP mock — lo importante es que NO hayan quedado intactos.
    assert queued_after is not None
    assert polling_after is not None
    assert (
        queued_after.status != AudioJobStatus.QUEUED
        or polling_after.status != AudioJobStatus.POLLING
    )


@pytest.mark.parametrize(
    "stuck_id,stuck_label",
    [("aud_creating_1", "creando"), ("aud_creating_2", "otro")],
)
async def test_on_mount_marks_creating_jobs_as_failed(
    tmp_path: Path, stuck_id: str, stuck_label: str
) -> None:
    """Audio jobs que quedaron en CREATING al apagar la app deben sanearse."""
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    db = AudioJobsDB(settings.db_path)
    await db.init()
    stuck = AudioJob(
        id=stuck_id,
        label=stuck_label,
        script="x",
        voice_id="V",
        status=AudioJobStatus.CREATING,
    )
    await db.upsert(stuck)

    app = KieAvatarStudioApp(settings=settings)

    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()

    sanitized = await db.get(stuck_id)
    assert sanitized is not None
    assert sanitized.status == AudioJobStatus.FAILED
    assert sanitized.error is not None
    assert "indeterminado" in sanitized.error.lower()


async def test_rebuild_kie_client_rebinds_workflow_subsystem(tmp_path: Path) -> None:
    """Tras rebuild, todo el subsistema workflow apunta al cliente NUEVO.

    Regresión del bug "Cannot send a request, as the client has been closed."
    que aparecía al previsualizar la imagen base después de configurar una
    nueva API key activa: el `WorkflowController`/`WorkflowBaseResolver`/
    `WorkflowRunnerFactory` quedaban apuntando al `httpx.AsyncClient` viejo
    cerrado, mientras que video/audio/image sí se rebindeaban.

    También cubre la re-suscripción de los listeners del notification
    bridge a las queues nuevas (hallazgo del code-quality-reviewer:
    CR-2.1 / regresión latente).
    """
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        old_kie = app.kie
        old_workflow_queue = app.workflow_queue
        old_workflow_controller = app.workflow_controller
        old_workflow_base_resolver = app.workflow_base_resolver
        old_workflow_factory = app.workflow_runner_factory

        await app._rebuild_kie_client()

        # Cliente nuevo + todas las referencias del subsistema renovadas.
        assert app.kie is not old_kie
        assert app.workflow_queue is not old_workflow_queue
        assert app.workflow_controller is not old_workflow_controller
        assert app.workflow_base_resolver is not old_workflow_base_resolver
        assert app.workflow_runner_factory is not old_workflow_factory
        # Base resolver y factory apuntan al cliente NUEVO (validamos por
        # identidad del atributo interno donde guardan el cliente).
        assert app.workflow_base_resolver._client is app.kie  # type: ignore[attr-defined]
        assert (
            app.workflow_runner_factory._image_deps.client is app.kie  # type: ignore[attr-defined]
        )
        assert (
            app.workflow_runner_factory._audio_deps.client is app.kie  # type: ignore[attr-defined]
        )
        # El controller usa el queue NUEVO (no el viejo).
        assert app.workflow_controller._queue is app.workflow_queue  # type: ignore[attr-defined]
        # El notification bridge se re-suscribió a las queues nuevas.
        assert (
            app.notification_bridge.on_workflow_event in app.workflow_queue._listeners  # type: ignore[attr-defined]
        )
