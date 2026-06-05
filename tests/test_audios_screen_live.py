"""Tests del refresh en vivo de la cola en `AudiosScreen` (Etapa 4).

Validan que cuando `audio_queue` emite `AudioJobUpdated`, la pantalla
refresca tabla + contadores sin necesidad de polling manual.
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import DataTable, Static

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.events import AudioJobUpdated
from kie_avatar_studio.domain.models import AudioJob, AudioJobStatus


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


async def test_screen_refreshes_when_queue_emits_event(tmp_path: Path) -> None:
    """Insertar un job nuevo y emitir evento → la tabla lo refleja."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        table = app.screen.query_one("#audios-table", DataTable)
        assert table.row_count == 0  # arranca vacía

        # Persistimos el job y emitimos el evento como lo haría el runner.
        new_job = AudioJob(
            id="aud_live",
            label="en vivo",
            script="x",
            voice_id="V",
            status=AudioJobStatus.QUEUED,
        )
        await app.audio_jobs_db.upsert(new_job)
        # Simulamos lo que hace `QueueManager._notify` invocando los
        # listeners registrados. La screen está suscripta.
        for listener in list(app.audio_queue._listeners):  # type: ignore[attr-defined]
            listener(AudioJobUpdated(new_job))
        await pilot.pause()

        assert table.row_count == 1


async def test_counters_reflect_status_distribution(tmp_path: Path) -> None:
    """Los contadores arriba deben sumar por estado."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # 1 en cola, 1 procesando, 2 listos, 1 fallido = total 5
        jobs = [
            AudioJob(id="q1", label="q", script="x", voice_id="V", status=AudioJobStatus.QUEUED),
            AudioJob(id="p1", label="p", script="x", voice_id="V", status=AudioJobStatus.POLLING),
            AudioJob(
                id="c1", label="c1", script="x", voice_id="V", status=AudioJobStatus.COMPLETED
            ),
            AudioJob(
                id="c2", label="c2", script="x", voice_id="V", status=AudioJobStatus.COMPLETED
            ),
            AudioJob(id="f1", label="f", script="x", voice_id="V", status=AudioJobStatus.FAILED),
        ]
        for j in jobs:
            await app.audio_jobs_db.upsert(j)

        await pilot.press("a")
        await pilot.pause()

        counters_widget = app.screen.query_one("#audios-counters", Static)
        rendered = str(counters_widget.render())
        # Buscamos cada conteo en el render (puede estar coloreado con
        # markup Rich; el plain text debería estar igual).
        assert "1 generando" in rendered
        assert "1 en cola" in rendered
        assert "2 listos" in rendered
        assert "1 fallidos" in rendered


async def test_unsubscribe_on_unmount(tmp_path: Path) -> None:
    """Al salir de la pantalla, el listener se desuscribe (no queda colgado)."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        initial_listeners = len(app.audio_queue._listeners)  # type: ignore[attr-defined]
        await pilot.press("a")
        await pilot.pause()
        assert len(app.audio_queue._listeners) == initial_listeners + 1  # type: ignore[attr-defined]

        # Volver al menú principal → unmount de AudiosScreen.
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.audio_queue._listeners) == initial_listeners  # type: ignore[attr-defined]
