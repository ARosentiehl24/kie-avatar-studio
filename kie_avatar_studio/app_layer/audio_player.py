"""Reproductor de audio compartido con state del proceso actual.

Encapsula la cadena `mpv` → `ffplay` → `mpg123` → `xdg-open` y mantiene una
referencia al `Popen` actual para poder cancelarlo. Sin un único punto que
trackee el proceso, los previews que el usuario lanza con `🔊 Preview`
quedarían corriendo en background sin forma de pararlos.

Reglas:
- Una sola reproducción a la vez: lanzar una nueva detiene la anterior.
- `stop()` termina con `SIGTERM`, con fallback a `SIGKILL` si no responde.
- Thread-safety via `asyncio.Lock` para que clicks rápidos no creen carrera
  entre spawn y stop.
- El fallback `xdg-open` no devuelve `Popen` (es fire-and-forget del launcher
  del SO), así que en ese camino `is_playing()` no puede tracker el proceso.
  Limitación documentada — `mpv`/`ffplay` cubren el caso interesante.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from ..domain.policies import validate_http_url
from .system_opener import open_local_path

AudioDownloader = Callable[[str, Path], Awaitable[None]]

# Reproductores CLI multi-formato probados antes de caer al launcher genérico
# del SO. Razón: en muchas instalaciones Linux minimal no hay un MIME handler
# asociado a `audio/mpeg`, y `xdg-open` devuelve exit 4 ("the action failed").
# Se lanzan con `Popen` + `start_new_session=True` para que sigan reproduciendo
# si la app TUI termina antes que el audio. Orden de preferencia:
# - mpv: el más liviano y ubicuo, no abre ventana con `--no-video`.
# - ffplay: viene con ffmpeg (típico en estaciones de trabajo).
# - mpg123: específico para MP3, footprint mínimo.
_AUDIO_PLAYERS: Final[tuple[tuple[str, ...], ...]] = (
    ("mpv", "--no-video", "--really-quiet"),
    ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"),
    ("mpg123", "-q"),
)

# Tiempo que esperamos a que un `terminate()` (SIGTERM) cierre limpio antes
# de pasar a `kill()` (SIGKILL). `mpv`/`ffplay`/`mpg123` reaccionan inmediato
# a SIGTERM; el 2s es un margen defensivo para sistemas cargados.
_TERMINATE_GRACE_SECONDS: Final[float] = 2.0


class AudioPlayer:
    """Reproductor singleton de audios MP3 con cache local.

    Pensado para inyectarse desde el composition root y compartirse entre
    pantallas: una sola reproducción a la vez en toda la app. Sin instancias
    paralelas porque el usuario solo tiene un par de oídos.
    """

    def __init__(
        self,
        downloader: AudioDownloader,
        voice_preview_dir: Path,
        audio_cache_dir: Path,
    ) -> None:
        self._downloader = downloader
        self._voice_preview_dir = voice_preview_dir
        self._audio_cache_dir = audio_cache_dir
        self._current: subprocess.Popen[bytes] | None = None
        self._lock = asyncio.Lock()

    async def play_voice_preview(self, url: str) -> None:
        """Reproduce el MP3 estático de preview de una voz built-in.

        Cachea en `voice_preview_dir`. Cancela cualquier audio anterior.
        """
        await self._play(url, self._voice_preview_dir)

    async def play_audio(self, url: str) -> None:
        """Reproduce un audio generado por Kie TTS.

        Cachea en `audio_cache_dir`. Cancela cualquier audio anterior.
        """
        await self._play(url, self._audio_cache_dir)

    async def stop(self) -> None:
        """Termina el audio en curso, si hay uno. Idempotente."""
        async with self._lock:
            await self._stop_locked()

    def is_playing(self) -> bool:
        """`True` si hay un Popen vivo. Best-effort: el fallback xdg-open
        no se trackea (es fire-and-forget del launcher del SO).
        """
        if self._current is None:
            return False
        if self._current.poll() is None:
            return True
        # Proceso terminó solo (el audio se acabó). Limpia el slot para
        # que la próxima llamada vea el estado real.
        self._current = None
        return False

    # --- internals --------------------------------------------------------

    async def _play(self, url: str, cache_dir: Path) -> None:
        validate_http_url(url)
        cache_path = cache_dir / _filename_from_url(url)
        async with self._lock:
            await self._stop_locked()
            if not cache_path.exists():
                await self._downloader(url, cache_path)
            absolute = await asyncio.to_thread(cache_path.resolve)
            proc = await asyncio.to_thread(_try_audio_players, absolute)
            if proc is not None:
                self._current = proc
                return
            try:
                await open_local_path(absolute)
            except OSError as exc:
                raise OSError(
                    f"{exc}; instalá `mpv`, `ffplay` o `mpg123` para reproducir audios "
                    "(o asociá una app a audio/mpeg en tu SO)"
                ) from exc

    async def _stop_locked(self) -> None:
        if self._current is None:
            return
        proc = self._current
        self._current = None
        await asyncio.to_thread(_terminate_process, proc)


def _try_audio_players(path: Path) -> subprocess.Popen[bytes] | None:
    """Intenta lanzar el primer CLI player disponible. Devuelve `Popen` o `None`."""
    for cmd in _AUDIO_PLAYERS:
        player = cmd[0]
        if shutil.which(player) is None:
            continue
        try:
            # Sin shell=True, args como lista, file path local (no URL): seguro.
            # `start_new_session=True` detacha del grupo de procesos para que
            # `stop()` pueda matar al player sin afectar a la TUI.
            proc = subprocess.Popen(  # noqa: S603
                [*cmd, str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            continue
        return proc
    return None


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    """Apaga el proceso con `terminate` + fallback a `kill`. Nunca lanza."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            # Última línea de defensa: si ni SIGKILL responde es problema
            # del SO; no podemos hacer más sin bloquear la TUI.
            pass
    except OSError:
        pass


def _filename_from_url(url: str) -> str:
    """Deriva un nombre de cache estable del último segmento de path de la URL.

    Hash sha256 corto como fallback si la URL no tiene path. Idéntico criterio
    que el helper que estaba inline en `app.py`; lo movemos acá para que la UI
    no tenga que conocer convenciones de cache.
    """
    _, separator, rest = url.partition("://")
    if separator and "/" in rest:
        last_segment = rest.rsplit("/", 1)[-1]
        candidate = last_segment.split("?", 1)[0].split("#", 1)[0]
        if candidate:
            return candidate
    return f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.mp3"
