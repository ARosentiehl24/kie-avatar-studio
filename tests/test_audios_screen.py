"""Smoke tests de `AudiosScreen` y `GenerateAudioFormScreen`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from textual.widgets import Button, DataTable, Select, TextArea

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings
from kie_avatar_studio.domain.models import AudioJob, AudioJobStatus, GeneratedAudio


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

    # En los smoke tests no queremos que el indicador de saldo haga una
    # request HTTP real (sin transport mockeado va a 5xx y la excepción
    # contamina los logs aunque el `except Exception` la atrape). Forzamos
    # `None` => "saldo no disponible" sin tocar la red.
    async def fake_check() -> float | None:
        return None

    app._check_credits = fake_check  # type: ignore[method-assign]
    return app


def _persisted_audio(audio_id: str = "aud-1") -> GeneratedAudio:
    return GeneratedAudio(
        id=audio_id,
        label="Saludo",
        script="Hola mundo",
        voice_id="EkK5I93UQWFDigLMpZcX",
        kie_url=f"https://tempfile.redpandaai.co/kieai/abc/{audio_id}.mp3",
        kie_file_path=f"kieai/abc/{audio_id}.mp3",
        generated_at=datetime.now(UTC),
    )


def _persisted_job(
    audio_id: str = "aud-1", status: AudioJobStatus = AudioJobStatus.COMPLETED
) -> AudioJob:
    """`AudioJob` espejo del `_persisted_audio` (mismo id por idempotencia)."""
    return AudioJob(
        id=audio_id,
        label="Saludo",
        script="Hola mundo",
        voice_id="EkK5I93UQWFDigLMpZcX",
        status=status,
        kie_url=f"https://tempfile.redpandaai.co/kieai/abc/{audio_id}.mp3"
        if status == AudioJobStatus.COMPLETED
        else None,
        kie_file_path=f"kieai/abc/{audio_id}.mp3" if status == AudioJobStatus.COMPLETED else None,
    )


async def _persist_completed_audio(app: KieAvatarStudioApp, audio_id: str = "aud-1") -> None:
    """Helper: persiste un audio COMPLETED en ambas tablas (audio_jobs + generated_audios)."""
    await app.audios_db.upsert(_persisted_audio(audio_id))
    await app.audio_jobs_db.upsert(_persisted_job(audio_id))


async def test_audios_screen_opens_with_a_hotkey(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "AudiosScreen"


async def test_audios_screen_lists_audio_jobs(tmp_path: Path) -> None:
    """La tabla muestra AudioJobs (incluye estado, no solo completados)."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await _persist_completed_audio(app)
        # Insertamos también un job en cola para verificar que ambos se ven.
        await app.audio_jobs_db.upsert(_persisted_job("aud-queued", AudioJobStatus.QUEUED))
        await pilot.press("a")
        await pilot.pause()
        table = app.screen.query_one("#audios-table", DataTable)
        assert table.row_count == 2


