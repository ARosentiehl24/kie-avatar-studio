"""Copy-to-clipboard robusto multi-backend.

Textual usa OSC 52 (escape sequence al terminal) para `copy_to_clipboard`,
pero MUCHAS terminales lo bloquean por default (GNOME Terminal, VS Code
terminal, tmux sin config, SSH sin allow-osc-52, etc). En esos casos el
usuario aprieta "Copiar URL", ve "✅ copiado" y la URL no quedó.

Este módulo intenta backends del sistema primero (donde el clipboard
real SÍ se actualiza) y solo cae a OSC 52 como último recurso. Devuelve
`ClipboardResult` con `success` y `backend` para que la UI pueda mostrar
algo accionable (ej: "no pude copiar, copiala vos: <url>").

Backends, en orden de preferencia:

1. **wl-copy** (Wayland): la mayoría de las distros modernas lo tienen.
2. **xclip -selection clipboard** (X11): segundo en preferencia.
3. **xsel --clipboard --input** (X11): fallback si no hay xclip.
4. **pbcopy** (macOS): nativo, siempre presente.
5. **clip.exe** (Windows / WSL): clip de Windows accesible desde WSL.
6. **osc52**: invoca el callback que el caller proporciona (típicamente
   `app.copy_to_clipboard` de Textual). No podemos verificar si llegó
   al clipboard, pero al menos es un intento defensivo.

No depende de Textual para que sea testeable de forma aislada y reusable
desde scripts/CLI.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

ClipboardBackend = Literal[
    "wl-copy",
    "wl-paste",
    "xclip",
    "xsel",
    "pbcopy",
    "pbpaste",
    "clip.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "osc52",
    "none",
]

# Orden de preferencia. Probamos cada uno; el primero disponible y que
# no falle se usa. Si ninguno funciona, caemos al fallback OSC 52.
_SYSTEM_BACKENDS: Final[tuple[tuple[ClipboardBackend, tuple[str, ...]], ...]] = (
    ("wl-copy", ("wl-copy",)),
    ("xclip", ("xclip", "-selection", "clipboard")),
    ("xsel", ("xsel", "--clipboard", "--input")),
    ("pbcopy", ("pbcopy",)),
    ("clip.exe", ("clip.exe",)),
)
_READ_BACKENDS: Final[tuple[tuple[ClipboardBackend, tuple[str, ...]], ...]] = (
    ("wl-paste", ("wl-paste", "--no-newline")),
    ("xclip", ("xclip", "-selection", "clipboard", "-o")),
    ("xsel", ("xsel", "--clipboard", "--output")),
    ("pbpaste", ("pbpaste",)),
    ("powershell", ("powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw")),
    ("powershell.exe", ("powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw")),
    ("pwsh", ("pwsh", "-NoProfile", "-Command", "Get-Clipboard -Raw")),
)


@dataclass(frozen=True, slots=True)
class ClipboardResult:
    """Resultado de intentar copiar al clipboard.

    `success=True` significa "el comando terminó OK". Para OSC 52 NO
    podemos confirmar que el terminal aceptó la secuencia — devolvemos
    `success=True` optimista y dejamos que el usuario lo verifique.

    `backend` indica qué método se usó. Útil para mensajes UI tipo
    "Copiado vía xclip" o para sugerir instalar `wl-copy` si solo
    funcionó OSC 52.
    """

    success: bool
    backend: ClipboardBackend
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ClipboardTextResult:
    """Resultado de leer texto desde el clipboard del sistema."""

    success: bool
    backend: ClipboardBackend
    text: str = ""
    error: str | None = None


async def copy_to_clipboard(
    text: str,
    *,
    osc52_fallback: Callable[[str], None] | None = None,
) -> ClipboardResult:
    """Copia `text` al clipboard del sistema. Intenta varios backends.

    `osc52_fallback` es una función opcional (típicamente
    `app.copy_to_clipboard` de Textual) que se invoca si ningún backend
    nativo está disponible. Si no se pasa y todos fallan, devuelve
    `ClipboardResult(success=False, backend="none")` para que la UI
    pueda mostrar la URL como fallback.
    """
    if not text:
        return ClipboardResult(success=False, backend="none", error="texto vacío")

    for backend_name, command in _SYSTEM_BACKENDS:
        if shutil.which(command[0]) is None:
            continue
        try:
            return await _run_pipe(backend_name, command, text)
        except (TimeoutError, OSError) as exc:
            # Si el comando existe pero falló, lo registramos como
            # último error y seguimos probando los demás.
            last_error: str | None = f"{backend_name}: {exc}"
            continue
    last_error = None

    if osc52_fallback is not None:
        try:
            osc52_fallback(text)
        except Exception as exc:
            return ClipboardResult(success=False, backend="osc52", error=str(exc))
        return ClipboardResult(success=True, backend="osc52")

    return ClipboardResult(
        success=False,
        backend="none",
        error=last_error
        or "no hay backend de clipboard disponible (instalá wl-copy / xclip / xsel)",
    )


async def read_from_clipboard() -> ClipboardTextResult:
    """Lee texto desde el clipboard del sistema usando backends nativos."""
    last_error: str | None = None
    for backend_name, command in _READ_BACKENDS:
        if shutil.which(command[0]) is None:
            continue
        try:
            result = await _run_capture(backend_name, command)
        except (TimeoutError, OSError) as exc:
            last_error = f"{backend_name}: {exc}"
            continue
        if result.success:
            return result
        last_error = result.error
    return ClipboardTextResult(
        success=False,
        backend="none",
        error=last_error
        or "no hay backend de lectura de clipboard disponible (probá Ctrl+V directo)",
    )


async def _run_pipe(
    backend: ClipboardBackend, command: tuple[str, ...], text: str
) -> ClipboardResult:
    """Ejecuta `command` pasando `text` por stdin. Timeout 2s.

    Si el proceso devuelve exit code != 0, devuelve `success=False`
    con el stderr capturado para que la UI pueda mostrarlo en logs.
    """
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(input=text.encode("utf-8")), timeout=2.0
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace").strip() or "exit != 0"
        return ClipboardResult(success=False, backend=backend, error=msg)
    return ClipboardResult(success=True, backend=backend)


async def _run_capture(backend: ClipboardBackend, command: tuple[str, ...]) -> ClipboardTextResult:
    """Ejecuta `command` y captura stdout como texto del clipboard."""
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace").strip() or "exit != 0"
        return ClipboardTextResult(success=False, backend=backend, error=msg)
    text = stdout.decode("utf-8", errors="replace")
    if not text:
        return ClipboardTextResult(success=False, backend=backend, error="clipboard vacío")
    return ClipboardTextResult(success=True, backend=backend, text=text)
