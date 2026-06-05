"""Smoke test de `LogsScreen` y la integración con `App._handle_exception`."""

from __future__ import annotations

import time

from loguru import logger
from textual.widgets import RichLog

from kie_avatar_studio.app import KieAvatarStudioApp
from kie_avatar_studio.config import Settings


def _build_app(tmp_path) -> KieAvatarStudioApp:
    settings = Settings(
        kie_api_key="",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
        inputs_dir=tmp_path / "inputs",
        presets_dir=tmp_path / "presets",
        logs_dir=tmp_path / "logs",
    )
    settings.ensure_dirs()
    return KieAvatarStudioApp(settings=settings)


def _flush_loguru() -> None:
    logger.complete()
    time.sleep(0.1)


async def test_logs_screen_opens_with_l_hotkey(tmp_path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LogsScreen"


async def test_logs_screen_shows_recent_entries(tmp_path) -> None:
    app = _build_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        logger.info("marcador-visible-1234")
        _flush_loguru()
        await pilot.press("l")
        await pilot.pause()
        view = app.screen.query_one("#logs-view", RichLog)
        # `RichLog.lines` expone las líneas renderizadas
        rendered = "\n".join(str(line) for line in view.lines)
        assert "marcador-visible-1234" in rendered


async def test_unhandled_exception_lands_in_log_file(tmp_path) -> None:
    """El hook `_handle_exception` debe loguear el traceback al archivo aunque
    el resto del flujo de Textual luego cierre la app. Testeamos el efecto
    aislado (sin Pilot), porque `super()._handle_exception` termina cerrando
    la TUI y eso no es lo que estamos verificando.
    """
    from contextlib import suppress

    app = _build_app(tmp_path)
    try:
        raise RuntimeError("bug-simulado-en-handler-9999")
    except RuntimeError as exc:
        with suppress(BaseException):
            app._handle_exception(exc)
    _flush_loguru()
    content = app.log_file.read_text()
    assert "bug-simulado-en-handler-9999" in content
    assert "RuntimeError" in content
