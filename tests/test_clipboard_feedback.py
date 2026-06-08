"""Tests del helper UI `_clipboard_feedback` (mensajes para el usuario)."""

from __future__ import annotations

from kie_avatar_studio.app_layer.clipboard import ClipboardResult
from kie_avatar_studio.ui._clipboard_feedback import copy_url_with_feedback


async def test_native_backend_message_is_success(monkeypatch) -> None:
    """Backend nativo (no OSC 52) → mensaje corto de éxito, sin URL."""

    async def fake_copy(text, *, osc52_fallback=None):
        return ClipboardResult(success=True, backend="xclip")

    monkeypatch.setattr("kie_avatar_studio.ui._clipboard_feedback.copy_to_clipboard", fake_copy)

    message, is_error = await copy_url_with_feedback(
        "https://k/x.mp4", osc52_fallback=lambda _t: None
    )

    assert is_error is False
    assert message == "✅ URL copiada al clipboard"


async def test_osc52_message_warns_about_uncertainty(monkeypatch) -> None:
    """OSC 52 → mensaje corto que aclara que fue por escape de terminal."""

    async def fake_copy(text, *, osc52_fallback=None):
        if osc52_fallback:
            osc52_fallback(text)
        return ClipboardResult(success=True, backend="osc52")

    monkeypatch.setattr("kie_avatar_studio.ui._clipboard_feedback.copy_to_clipboard", fake_copy)

    message, is_error = await copy_url_with_feedback(
        "https://k/x.mp4", osc52_fallback=lambda _t: None
    )

    assert is_error is False
    assert "terminal" in message.lower() or "escape" in message.lower()
    # En éxito no hace falta repetir la URL: ya quedó en el clipboard.
    assert "https://k/x.mp4" not in message


async def test_total_failure_marks_error_and_includes_url(monkeypatch) -> None:
    """Si nada funcionó, mensaje de error CON la URL para copiar manual."""

    async def fake_copy(text, *, osc52_fallback=None):
        return ClipboardResult(success=False, backend="none", error="sin backend")

    monkeypatch.setattr("kie_avatar_studio.ui._clipboard_feedback.copy_to_clipboard", fake_copy)

    message, is_error = await copy_url_with_feedback("https://k/x.mp4", osc52_fallback=None)

    assert is_error is True
    # Solo en el error mostramos la URL para que el user la copie a mano.
    assert "https://k/x.mp4" in message
    assert "sin backend" in message
