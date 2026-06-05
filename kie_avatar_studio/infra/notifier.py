"""Notificaciones del sistema operativo cross-platform.

Implementa el puerto `domain.ports.DesktopNotifier` usando comandos
nativos del SO — **sin dependencias externas**:

- Linux: `notify-send` (libnotify-bin; preinstalado en GNOME/KDE/Cinnamon).
- macOS: `osascript -e 'display notification ...'` (built-in).
- Windows: PowerShell + WinRT `ToastNotificationManager` (Windows 10+).

Decisión de no usar `plyer`/`desktop-notifier`/`winotify`: agregar una dep
para algo que ya viene con el SO multiplica la superficie de fallo
(versión de la lib, pin transitivo, install en venvs limpios). El
trade-off es que cada backend es un poco más verboso, pero el código
queda autocontenido y sin sorpresas.

Todos los backends son **best-effort**: si el comando no existe o falla
(libnotify no instalado en server headless, PowerShell bloqueado por
policy, etc.) loguean a DEBUG y NUNCA propagan la excepción al caller.
Una notificación rota no debe romper un job exitoso.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from collections.abc import Awaitable, Callable
from typing import Final

from loguru import logger

# Tipo de un backend: Callable async que recibe (app_name, title, message, success).
# Devuelve None. NO debe lanzar — los errores se atrapan en SystemNotifier.notify.
_Backend = Callable[[str, str, str, bool], Awaitable[None]]

# Subprocess timeout: si `notify-send` o `powershell` se cuelgan (ej.
# permisos de pantalla bloqueando el daemon de notificaciones), no
# bloqueamos el listener del queue indefinidamente. 5s es generoso para
# un comando que normalmente retorna en <100ms.
_NOTIFY_TIMEOUT_SECONDS: Final[float] = 5.0

_APP_NAME: Final[str] = "Kie Avatar Studio"


class NullNotifier:
    """No-op: usado cuando `settings.notifications_enabled=False` o en tests."""

    async def notify(self, *, title: str, message: str, success: bool) -> None:
        return None


class SystemNotifier:
    """Implementación real: detecta el SO y delega al backend correspondiente."""

    def __init__(self, *, app_name: str = _APP_NAME) -> None:
        self._app_name = app_name
        # Resolvemos el backend una vez para evitar repetir la detección
        # de plataforma + `shutil.which` por cada notificación.
        self._backend = _select_backend()
        if self._backend is _backend_disabled:
            logger.info(
                "Notificaciones del sistema deshabilitadas: backend para "
                "sys.platform={} no disponible (instalá libnotify-bin en Linux).",
                sys.platform,
            )

    async def notify(self, *, title: str, message: str, success: bool) -> None:
        """Dispara una notificación del SO. Nunca lanza."""
        try:
            await self._backend(self._app_name, title, message, success)
        except Exception:
            logger.opt(exception=True).debug("Notificación falló (best-effort): title={!r}", title)


# --- backends -----------------------------------------------------------

# (Ver type alias `_Backend` arriba.)


async def _backend_disabled(_app: str, _title: str, _message: str, _success: bool) -> None:
    return None


async def _backend_linux(app: str, title: str, message: str, success: bool) -> None:
    """Linux: `notify-send` de libnotify.

    `--urgency=critical` para fallos hace que algunos themes (GNOME,
    Cinnamon) no auto-dismissen la notificación: el usuario debe
    cerrarla. Para éxitos usamos `normal` (timeout default del DE).

    `--icon=dialog-information` / `dialog-error` son nombres del Icon
    Naming Spec (freedesktop.org): siempre resuelven contra el theme
    activo, no contienen path absoluto.
    """
    urgency = "normal" if success else "critical"
    icon = "dialog-information" if success else "dialog-error"
    await _run_subprocess(
        "notify-send",
        f"--app-name={app}",
        f"--urgency={urgency}",
        f"--icon={icon}",
        title,
        message,
    )


async def _backend_macos(app: str, title: str, message: str, success: bool) -> None:
    """macOS: `osascript` + `display notification`.

    `display notification` en AppleScript no acepta urgency/level; solo
    title + subtitle + sound. Para fallos agregamos un sonido del sistema
    (`Basso`) para distinguir auditivamente del éxito (`Glass`).
    """
    sound = "Glass" if success else "Basso"
    # Escapado defensivo: AppleScript usa comillas dobles como delimitadores
    # y no soporta escape con \\. Reemplazamos por single-quotes seguras.
    safe_title = title.replace('"', "'")
    safe_message = message.replace('"', "'")
    safe_app = app.replace('"', "'")
    script = (
        f'display notification "{safe_message}" '
        f'with title "{safe_app}" subtitle "{safe_title}" '
        f'sound name "{sound}"'
    )
    await _run_subprocess("osascript", "-e", script)


_POWERSHELL_TOAST_SCRIPT: Final[str] = r"""
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$null = $texts.Item(0).AppendChild($template.CreateTextNode($env:KAS_NOTIFY_TITLE))
$null = $texts.Item(1).AppendChild($template.CreateTextNode($env:KAS_NOTIFY_MESSAGE))
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:KAS_NOTIFY_APP).Show($toast)
""".strip()


async def _backend_windows(app: str, title: str, message: str, success: bool) -> None:
    """Windows: PowerShell + WinRT ToastNotificationManager (Windows 10+).

    Pasamos title/message/app por **variables de entorno**, no como
    argumentos de la línea de comandos: así evitamos un quoting infierno
    con caracteres especiales (`'`, `"`, `;`, `|`) que el PowerShell
    parser reinterpreta. WinRT `ToastNotificationManager` requiere PS
    5.0+ con el módulo WinRT (built-in en Win10/11).

    `success` no afecta el visual (el template ToastText02 es fijo); si
    en el futuro queremos íconos distintos por éxito/error, hay que
    cambiar a `ToastTemplateType.ToastImageAndText02`.
    """
    env = {
        "KAS_NOTIFY_TITLE": title,
        "KAS_NOTIFY_MESSAGE": message,
        "KAS_NOTIFY_APP": app,
        # `success` lo dejamos por compatibilidad: no se usa en el
        # template actual pero un backend futuro podría leerlo.
        "KAS_NOTIFY_SUCCESS": "1" if success else "0",
    }
    await _run_subprocess(
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-Command",
        _POWERSHELL_TOAST_SCRIPT,
        env=env,
    )


# --- helpers ------------------------------------------------------------


def _select_backend() -> _Backend:
    """Devuelve el backend apropiado para esta plataforma, o disabled.

    Detección por `sys.platform` + `shutil.which` para confirmar que el
    binario está en PATH antes de elegir el backend (mejor que descubrir
    `FileNotFoundError` en cada notificación).
    """
    backend: _Backend = _backend_disabled
    required_binary: str | tuple[str, ...] | None = None
    if sys.platform.startswith("linux"):
        backend, required_binary = _backend_linux, "notify-send"
    elif sys.platform == "darwin":
        backend, required_binary = _backend_macos, "osascript"
    elif sys.platform.startswith("win"):
        backend, required_binary = _backend_windows, ("powershell.exe", "powershell")
    if required_binary is None:
        return _backend_disabled
    candidates = (required_binary,) if isinstance(required_binary, str) else required_binary
    if not any(shutil.which(name) for name in candidates):
        return _backend_disabled
    return backend


async def _run_subprocess(
    program: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> None:
    """Ejecuta un comando con timeout duro. Captura stdout/stderr.

    Logueamos el comando + exit_code en DEBUG para diagnóstico cuando
    un toast no aparece, sin spamear INFO con cada notificación
    exitosa (que son las más comunes).
    """
    import os

    full_env = None
    if env is not None:
        # Heredar PATH y demás del proceso padre + overrides nuestros.
        full_env = {**os.environ, **env}
    proc = await asyncio.create_subprocess_exec(
        program,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=full_env,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_NOTIFY_TIMEOUT_SECONDS
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        logger.debug("Notificación: {} se colgó (>{}s), kill", program, _NOTIFY_TIMEOUT_SECONDS)
        return
    if proc.returncode != 0:
        logger.debug(
            "Notificación: {} salió con code={} stderr={!r}",
            program,
            proc.returncode,
            stderr.decode(errors="replace")[:200],
        )
