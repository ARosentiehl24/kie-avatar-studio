"""Tests del `SystemNotifier`/`NullNotifier`: backends + best-effort failure."""

from __future__ import annotations

import asyncio

import pytest

from kie_avatar_studio.infra import notifier as notifier_mod
from kie_avatar_studio.infra.notifier import NullNotifier, SystemNotifier


async def test_null_notifier_does_nothing() -> None:
    n = NullNotifier()
    # No debe lanzar ni hacer nada observable.
    await n.notify(title="t", message="m", success=True)


async def test_system_notifier_invokes_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """SystemNotifier delega al backend resuelto en __init__."""
    calls: list[tuple[str, str, str, bool]] = []

    async def fake_backend(app: str, title: str, message: str, success: bool) -> None:
        calls.append((app, title, message, success))

    notifier = SystemNotifier()
    # Inyectamos el backend post-init (saltea la detección de plataforma).
    notifier._backend = fake_backend  # type: ignore[assignment]
    await notifier.notify(title="hola", message="mundo", success=True)
    assert calls == [("Kie Avatar Studio", "hola", "mundo", True)]


async def test_system_notifier_swallows_backend_errors() -> None:
    """Si el backend lanza, SystemNotifier loguea y sigue sin propagar."""

    async def crashing_backend(*_args: object) -> None:
        raise RuntimeError("boom")

    notifier = SystemNotifier()
    notifier._backend = crashing_backend  # type: ignore[assignment]
    # No debe lanzar.
    await notifier.notify(title="x", message="y", success=False)


async def test_select_backend_returns_disabled_on_unknown_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifier_mod.sys, "platform", "freebsd")
    backend = notifier_mod._select_backend()
    assert backend is notifier_mod._backend_disabled


async def test_select_backend_returns_disabled_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si shutil.which devuelve None para todos los candidatos → disabled."""
    monkeypatch.setattr(notifier_mod.sys, "platform", "linux")
    monkeypatch.setattr(notifier_mod.shutil, "which", lambda _name: None)
    backend = notifier_mod._select_backend()
    assert backend is notifier_mod._backend_disabled


async def test_select_backend_returns_linux_when_notify_send_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifier_mod.sys, "platform", "linux")
    monkeypatch.setattr(notifier_mod.shutil, "which", lambda name: "/usr/bin/notify-send")
    backend = notifier_mod._select_backend()
    assert backend is notifier_mod._backend_linux


async def test_run_subprocess_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el proceso se cuelga >timeout, se mata sin lanzar."""

    class _HangingProc:
        returncode = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(100)
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    async def _fake_create(*_args: object, **_kw: object) -> _HangingProc:
        return _HangingProc()

    monkeypatch.setattr(notifier_mod.asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr(notifier_mod, "_NOTIFY_TIMEOUT_SECONDS", 0.05)
    # No debe lanzar.
    await notifier_mod._run_subprocess("dummy", "arg")


async def test_run_subprocess_logs_non_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit codes != 0 no propagan: se loguean a DEBUG y se ignoran."""

    class _FailProc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"boom"

    async def _fake_create(*_args: object, **_kw: object) -> _FailProc:
        return _FailProc()

    monkeypatch.setattr(notifier_mod.asyncio, "create_subprocess_exec", _fake_create)
    # No debe lanzar.
    await notifier_mod._run_subprocess("dummy")
