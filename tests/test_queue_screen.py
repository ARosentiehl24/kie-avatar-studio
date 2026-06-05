"""Smoke tests de `QueueScreen` (cola operativa video+audio)."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, DataTable, Static

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import (
    AudioJob,
    AudioJobStatus,
    JobStatus,
    VideoJob,
)


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


def _audio(audio_id: str, status: AudioJobStatus) -> AudioJob:
    return AudioJob(id=audio_id, label=audio_id, script="x", voice_id="V", status=status)


def _video(video_id: str, status: JobStatus) -> VideoJob:
    return VideoJob(
        id=video_id,
        prompt=video_id,
        image_url="https://k/i.png",
        audio_url="https://k/a.mp3",
        status=status,
    )


async def test_queue_opens_with_g_hotkey(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "QueueScreen"


async def test_queue_excludes_completed_jobs(tmp_path: Path) -> None:
    """La cola NO debe mostrar jobs COMPLETED (esos están en Historial)."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await app.audio_jobs_db.upsert(_audio("a-queued", AudioJobStatus.QUEUED))
        await app.audio_jobs_db.upsert(_audio("a-done", AudioJobStatus.COMPLETED))
        await app.audio_jobs_db.upsert(_audio("a-failed", AudioJobStatus.FAILED))
        await app.db.upsert(_video("v-done", JobStatus.COMPLETED))
        await app.db.upsert(_video("v-progress", JobStatus.WAITING_VIDEO))

        await pilot.press("g")
        await pilot.pause()

        table = app.screen.query_one("#queue-table", DataTable)
        # 3 esperados: a-queued, a-failed, v-progress. Los 2 completed
        # quedan fuera.
        assert table.row_count == 3


async def test_queue_summary_counts_by_state(tmp_path: Path) -> None:
    """Los contadores arriba deben separar queued / procesando / fallidos."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await app.audio_jobs_db.upsert(_audio("q1", AudioJobStatus.QUEUED))
        await app.audio_jobs_db.upsert(_audio("q2", AudioJobStatus.QUEUED))
        await app.audio_jobs_db.upsert(_audio("f1", AudioJobStatus.FAILED))
        await app.db.upsert(_video("p1", JobStatus.CREATING_AVATAR))

        await pilot.press("g")
        await pilot.pause()

        summary = str(app.screen.query_one("#queue-summary", Static).render())
        assert "2 en cola" in summary
        assert "1 procesando" in summary
        assert "1 fallidos" in summary


async def test_queue_action_buttons_render(tmp_path: Path) -> None:
    """Las 4 acciones de cola deben estar renderizadas."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        for btn_id in (
            "queue-cancel",
            "queue-retry",
            "queue-cancel-all",
            "queue-retry-all",
        ):
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert btn is not None


async def test_queue_filter_solo_audio_excludes_video(tmp_path: Path) -> None:
    """Filtro 'Solo audio' debe esconder los video jobs."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await app.audio_jobs_db.upsert(_audio("a1", AudioJobStatus.QUEUED))
        await app.db.upsert(_video("v1", JobStatus.WAITING_VIDEO))

        await pilot.press("g")
        await pilot.pause()
        await pilot.click("#queue-filter-audio")
        await pilot.pause()

        table = app.screen.query_one("#queue-table", DataTable)
        assert table.row_count == 1


async def test_queue_cancel_all_queued_invokes_controller(tmp_path: Path) -> None:
    """'Cancelar todos en cola' debe llamar cancel() para cada job QUEUED."""
    app = _build_app(tmp_path)
    cancelled: list[str] = []

    async def fake_audio_cancel(job_id: str) -> bool:
        cancelled.append(f"audio:{job_id}")
        return True

    async def fake_video_cancel(job_id: str) -> bool:
        cancelled.append(f"video:{job_id}")
        return True

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await app.audio_jobs_db.upsert(_audio("aq1", AudioJobStatus.QUEUED))
        await app.audio_jobs_db.upsert(_audio("aq2", AudioJobStatus.QUEUED))
        # Este NO debe cancelarse (no está en cola, está procesando):
        await app.audio_jobs_db.upsert(_audio("ap1", AudioJobStatus.POLLING))
        await app.db.upsert(_video("vq1", JobStatus.QUEUED))

        await pilot.press("g")
        await pilot.pause()
        app.audios_controller.cancel = fake_audio_cancel  # type: ignore[method-assign]
        app.videos_controller.cancel = fake_video_cancel  # type: ignore[method-assign]
        await pilot.click("#queue-cancel-all")
        await pilot.pause()

    assert sorted(cancelled) == ["audio:aq1", "audio:aq2", "video:vq1"]
    # ap1 (POLLING) NO debe estar.
    assert "audio:ap1" not in cancelled


async def test_queue_retry_all_failed_invokes_controller(tmp_path: Path) -> None:
    """'Reintentar TODOS los fallidos' debe llamar retry() para FAILED y CANCELLED."""
    app = _build_app(tmp_path)
    retried: list[str] = []

    async def fake_audio_retry(job_id: str) -> bool:
        retried.append(f"audio:{job_id}")
        return True

    async def fake_video_retry(job_id: str) -> bool:
        retried.append(f"video:{job_id}")
        return True

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await app.audio_jobs_db.upsert(_audio("af1", AudioJobStatus.FAILED))
        await app.audio_jobs_db.upsert(_audio("ac1", AudioJobStatus.CANCELLED))
        await app.audio_jobs_db.upsert(_audio("aq1", AudioJobStatus.QUEUED))
        await app.db.upsert(_video("vf1", JobStatus.FAILED))

        await pilot.press("g")
        await pilot.pause()
        app.audios_controller.retry = fake_audio_retry  # type: ignore[method-assign]
        app.videos_controller.retry = fake_video_retry  # type: ignore[method-assign]
        await pilot.click("#queue-retry-all")
        await pilot.pause()

    assert sorted(retried) == ["audio:ac1", "audio:af1", "video:vf1"]
    # QUEUED NO debe estar (no es failed).
    assert "audio:aq1" not in retried


async def test_queue_unsubscribes_on_unmount(tmp_path: Path) -> None:
    """Al salir, los listeners de ambas queues se desuscriben."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        initial_v = len(app.queue._listeners)  # type: ignore[attr-defined]
        initial_a = len(app.audio_queue._listeners)  # type: ignore[attr-defined]
        await pilot.press("g")
        await pilot.pause()
        assert len(app.queue._listeners) == initial_v + 1  # type: ignore[attr-defined]
        assert len(app.audio_queue._listeners) == initial_a + 1  # type: ignore[attr-defined]

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.queue._listeners) == initial_v  # type: ignore[attr-defined]
        assert len(app.audio_queue._listeners) == initial_a  # type: ignore[attr-defined]