async def test_audios_screen_buttons_render_with_labels(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        for btn_id, expected in (
            ("aud-generate", "Generar"),
            ("aud-listen", "Escuchar"),
            ("aud-stop", "Detener"),
            ("aud-copy-url", "Copiar URL"),
            ("aud-cancel-job", "Cancelar job"),
            ("aud-retry", "Reintentar"),
            ("aud-delete", "Quitar"),
        ):
            btn = app.screen.query_one(f"#{btn_id}", Button)
            assert str(btn.label) == expected


async def test_table_does_not_render_clickable_url(tmp_path: Path) -> None:
    """Regresión: la celda no debe contener https://."""
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await _persist_completed_audio(app)
        await pilot.press("a")
        await pilot.pause()
        table = app.screen.query_one("#audios-table", DataTable)
        for col_index in range(len(table.columns)):
            value = str(table.get_cell_at((0, col_index)))
            assert "https://" not in value, f"col {col_index}: {value!r}"
            assert "%E2%80%A6" not in value


async def test_handle_listen_uses_audio_player_with_clipboard_backup(
    tmp_path: Path, monkeypatch
) -> None:
    """El botón Escuchar invoca audio_player.play_audio y copia la URL al clipboard.

    Tras el refactor multi-backend de `app_layer.clipboard`, el
    `osc52_fallback` (= `app.copy_to_clipboard`) SOLO se invoca si
    todos los backends del SO fallaron primero. En CI/Windows con
    `clip.exe` disponible, ese backend triunfa antes y el mock nunca
    se llama. Forzamos el path OSC 52 vaciando la tabla de backends.
    """
    from kie_avatar_studio.app_layer import clipboard as cb_module

    monkeypatch.setattr(cb_module, "_SYSTEM_BACKENDS", ())

    app = _build_app(tmp_path)
    play_calls: list[str] = []
    clipboard_calls: list[str] = []

    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await _persist_completed_audio(app)

        await pilot.press("a")
        await pilot.pause()

        async def fake_play(u: str) -> None:
            play_calls.append(u)

        def fake_clipboard(text: str) -> None:
            clipboard_calls.append(text)

        app.audio_player.play_audio = fake_play  # type: ignore[method-assign]
        app.copy_to_clipboard = fake_clipboard  # type: ignore[method-assign]
        await pilot.click("#aud-listen")
        await pilot.pause()

    expected_url = _persisted_audio().kie_url
    assert play_calls == [expected_url]
    assert clipboard_calls == [expected_url]


async def test_handle_stop_invokes_audio_player_stop(tmp_path: Path) -> None:
    """El botón Detener llama a audio_player.stop()."""
    app = _build_app(tmp_path)
    stop_calls: list[bool] = []

    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        async def fake_stop() -> None:
            stop_calls.append(True)

        app.audio_player.stop = fake_stop  # type: ignore[method-assign]
        await pilot.click("#aud-stop")
        await pilot.pause()
        # Capturamos las llamadas dentro del context para no contar la del
        # `on_unmount` que limpia al cerrar la app.
        click_stop_calls = list(stop_calls)

    assert click_stop_calls == [True]


async def test_handle_copy_url_copies_to_clipboard(tmp_path: Path, monkeypatch) -> None:
    """Verifica que 'Copiar URL' termina invocando el clipboard del sistema.

    Forzamos el path OSC 52 (mismo motivo que `test_handle_listen_*`):
    en plataformas con backend del SO disponible, el `osc52_fallback`
    no se llama y el mock queda vacío.
    """
    from kie_avatar_studio.app_layer import clipboard as cb_module

    monkeypatch.setattr(cb_module, "_SYSTEM_BACKENDS", ())

    app = _build_app(tmp_path)
    clipboard_calls: list[str] = []

    async with app.run_test(size=(140, 35)) as pilot:
        await pilot.pause()
        await _persist_completed_audio(app)

        await pilot.press("a")
        await pilot.pause()
        screen = app.screen
        screen.app.copy_to_clipboard = clipboard_calls.append  # type: ignore[method-assign,assignment]
        await pilot.click("#aud-copy-url")
        await pilot.pause()

    assert clipboard_calls == [_persisted_audio().kie_url]


# --- GenerateAudioFormScreen smoke ---------------------------------------


async def test_generate_form_opens_from_audios_screen(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.click("#aud-generate")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "GenerateAudioFormScreen"


async def test_generate_form_voice_select_populated_with_builtin(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.click("#aud-generate")
        await pilot.pause()
        select = app.screen.query_one("#audio-voice", Select)
        # 67 voces builtin
        assert len(list(select._options)) == 67


async def test_generate_form_script_counter_updates_on_text_change(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.click("#aud-generate")
        await pilot.pause()
        text_area = app.screen.query_one("#audio-script", TextArea)
        text_area.text = "hola"
        await pilot.pause()
        counter = app.screen.query_one("#audio-script-counter")
        assert "4 /" in str(counter.render())


# --- Generar y otro (reabrir modal con voz/settings precargados) ----------


async def test_generate_form_result_keep_open_false_by_default(tmp_path: Path) -> None:
    """El boton 'Generar' devuelve un Result con keep_open=False (default)."""
    from kie_avatar_studio.ui.screens.generate_audio import GenerateAudioFormResult

    r = GenerateAudioFormResult(label="x", script="hola", voice_id="V", voice_settings=None)
    assert r.keep_open is False


async def test_generate_form_keep_open_true_reopens_modal(tmp_path: Path) -> None:
    """Cuando keep_open=True, AudiosScreen vuelve a abrir el modal con
    los mismos voice/settings precargados (UX rapida para crear varios
    audios con la misma config)."""
    from kie_avatar_studio.ui.screens.generate_audio import (
        GenerateAudioFormResult,
        GenerateAudioFormScreen,
    )

    app = _build_app(tmp_path)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        # Stub del controller para que no haga HTTP real.

        async def fake_enqueue(
            label: str, script: str, voice_id: str, voice_settings: object = None
        ) -> AudioJob:
            return AudioJob(
                id="aud_stub",
                label=label,
                script=script,
                voice_id=voice_id,
                status=AudioJobStatus.QUEUED,
            )

        app.audios_controller.enqueue_generation = fake_enqueue  # type: ignore[method-assign]
        # Forzamos el dismiss del modal con keep_open=True.
        screen = app.screen  # AudiosScreen
        screen._on_generate_form_dismissed(  # type: ignore[attr-defined]
            GenerateAudioFormResult(
                label="hola1",
                script="texto 1",
                voice_id="N2lVS1w4EtoT3dr4eOWO",
                voice_settings=None,
                keep_open=True,
            )
        )
        await pilot.pause()
        # El modal se reabrio encima.
        assert isinstance(app.screen, GenerateAudioFormScreen)
        # Con la voz precargada (no la default).
        from textual.widgets import Select

        select = app.screen.query_one("#audio-voice", Select)
        assert select.value == "N2lVS1w4EtoT3dr4eOWO"
