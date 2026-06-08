"""Smoke tests de `PresetsScreen` y `PresetFormScreen`."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, DataTable, Input

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import VoicePreset, VoiceSettings


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


async def test_presets_screen_opens_with_p_hotkey(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "PresetsScreen"


async def test_presets_screen_arranca_vacia(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        table = app.screen.query_one("#presets-table", DataTable)
        assert table.row_count == 0


async def test_presets_screen_buttons_render(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        for btn_id, expected in (
            ("preset-new", "Nuevo preset"),
            ("preset-edit", "Editar"),
            ("preset-delete", "Eliminar"),
        ):
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert str(btn.label) == expected


async def test_modal_nuevo_se_abre_y_persiste(tmp_path: Path) -> None:
    """End-to-end: abrir modal → llenar label → save → ver en tabla + en disco."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        await pilot.click("#preset-new")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "PresetFormScreen"

        # Llenar y guardar. Hacemos scroll al footer para asegurar que
        # `#save` quede en el viewport del pilot (el modal ahora trae
        # botones de Preview/Detener que lo hacen más alto).
        app.screen.query_one("#preset-label", Input).value = "narrador test"
        save_button = app.screen.query_one("#save", Button)
        save_button.scroll_visible()
        await pilot.pause()
        await pilot.click("#save")
        await pilot.pause()
        await pilot.pause()

        # Volvimos a PresetsScreen.
        assert app.screen.__class__.__name__ == "PresetsScreen"
        table = app.screen.query_one("#presets-table", DataTable)
        assert table.row_count == 1

        # JSON en disco con slug del label.
        json_path = tmp_path / "presets" / "voices" / "narrador_test.json"
        assert json_path.is_file()


async def test_modal_edicion_precarga_existente(tmp_path: Path) -> None:
    """Abrir editar sobre un preset existente debe precargar los valores."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        # Pre-cargar un preset directo desde el controller.
        preset = VoicePreset(
            id="narrador",
            label="narrador",
            voice_id="EkK5I93UQWFDigLMpZcX",
            voice_settings=VoiceSettings(stability=0.7),
            description="voz grave",
        )
        await app.presets_store.upsert(preset)

        await pilot.press("p")
        await pilot.pause()
        # Click en la primera fila para seleccionarla.
        table = app.screen.query_one("#presets-table", DataTable)
        assert table.row_count == 1
        await pilot.click("#preset-edit")
        await pilot.pause()

        assert app.screen.__class__.__name__ == "PresetFormScreen"
        label_input = app.screen.query_one("#preset-label", Input)
        assert label_input.value == "narrador"
        stability_input = app.screen.query_one("#preset-stability", Input)
        assert stability_input.value == "0.7"


async def test_delete_remueve_del_disco(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        preset = VoicePreset(
            id="borrar_me",
            label="borrar_me",
            voice_id="EkK5I93UQWFDigLMpZcX",
        )
        await app.presets_store.upsert(preset)

        json_path = tmp_path / "presets" / "voices" / "borrar_me.json"
        assert json_path.is_file()

        await pilot.press("p")
        await pilot.pause()
        await pilot.click("#preset-delete")
        await pilot.pause()
        await pilot.pause()

        assert not json_path.exists()
