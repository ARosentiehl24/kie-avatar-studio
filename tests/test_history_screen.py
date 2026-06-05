"""Smoke tests de `HistoryScreen`: render + refresh en vivo + filtros."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from textual.widgets import DataTable, Static

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.events import AudioJobUpdated, HistoryEntry, JobUpdated
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
    return KieAvatarStudioApp(settings=settings)


def _video(job_id: str = "v1") -> VideoJob:
    return VideoJob(
        id=job_id,
        script="describe this scene",
        image_path="/tmp/x.png",
        prompt="cinematic",
        voice="V",
        status=JobStatus.COMPLETED,
        created_at=datetime.now(UTC),
    )


def _audio(job_id: str = "a1") -> AudioJob:
    return AudioJob(
        id=job_id,
        label="saludo",
        script="hola",
        voice_id="V",
        status=AudioJobStatus.COMPLETED,
        created_at=datetime.now(UTC),
    )


async def test_history_opens_with_h_hotkey(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HistoryScreen"


async def test_history_lists_both_kinds(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.db.upsert(_video())
        await app.audio_jobs_db.upsert(_audio())
        await pilot.press("h")
        await pilot.pause()
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 2


async def test_history_refreshes_on_audio_event(tmp_path: Path) -> None:
    """Si llega un AudioJobUpdated mientras la pantalla está abierta,
    la tabla refresca sin recargar manualmente."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 0

        new_audio = _audio("a_live")
        await app.audio_jobs_db.upsert(new_audio)
        # Simulamos el evento del runner.
        for listener in list(app.audio_queue._listeners):  # type: ignore[attr-defined]
            listener(AudioJobUpdated(new_audio))
        await pilot.pause()
        assert table.row_count == 1


async def test_history_refreshes_on_video_event(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 0

        new_video = _video("v_live")
        await app.db.upsert(new_video)
        for listener in list(app.queue._listeners):  # type: ignore[attr-defined]
            listener(JobUpdated(new_video))
        await pilot.pause()
        assert table.row_count == 1


async def test_filter_solo_video_excludes_audio(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.db.upsert(_video())
        await app.audio_jobs_db.upsert(_audio())
        await pilot.press("h")
        await pilot.pause()
        await pilot.click("#hist-filter-video")
        await pilot.pause()
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 1


async def test_filter_solo_audio_excludes_video(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.db.upsert(_video())
        await app.audio_jobs_db.upsert(_audio())
        await pilot.press("h")
        await pilot.pause()
        await pilot.click("#hist-filter-audio")
        await pilot.pause()
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 1


async def test_filter_todos_restores_both(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.db.upsert(_video())
        await app.audio_jobs_db.upsert(_audio())
        await pilot.press("h")
        await pilot.pause()
        await pilot.click("#hist-filter-video")
        await pilot.pause()
        await pilot.click("#hist-filter-all")
        await pilot.pause()
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 2


async def test_summary_panel_counts_correctly(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.db.upsert(_video("v1"))  # completed
        await app.audio_jobs_db.upsert(_audio("a1"))  # completed
        await app.audio_jobs_db.upsert(
            AudioJob(
                id="a_queue",
                label="x",
                script="y",
                voice_id="V",
                status=AudioJobStatus.QUEUED,
            )
        )
        await pilot.press("h")
        await pilot.pause()
        widget = app.screen.query_one("#history-summary", Static)
        rendered = str(widget.render())
        assert "Total 3" in rendered
        assert "1 en cola" in rendered
        assert "2 listos" in rendered


async def test_unsubscribe_on_unmount(tmp_path: Path) -> None:
    """Al salir, se desuscriben los DOS listeners (uno por cada queue)."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        initial_v = len(app.queue._listeners)  # type: ignore[attr-defined]
        initial_a = len(app.audio_queue._listeners)  # type: ignore[attr-defined]
        await pilot.press("h")
        await pilot.pause()
        assert len(app.queue._listeners) == initial_v + 1  # type: ignore[attr-defined]
        assert len(app.audio_queue._listeners) == initial_a + 1  # type: ignore[attr-defined]

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.queue._listeners) == initial_v  # type: ignore[attr-defined]
        assert len(app.audio_queue._listeners) == initial_a  # type: ignore[attr-defined]


async def test_history_entry_factory_methods(tmp_path: Path) -> None:
    """Smoke unitario de los factory methods de HistoryEntry."""
    video = _video()
    audio = _audio()
    e_video = HistoryEntry.from_video_job(video)
    e_audio = HistoryEntry.from_audio_job(audio)
    assert e_video.kind == "video"
    assert e_video.raw is video
    assert e_audio.kind == "audio"
    assert e_audio.raw is audio
    assert e_audio.label == "saludo"
