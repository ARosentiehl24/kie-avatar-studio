"""Tests del módulo `app_layer.clipboard` (backends multi-OS)."""

from __future__ import annotations

from kie_avatar_studio.app_layer.clipboard import (
    ClipboardResult,
    copy_to_clipboard,
)

# --- input vacío -----------------------------------------------------------


async def test_empty_text_returns_failure() -> None:
    result = await copy_to_clipboard("")
    assert result.success is False
    assert result.backend == "none"


# --- backend nativo (mockeado) ---------------------------------------------


async def test_uses_first_available_backend(monkeypatch) -> None:
    """Si `wl-copy` está disponible, lo usa antes que `xclip`."""

    def fake_which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}" if cmd == "wl-copy" else None

    # `_run_pipe` lo mockeamos para no ejecutar realmente el comando.
    async def fake_run(backend, command, text):
        assert backend == "wl-copy"
        assert text == "hola"
        return ClipboardResult(success=True, backend=backend)

    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard.shutil.which", fake_which)
    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard._run_pipe", fake_run)

    result = await copy_to_clipboard("hola")
    assert result.success is True
    assert result.backend == "wl-copy"


async def test_skips_unavailable_backends_and_uses_next(monkeypatch) -> None:
    """Si solo `xclip` está instalado (no wl-copy), debe usarlo."""

    def fake_which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}" if cmd == "xclip" else None

    async def fake_run(backend, command, text):
        assert backend == "xclip"
        return ClipboardResult(success=True, backend=backend)

    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard.shutil.which", fake_which)
    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard._run_pipe", fake_run)

    result = await copy_to_clipboard("hola")
    assert result.backend == "xclip"


async def test_continues_when_backend_fails(monkeypatch) -> None:
    """Si `wl-copy` está pero crashea, prueba con `xclip`."""

    def fake_which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}" if cmd in {"wl-copy", "xclip"} else None

    async def fake_run(backend, command, text):
        if backend == "wl-copy":
            raise OSError("backend roto")
        return ClipboardResult(success=True, backend=backend)

    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard.shutil.which", fake_which)
    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard._run_pipe", fake_run)

    result = await copy_to_clipboard("hola")
    assert result.success is True
    assert result.backend == "xclip"


# --- fallback OSC 52 -------------------------------------------------------


async def test_falls_back_to_osc52_when_no_native_backend(monkeypatch) -> None:
    """Sin xclip/xsel/wl-copy/pbcopy/clip.exe, invoca el fallback OSC 52."""
    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard.shutil.which", lambda _cmd: None)
    calls: list[str] = []

    def fake_osc52(text: str) -> None:
        calls.append(text)

    result = await copy_to_clipboard("hola", osc52_fallback=fake_osc52)

    assert result.success is True
    assert result.backend == "osc52"
    assert calls == ["hola"]


async def test_no_backend_no_fallback_returns_failure(monkeypatch) -> None:
    """Sin nada disponible y sin fallback, devuelve `backend='none'`."""
    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard.shutil.which", lambda _cmd: None)

    result = await copy_to_clipboard("hola")

    assert result.success is False
    assert result.backend == "none"
    assert result.error is not None
    assert "xclip" in result.error or "wl-copy" in result.error


async def test_osc52_fallback_exception_propagates_as_failure(monkeypatch) -> None:
    """Si el fallback OSC 52 lanza, lo capturamos con success=False."""
    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard.shutil.which", lambda _cmd: None)

    def broken_osc52(_text: str) -> None:
        raise RuntimeError("driver caído")

    result = await copy_to_clipboard("hola", osc52_fallback=broken_osc52)

    assert result.success is False
    assert result.backend == "osc52"
    assert result.error == "driver caído"


# --- camino Windows nativo --------------------------------------------------


async def test_windows_uses_clip_exe(monkeypatch) -> None:
    """En Windows nativo sin xclip/wl-copy/pbcopy, debe seleccionar clip.exe.

    `clip.exe` viene built-in con Windows desde XP (`C:\\Windows\\System32\\
    clip.exe`) y siempre está en PATH. Este test simula el entorno Windows
    donde el único backend disponible es clip.exe y verifica que el loop
    de selección llegue hasta él (es el último en `_SYSTEM_BACKENDS`).
    """

    def fake_which(cmd: str) -> str | None:
        return r"C:\Windows\System32\clip.exe" if cmd == "clip.exe" else None

    async def fake_run(backend, command, text):
        # Validamos que el comando sea exactamente `clip.exe` (sin args),
        # que es como Windows espera recibirlo (lee stdin y copia al
        # clipboard del SO).
        assert backend == "clip.exe"
        assert command == ("clip.exe",)
        assert text == "https://kie/x.mp4"
        return ClipboardResult(success=True, backend=backend)

    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard.shutil.which", fake_which)
    monkeypatch.setattr("kie_avatar_studio.app_layer.clipboard._run_pipe", fake_run)

    result = await copy_to_clipboard("https://kie/x.mp4")
    assert result.success is True
    assert result.backend == "clip.exe"
