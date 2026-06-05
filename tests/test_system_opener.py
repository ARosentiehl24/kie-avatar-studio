"""Tests de `app_layer.system_opener` — abrir paths locales y URLs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kie_avatar_studio.app_layer.system_opener import (
    open_local_path,
    open_url,
)
from kie_avatar_studio.domain.errors import UrlValidationError


async def test_open_local_path_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "no-existe.png"
    with pytest.raises(OSError, match="no existe"):
        await open_local_path(missing)


async def test_open_url_rejects_empty() -> None:
    with pytest.raises(UrlValidationError, match="vacía"):
        await open_url("")


async def test_open_url_rejects_file_scheme() -> None:
    with pytest.raises(UrlValidationError, match="http://"):
        await open_url("file:///etc/passwd")


async def test_open_url_rejects_javascript_scheme() -> None:
    with pytest.raises(UrlValidationError, match="http://"):
        await open_url("javascript:alert(1)")


async def test_open_url_invokes_launcher_with_https_url() -> None:
    """Mockea el subprocess para no abrir el browser real durante el test."""
    captured: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return _FakeCompleted()

    # `shutil.which` debe devolver algo para que el launcher arranque.
    with (
        patch(
            "kie_avatar_studio.app_layer.system_opener.shutil.which",
            return_value="/usr/bin/xdg-open",
        ),
        patch("kie_avatar_studio.app_layer.system_opener.subprocess.run", side_effect=fake_run),
        patch("kie_avatar_studio.app_layer.system_opener.sys.platform", "linux"),
    ):
        await open_url("https://tempfile.redpandaai.co/kieai/abc/modelo.png")

    assert len(captured) == 1
    assert captured[0][0] == "xdg-open"
    assert captured[0][1] == "https://tempfile.redpandaai.co/kieai/abc/modelo.png"


async def test_open_url_raises_oserror_when_launcher_missing() -> None:
    with (
        patch("kie_avatar_studio.app_layer.system_opener.shutil.which", return_value=None),
        patch("kie_avatar_studio.app_layer.system_opener.sys.platform", "linux"),
        pytest.raises(OSError, match="no se encontró"),
    ):
        await open_url("https://example.com")


# --- Fixes Fase 2.2c.fix: path absoluto + capturar CalledProcessError -----


async def test_open_local_path_resolves_relative_path_before_launcher(tmp_path: Path) -> None:
    """`xdg-open` falla con paths relativos: el launcher debe recibir absoluto."""
    import os
    import subprocess

    target = tmp_path / "audio.mp3"
    target.write_bytes(b"FAKE-MP3")
    cwd = tmp_path
    relative = Path("audio.mp3")  # relativo a tmp_path

    captured: list[list[str]] = []

    class _FakeCompleted:
        returncode = 0

    def fake_run(args, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(list(args))
        return _FakeCompleted()

    original_cwd = Path.cwd()
    os.chdir(cwd)
    try:
        with (
            patch(
                "kie_avatar_studio.app_layer.system_opener.shutil.which",
                return_value="/usr/bin/xdg-open",
            ),
            patch(
                "kie_avatar_studio.app_layer.system_opener.subprocess.run",
                side_effect=fake_run,
            ),
            patch("kie_avatar_studio.app_layer.system_opener.sys.platform", "linux"),
        ):
            await open_local_path(relative)
    finally:
        os.chdir(original_cwd)

    assert len(captured) == 1
    launched_target = captured[0][1]
    assert Path(launched_target).is_absolute(), f"esperaba absoluto, recibí: {launched_target!r}"
    assert launched_target.endswith("audio.mp3")
    # Confirma que pasó por subprocess sin levantar la excepción anterior
    # (esa era CalledProcessError(4, ['xdg-open', 'data/voice_previews/...']))
    _ = subprocess  # placeholder para que el import quede usado


async def test_open_local_path_wraps_called_process_error_as_oserror(tmp_path: Path) -> None:
    """`xdg-open` con exit code 4 → no debe escaparse como CalledProcessError."""
    import subprocess

    target = tmp_path / "x.mp3"
    target.write_bytes(b"FAKE")

    def failing_run(args, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.CalledProcessError(returncode=4, cmd=args)

    with (
        patch(
            "kie_avatar_studio.app_layer.system_opener.shutil.which",
            return_value="/usr/bin/xdg-open",
        ),
        patch(
            "kie_avatar_studio.app_layer.system_opener.subprocess.run",
            side_effect=failing_run,
        ),
        patch("kie_avatar_studio.app_layer.system_opener.sys.platform", "linux"),
        pytest.raises(OSError, match="exit code 4"),
    ):
        await open_local_path(target)


async def test_open_local_path_wraps_timeout_expired_as_oserror(tmp_path: Path) -> None:
    """Si `xdg-open` se cuelga, el caller ve OSError legible, no TimeoutExpired."""
    import subprocess

    target = tmp_path / "x.mp3"
    target.write_bytes(b"FAKE")

    def hanging_run(args, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd=args, timeout=10)

    with (
        patch(
            "kie_avatar_studio.app_layer.system_opener.shutil.which",
            return_value="/usr/bin/xdg-open",
        ),
        patch(
            "kie_avatar_studio.app_layer.system_opener.subprocess.run",
            side_effect=hanging_run,
        ),
        patch("kie_avatar_studio.app_layer.system_opener.sys.platform", "linux"),
        pytest.raises(OSError, match="no respondió"),
    ):
        await open_local_path(target)


async def test_open_url_wraps_called_process_error_as_oserror() -> None:
    """Mismo fix para `open_url`: el navegador puede fallar también."""
    import subprocess

    def failing_run(args, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.CalledProcessError(returncode=3, cmd=args)

    with (
        patch(
            "kie_avatar_studio.app_layer.system_opener.shutil.which",
            return_value="/usr/bin/xdg-open",
        ),
        patch(
            "kie_avatar_studio.app_layer.system_opener.subprocess.run",
            side_effect=failing_run,
        ),
        patch("kie_avatar_studio.app_layer.system_opener.sys.platform", "linux"),
        pytest.raises(OSError, match="exit code 3"),
    ):
        await open_url("https://example.com")
