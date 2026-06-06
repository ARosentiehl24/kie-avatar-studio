"""Abrir archivos locales o URLs con la app por defecto del sistema operativo.

Encapsulado acá para que la UI no toque `subprocess` directamente y para
poder mockearlo en tests. Política:

- Linux  → `xdg-open <target>` (si está disponible).
- macOS  → `open <target>`.
- Windows → `os.startfile(target)` (acepta tanto paths como URLs http(s)).

Detecta plataforma con `sys.platform`. Si el comando falla o no está
disponible, lanza `OSError` con un mensaje en español; el caller (la
pantalla) lo traduce a notify visible.

Para reproducir audios con un proceso trackeado y cancelable, ver
`app_layer.audio_player.AudioPlayer`.

Seguridad (CR-7.2):
- No usa `shell=True` y pasa el target como argumento puro: sin shell
  injection aunque venga del usuario.
- `open_url` valida que sea http(s):// antes de invocar al launcher, así
  no se le entrega un `file://` ni un esquema raro que pudiera abrir
  archivos locales fuera de control.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

from ..domain.policies import validate_http_url

_LINUX_OPENER: Final[str] = "xdg-open"
_MACOS_OPENER: Final[str] = "open"
_SUBPROCESS_TIMEOUT_SECONDS: Final[int] = 10


async def open_local_path(path: Path) -> None:
    """Abre `path` con el visor por defecto del SO. Lanza `OSError` si falla.

    Resolvemos el path a absoluto en un thread (evita bloquear la event
    loop con symlinks/redes) antes de pasarlo al launcher: `xdg-open`
    falla con exit code 4 en muchos backends si recibe un path relativo.
    """
    absolute = await asyncio.to_thread(path.resolve)
    await asyncio.to_thread(_dispatch_local, absolute)


async def open_url(url: str) -> None:
    """Abre una URL http(s) en el navegador por defecto.

    Lanza `OSError` si el launcher del SO no está disponible o falla.
    Lanza `UrlValidationError` (vía `validate_http_url`) si la URL no es http(s).
    """
    validate_http_url(url)
    await asyncio.to_thread(_dispatch_url, url)


def _dispatch_local(path: Path) -> None:
    if not path.exists():
        raise OSError(f"el archivo no existe: {path}")
    _launch(str(path))


def _dispatch_url(url: str) -> None:
    _launch(url)


def _launch(target: str) -> None:
    """Invoca al launcher del SO con `target` (path absoluto o URL http(s))."""
    platform = sys.platform
    if platform == "win32":
        # `os.startfile` solo existe en Windows; importamos local para que
        # mypy en plataformas no-Windows no marque el módulo. Combinamos
        # `attr-defined` (necesario en Linux/Mac donde `os.startfile` no
        # existe) con `unused-ignore` (necesario en Windows donde sí
        # existe y warn_unused_ignores reportaría el ignore como muerto).
        # El resultado es portable cross-platform sin tocar config global.
        import os

        os.startfile(target)  # type: ignore[attr-defined, unused-ignore]  # noqa: S606
        return
    opener = _MACOS_OPENER if platform == "darwin" else _LINUX_OPENER
    if shutil.which(opener) is None:
        raise OSError(f"no se encontró '{opener}' en PATH; no puedo abrir el visor del sistema")
    try:
        # `subprocess.run` sin shell=True y con args como lista: sin shell injection.
        subprocess.run(  # noqa: S603
            [opener, target],
            check=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        # `xdg-open` documenta exit codes 1-4 según el modo de fallo (no MIME
        # handler, target inexistente, etc.). Convertimos a `OSError` para que
        # las pantallas lo traduzcan a un notify legible sin tener que
        # importar `subprocess`.
        raise OSError(
            f"'{opener}' rechazó abrir {target!r} (exit code {exc.returncode}); "
            "verificá que tengas una app asociada a ese tipo de archivo"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError(
            f"'{opener}' no respondió en {_SUBPROCESS_TIMEOUT_SECONDS}s al abrir {target!r}"
        ) from exc
