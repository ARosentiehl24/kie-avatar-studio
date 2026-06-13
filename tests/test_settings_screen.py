"""Smoke test de la pantalla `SettingsScreen`.

Verifica navegación básica y que el flujo de agregar/activar/eliminar key
funciona end-to-end (sin tocar Kie real).
"""

from __future__ import annotations

from textual.widgets import DataTable

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings


def _build_app(tmp_path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        batch_jobs_dir=tmp_path / "batch_jobs",
        workflows_dir=tmp_path / "workflows",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return KieAvatarStudioApp(settings=settings)


async def test_settings_screen_opens_from_main_menu(tmp_path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")  # atajo global
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SettingsScreen"


async def test_settings_screen_tabs_have_keys_table(tmp_path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        table = app.screen.query_one("#keys-table", DataTable)
        assert table is not None
        assert table.row_count == 0  # sin keys todavía


async def test_keys_workflow_add_activate_delete(tmp_path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Agrega una key directamente vía el controller (más estable que el modal en tests).
        await app.keys_controller.add_key("dev", "Cuenta dev", "sk-12345678abcd")
        await app.keys_controller.set_active("dev")
        active = await app.keys_controller.get_active()
        assert active is not None
        assert active.id == "dev"

        await pilot.press("c")
        await pilot.pause()
        table = app.screen.query_one("#keys-table", DataTable)
        assert table.row_count == 1

        # Eliminamos vía controller y refrescamos.
        await app.keys_controller.delete_key("dev")
        await app.screen._refresh_keys_table()  # type: ignore[attr-defined]
        assert table.row_count == 0


async def test_add_key_button_opens_modal_without_worker_error(tmp_path) -> None:
    """Regresión: apretar 'Agregar' tiraba NoActiveWorker porque usábamos
    push_screen_wait fuera de un @work. Ahora usamos push_screen + callback.

    Usamos size=(80, 40) explícito porque el viewport default 80x24 no
    alcanza para mostrar SettingsScreen con la escala de spacing mínimo 2
    (header + título + tab-bar + tabla + margin-top: 2 + actions-row +
    status-bar + footer). En la app real el viewport siempre es ≥30 filas.
    """
    app = _build_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        # Click en el botón "Agregar" — antes esto rompía con NoActiveWorker.
        await pilot.click("#key-add")
        await pilot.pause()
        # El modal KeyFormScreen quedó montado encima.
        assert app.screen.__class__.__name__ == "KeyFormScreen"
        # Escape lo cierra sin guardar.
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SettingsScreen"
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        # Click en el botón "Agregar" — antes esto rompía con NoActiveWorker.
        await pilot.click("#key-add")
        await pilot.pause()
        # El modal KeyFormScreen quedó montado encima.
        assert app.screen.__class__.__name__ == "KeyFormScreen"
        # Escape lo cierra sin guardar.
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SettingsScreen"


async def test_all_buttons_render_with_full_label(tmp_path) -> None:
    """Regresión: los botones de las tabs (especialmente las 4 chiquitas de
    API Keys y los 'Guardar X' del resto) deben pintarse con su label
    completo aunque la terminal sea angosta.
    """
    from textual.widgets import Button, TabbedContent

    app = _build_app(tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        # API Keys tab: las 4 acciones deben tener región con width útil.
        for btn_id in ("key-add", "key-activate", "key-test", "key-delete"):
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert btn.region.width >= 9, f"{btn_id} colapsó a width={btn.region.width}"
            assert str(btn.label).strip(), f"{btn_id} sin label visible"
        # Las otras tabs cada una con un botón crítico cuyo label cabe.
        tc = app.screen.query_one(TabbedContent)
        for tab_id, btn_id, label in (
            ("tab-endpoints", "save-endpoints", "Guardar endpoints"),
            ("tab-execution", "save-execution", "Guardar ejecución"),
            ("tab-concurrency", "save-concurrency", "Guardar concurrencia"),
            ("tab-defaults", "save-defaults", "Guardar defaults"),
            ("tab-maintenance", "cleanup-runtime-db", "Limpiar DB runtime"),
        ):
            tc.active = tab_id
            await pilot.pause()
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert str(btn.label) == label
            assert btn.region.width >= len(label) + 2, (
                f"{btn_id} width={btn.region.width} no cubre label={label!r}"
            )


async def test_concurrency_tab_exposes_all_subsystem_limits(tmp_path) -> None:
    """La pestaña Concurrencia muestra los 5 spinners + el botón Guardar."""
    from textual.widgets import Input, TabbedContent

    app = _build_app(tmp_path)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        tc = app.screen.query_one(TabbedContent)
        tc.active = "tab-concurrency"
        await pilot.pause()
        for input_id, expected in (
            ("#max-parallel-audio", str(app.settings.max_parallel_audio_jobs)),
            ("#max-parallel-image", str(app.settings.max_parallel_image_jobs)),
            ("#max-parallel-video", str(app.settings.max_parallel_video_jobs)),
            ("#max-parallel-upload", str(app.settings.max_parallel_upload_jobs)),
            ("#max-parallel-download", str(app.settings.max_parallel_download_jobs)),
        ):
            field = app.screen.query_one(input_id, Input)
            assert field.value == expected, (
                f"{input_id} value={field.value!r} expected={expected!r}"
            )
