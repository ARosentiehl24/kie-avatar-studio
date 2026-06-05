"""Configuración de logging con loguru.

Centralizado aquí para que ni la UI ni el dominio necesiten conocer detalles
de loguru.

Dos modos:

- `configure_logging(logs_dir)`: sink dual (stderr + archivo rotado). Apto para
  scripts y tests.
- `configure_logging(logs_dir, tui_mode=True)`: solo archivo. Stderr quedaría
  silenciado por Textual al entrar a alt-screen, así que escribirlo allí solo
  pinta caracteres encima de la TUI y los pierde igual. En TUI mode, además,
  Python warnings y excepciones no manejadas se redirigen al log.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import warnings
from pathlib import Path
from types import FrameType
from typing import Any, Final

from loguru import logger

LOG_FILE_NAME: Final[str] = "kie-avatar-studio.log"
ROTATION: Final[str] = "10 MB"
RETENTION: Final[str] = "14 days"
_FILE_FORMAT: Final[str] = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
)


def configure_logging(
    logs_dir: Path,
    level: str = "INFO",
    *,
    tui_mode: bool = False,
) -> Path:
    """Reemplaza sinks por archivo rotado (y stderr si no estamos en TUI).

    Devuelve la ruta absoluta del archivo de log para que el composition root
    pueda mostrársela al usuario.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / LOG_FILE_NAME
    logger.remove()
    if not tui_mode:
        logger.add(sys.stderr, level=level)
    logger.add(
        log_file,
        level=level,
        rotation=ROTATION,
        retention=RETENTION,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,  # CR-7.1: diagnose=True imprime valores locales (riesgo de secretos)
        format=_FILE_FORMAT,
    )
    if tui_mode:
        _redirect_stdlib_warnings_and_uncaught()
    return log_file.resolve()


def _redirect_stdlib_warnings_and_uncaught() -> None:
    """En TUI mode, garantizamos que NADA se pierda en stderr silenciado.

    - `warnings.showwarning` → loguru `WARNING`.
    - `sys.excepthook` → loguru `ERROR` con traceback completo.
    - `asyncio` exception handler (instalado al primer ciclo) → loguru `ERROR`.

    El handler de asyncio se aplica desde `App.on_mount` porque allí ya existe
    un loop corriendo.
    """

    def _warn_to_log(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> None:
        logger.opt(depth=1).warning(
            "{category}: {msg} ({file}:{line})",
            category=category.__name__,
            msg=str(message),
            file=filename,
            line=lineno,
        )

    warnings.showwarning = _warn_to_log

    def _excepthook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        logger.opt(exception=(exc_type, exc, tb)).error("Excepción no manejada en hilo principal")

    sys.excepthook = _excepthook


def install_asyncio_exception_handler() -> None:
    """Captura excepciones de tasks asyncio "huérfanas" (no awaited).

    Debe llamarse desde dentro de una event loop activa.
    """

    def _handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        message = context.get("message", "asyncio handler sin mensaje")
        if exc is not None:
            logger.opt(exception=exc).error("asyncio: {}", message)
        else:
            logger.error("asyncio: {} (context={})", message, context)

    asyncio.get_running_loop().set_exception_handler(_handler)


# Compatibilidad con `logging` stdlib: cualquier librería que use `logging.getLogger`
# (httpx, pydantic) termina yendo a loguru.


class _InterceptHandler(logging.Handler):
    """Puentea `logging` stdlib hacia loguru. Idempotente."""

    def emit(self, record: logging.LogRecord) -> None:
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def bridge_stdlib_logging(level: str = "INFO") -> None:
    """Instala el handler de intercepción una sola vez.

    Además silencia loggers ruidosos de librerías de red (`httpx`,
    `httpcore`) que loguean cada request en `INFO` — eso ensucia los
    logs y los caplog de pytest sin aportar nada al usuario final.
    Los errores reales (WARNING+) sí pasan.
    """
    root = logging.getLogger()
    if any(isinstance(h, _InterceptHandler) for h in root.handlers):
        return
    root.handlers = [_InterceptHandler()]
    root.setLevel(level)
    # Loggers de librerías externas que loguean cada request HTTP. El
    # usuario no necesita ver "GET https://api.github.com/.../releases/latest
    # 200 OK" cada vez que arranca la app. Los WARNING/ERROR sí pasan.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
