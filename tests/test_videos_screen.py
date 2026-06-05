"""Smoke tests de `VideosScreen` + `NewVideoFormScreen`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from textual.widgets import Button, DataTable, Static

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.events import JobUpdated
from kie_avatar_studio.domain.models import (
    GeneratedAudio,
    JobStatus,
    UploadedImage,
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


def _img(image_id: str = "img-1") -> UploadedImage:
    return UploadedImage(
        id=image_id,
        label="avatar Maria",
        local_path=f"/tmp/{image_id}.png",
        kie_url=f"https://tempfile.redpandaai.co/kieai/{image_id}.png",
        kie_file_path=f"kieai/{image_id}.png",
        file_size=12345,
        mime_type="image/png",
        uploaded_at=datetime.now(UTC),
    )


def _aud(audio_id: str = "aud-1") -> GeneratedAudio:
    return GeneratedAudio(
        id=audio_id,
        label="saludo Maria",
        script="Hola",
        voice_id="V",
        kie_url=f"https://tempfile.redpandaai.co/kieai/{audio_id}.mp3",
        kie_file_path=f"kieai/{audio_id}.mp3",
        generated_at=datetime.now(UTC),
    )


async def test_videos_screen_opens_with_n_hotkey(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "VideosScreen"


async def test_videos_screen_buttons_render(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        for btn_id, expected in (
            ("vid-new", "Nuevo video"),
            ("vid-open", "▶ Abrir mp4"),
            ("vid-copy-url", "Copiar URL"),
            ("vid-cancel-job", "Cancelar job"),
            ("vid-retry", "Reintentar"),
            ("vid-delete", "Quitar"),
        ):
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert str(btn.label) == expected


async def test_new_video_modal_opens_from_videos_screen(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.click("#vid-new")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "NewVideoFormScreen"


async def test_new_video_modal_lists_available_assets(tmp_path: Path) -> None:
    """El modal debe poblar los Select con las imágenes y audios reales."""
    from textual.widgets import Select

    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await app.images_db.upsert(_img())
        await app.audios_db.upsert(_aud())
        await pilot.press("n")
        await pilot.pause()
        await pilot.click("#vid-new")
        await pilot.pause()

        image_select = app.screen.query_one("#video-image", Select)
        audio_select = app.screen.query_one("#video-audio", Select)
        assert image_select.value == "img-1"
        assert audio_select.value == "aud-1"


async def test_videos_screen_lists_video_jobs(tmp_path: Path) -> None:
    """La tabla muestra VideoJobs en cualquier estado."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        job = VideoJob(
            id="vid-test-1",
            prompt="Plano cerrado",
            image_url="https://kie/img.png",
            audio_url="https://kie/aud.mp3",
            status=JobStatus.COMPLETED,
        )
        await app.db.upsert(job)
        await pilot.press("n")
        await pilot.pause()
        table = app.screen.query_one("#videos-table", DataTable)
        assert table.row_count == 1


async def test_videos_screen_refreshes_on_event(tmp_path: Path) -> None:
    """Si llega un JobUpdated, la tabla refresca en vivo sin polling."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        table = app.screen.query_one("#videos-table", DataTable)
        assert table.row_count == 0

        new_job = VideoJob(
            id="vid-live",
            prompt="p",
            image_url="https://kie/img.png",
            audio_url="https://kie/aud.mp3",
            status=JobStatus.QUEUED,
        )
        await app.db.upsert(new_job)
        # Simulamos lo que hace `QueueManager._notify`.
        for listener in list(app.queue._listeners):  # type: ignore[attr-defined]
            listener(JobUpdated(new_job))
        await pilot.pause()
        assert table.row_count == 1


async def test_videos_screen_unsubscribes_on_unmount(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        initial = len(app.queue._listeners)  # type: ignore[attr-defined]
        await pilot.press("n")
        await pilot.pause()
        assert len(app.queue._listeners) == initial + 1  # type: ignore[attr-defined]

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.queue._listeners) == initial  # type: ignore[attr-defined]


async def test_videos_summary_counters_format(tmp_path: Path) -> None:
    """El panel de contadores arriba muestra los conteos por estado."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        await app.db.upsert(
            VideoJob(
                id="v1",
                prompt="x",
                image_url="https://k/i.png",
                audio_url="https://k/a.mp3",
                status=JobStatus.COMPLETED,
            )
        )
        await app.db.upsert(
            VideoJob(
                id="v2",
                prompt="x",
                image_url="https://k/i.png",
                audio_url="https://k/a.mp3",
                status=JobStatus.QUEUED,
            )
        )
        await pilot.press("n")
        await pilot.pause()
        widget = app.screen.query_one("#videos-counters", Static)
        rendered = str(widget.render())
        assert "1 en cola" in rendered
        assert "1 listos" in rendered
