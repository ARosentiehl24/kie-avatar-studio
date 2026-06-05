"""Tests para `app_layer.audio_player.AudioPlayer` — play / stop / cancel."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kie_avatar_studio.app_layer import audio_player as ap_module
from kie_avatar_studio.app_layer.audio_player import AudioPlayer


class _FakeProc:
    """Stub mínimo de `subprocess.Popen` para tests."""

    def __init__(self, *args: object, alive: bool = True, **_kwargs: object) -> None:
        self.cmd = list(args[0]) if args else []  # type: ignore[arg-type]
        self._alive = alive
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


async def _fake_downloader(_url: str, destination: Path) -> None:
    # Sync IO en fake downloader de test: bytes contados, sin event loop bloqueada.
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"FAKE-MP3")


def _player(tmp_path: Path) -> AudioPlayer:
    return AudioPlayer(
        downloader=_fake_downloader,
        voice_preview_dir=tmp_path / "voice_previews",
        audio_cache_dir=tmp_path / "audio_cache",
    )


def _patch_player_spawn(
    monkeypatch: pytest.MonkeyPatch,
    proc: _FakeProc | None,
) -> list[Path]:
    """Patchea `_try_audio_players` para que devuelva `proc` y rastree llamadas."""
    calls: list[Path] = []

    def fake_try(path: Path) -> object:
        calls.append(path)
        return proc

    monkeypatch.setattr(ap_module, "_try_audio_players", fake_try)
    return calls


async def test_play_voice_preview_spawns_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _FakeProc(["mpv", "x"])
    _patch_player_spawn(monkeypatch, proc)
    player = _player(tmp_path)

    await player.play_voice_preview("https://x.com/voice.mp3")

    assert player.is_playing() is True
    # El proceso current debe ser el que devolvió el spawn.
    assert player._current is proc  # type: ignore[attr-defined]


async def test_play_audio_uses_audio_cache_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _FakeProc(["mpv", "x"])
    spawn_calls = _patch_player_spawn(monkeypatch, proc)
    player = _player(tmp_path)

    await player.play_audio("https://x.com/audio.mp3")

    assert len(spawn_calls) == 1
    assert spawn_calls[0].parent == (tmp_path / "audio_cache").resolve()
    assert spawn_calls[0].name == "audio.mp3"


async def test_stop_terminates_current_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _FakeProc(["mpv", "x"])
    _patch_player_spawn(monkeypatch, proc)
    player = _player(tmp_path)

    await player.play_voice_preview("https://x.com/voice.mp3")
    assert player.is_playing() is True

    await player.stop()

    assert proc.terminated is True
    assert player.is_playing() is False
    assert player._current is None  # type: ignore[attr-defined]


async def test_stop_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Llamar stop dos veces seguidas no rompe ni intenta terminar None."""
    _patch_player_spawn(monkeypatch, None)
    player = _player(tmp_path)

    await player.stop()  # nunca hubo proceso
    await player.stop()  # idempotente

    assert player.is_playing() is False


async def test_play_again_cancels_previous_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproducir un audio nuevo cancela el anterior automáticamente."""
    proc_a = _FakeProc(["mpv", "a"])
    proc_b = _FakeProc(["mpv", "b"])
    procs = iter([proc_a, proc_b])

    def fake_try(_path: Path) -> object:
        return next(procs)

    monkeypatch.setattr(ap_module, "_try_audio_players", fake_try)
    player = _player(tmp_path)

    await player.play_voice_preview("https://x.com/a.mp3")
    await player.play_voice_preview("https://x.com/b.mp3")

    assert proc_a.terminated is True  # el anterior se canceló
    assert player._current is proc_b  # type: ignore[attr-defined]


async def test_is_playing_self_cleans_when_process_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el proceso terminó solo (audio se acabó), is_playing devuelve False
    y limpia el slot para no quedar con referencias zombi."""
    proc = _FakeProc(["mpv", "x"], alive=False)  # ya terminó
    _patch_player_spawn(monkeypatch, proc)
    player = _player(tmp_path)

    await player.play_voice_preview("https://x.com/voice.mp3")
    # poll() devuelve 0 (no None) → is_playing limpia y devuelve False.
    assert player.is_playing() is False
    assert player._current is None  # type: ignore[attr-defined]


