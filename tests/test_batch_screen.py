"""Smoke tests de `BatchScreen`: render + interacciones básicas."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, DataTable, Static

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings


def _make_image(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)


def _build_app(tmp_path: Path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        batch_jobs_dir=tmp_path / "batch_jobs",
        workflows_dir=tmp_path / "workflows",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    app = KieAvatarStudioApp(settings=settings)

    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    return app


async def test_batch_screen_opens_with_b_hotkey(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "BatchScreen"


async def test_batch_screen_empty_dir_shows_zero_rows(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        for _ in range(3):
            await pilot.pause()
        table = app.screen.query_one("#batch-table", DataTable)
        assert table.row_count == 0
        counters = app.screen.query_one("#batch-counters", Static)
        assert "Sin lotes" in str(counters.render())


async def test_batch_screen_lists_valid_and_invalid_entries(tmp_path: Path) -> None:
    batch = tmp_path / "batch_jobs"
    batch.mkdir()
    # lote válido
    ok = batch / "video_001"
    ok.mkdir()
    (ok / "script.txt").write_text("hola")
    _make_image(ok / "modelo.png")
    # lote inválido (sin imagen)
    bad = batch / "video_002"
    bad.mkdir()
    (bad / "script.txt").write_text("hola")

    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        for _ in range(5):
            await pilot.pause()
        table = app.screen.query_one("#batch-table", DataTable)
        assert table.row_count == 2
        counters = app.screen.query_one("#batch-counters", Static)
        rendered = str(counters.render())
        assert "1 listos" in rendered
        assert "1 con error" in rendered


async def test_batch_screen_buttons_render(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        for btn_id in ("batch-enqueue-all", "batch-enqueue-one", "batch-refresh"):
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert btn.label  # tiene texto, no vacío


async def test_batch_screen_refresh_action(tmp_path: Path) -> None:
    """Crear una carpeta DESPUÉS de abrir la screen y refrescar la detecta."""
    batch = tmp_path / "batch_jobs"
    batch.mkdir()
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        for _ in range(3):
            await pilot.pause()
        table = app.screen.query_one("#batch-table", DataTable)
        assert table.row_count == 0
        # Creo un lote válido y refresco con R
        new = batch / "video_001"
        new.mkdir()
        (new / "script.txt").write_text("hola")
        _make_image(new / "modelo.png")
        await pilot.press("r")
        for _ in range(3):
            await pilot.pause()
        table = app.screen.query_one("#batch-table", DataTable)
        assert table.row_count == 1


async def test_batch_screen_enqueue_one_without_selection_shows_error(tmp_path: Path) -> None:
    """Clic en 'Encolar seleccionado' sin selección debe mostrar error."""
    batch = tmp_path / "batch_jobs"
    batch.mkdir()
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        for _ in range(3):
            await pilot.pause()
        await pilot.click("#batch-enqueue-one")
        for _ in range(3):
            await pilot.pause()
        bar = app.screen.query_one("#status-bar", Static)
        assert "Seleccion" in str(bar.render())