async def test_falls_back_to_open_local_when_no_cli_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin CLI player disponible, debe caer a open_local_path."""
    _patch_player_spawn(monkeypatch, None)
    opened: list[Path] = []

    async def fake_open(p: Path) -> None:
        opened.append(p)

    monkeypatch.setattr(ap_module, "open_local_path", fake_open)
    player = _player(tmp_path)

    await player.play_voice_preview("https://x.com/voice.mp3")

    assert len(opened) == 1
    assert opened[0].name == "voice.mp3"
    # `open_local_path` no devuelve Popen; el current queda None.
    assert player._current is None  # type: ignore[attr-defined]


async def test_includes_install_hint_when_all_methods_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_player_spawn(monkeypatch, None)

    async def failing_open(_p: Path) -> None:
        raise OSError("xdg-open exit 4")

    monkeypatch.setattr(ap_module, "open_local_path", failing_open)
    player = _player(tmp_path)

    with pytest.raises(OSError, match=r"mpv.*ffplay.*mpg123"):
        await player.play_voice_preview("https://x.com/voice.mp3")


# --- Tests del helper `_terminate_process` con timing real (rápidos) -----


def test_terminate_process_skips_already_dead() -> None:
    proc = _FakeProc(["mpv", "x"], alive=False)
    ap_module._terminate_process(proc)  # type: ignore[arg-type]
    # No debería haber tocado terminate ni kill — el proceso ya estaba muerto.
    assert proc.terminated is False
    assert proc.killed is False


def test_terminate_process_kills_when_terminate_times_out() -> None:
    """Si el proceso ignora SIGTERM, debe pasar a SIGKILL."""

    class _StubbornProc(_FakeProc):
        def wait(self, timeout: float | None = None) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout or 0)
            return 0

    proc = _StubbornProc(["mpv", "x"])
    ap_module._terminate_process(proc)  # type: ignore[arg-type]

    assert proc.terminated is True
    assert proc.killed is True


def test_terminate_process_swallows_os_error() -> None:
    """`OSError` durante terminate (proceso ya muerto entre poll y kill) no escapa."""

    class _RacingProc(_FakeProc):
        def terminate(self) -> None:
            raise OSError("no such process")

    proc = _RacingProc(["mpv", "x"])
    # No debe lanzar; nunca queremos que stop() falle por race conditions.
    ap_module._terminate_process(proc)  # type: ignore[arg-type]


# --- Filename derivation -------------------------------------------------


def test_filename_from_url_uses_last_segment() -> None:
    assert ap_module._filename_from_url("https://x.com/a/b/voice.mp3") == "voice.mp3"


def test_filename_from_url_strips_query_string() -> None:
    assert ap_module._filename_from_url("https://x.com/voice.mp3?token=abc&ts=123") == "voice.mp3"


def test_filename_from_url_strips_fragment() -> None:
    assert ap_module._filename_from_url("https://x.com/voice.mp3#t=10") == "voice.mp3"


def test_filename_from_url_falls_back_to_hash_when_no_path() -> None:
    result = ap_module._filename_from_url("https://x.com")
    assert result.endswith(".mp3")
    assert len(result) == 16 + len(".mp3")  # sha256 truncado a 16 chars + .mp3


# --- Concurrency ---------------------------------------------------------


async def test_concurrent_plays_serialize_through_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dos play_voice_preview en paralelo no debe corromper el state.

    El lock garantiza que `_stop_locked` del segundo vea el current del
    primero (no None) y lo termine antes de spawnear el suyo."""
    import asyncio

    procs = [_FakeProc(["mpv", str(i)]) for i in range(2)]
    proc_iter = iter(procs)

    def fake_try(_path: Path) -> object:
        return next(proc_iter)

    monkeypatch.setattr(ap_module, "_try_audio_players", fake_try)
    player = _player(tmp_path)

    await asyncio.gather(
        player.play_voice_preview("https://x.com/a.mp3"),
        player.play_voice_preview("https://x.com/b.mp3"),
    )

    # Uno de los dos debió ser terminado; el otro queda como current.
    terminated_count = sum(1 for p in procs if p.terminated)
    assert terminated_count == 1
    assert player.is_playing() is True
