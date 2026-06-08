"""Tests del sistema de logging y captura de errores."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from loguru import logger

from kie_avatar_studio.app_layer.log_reader import LogReader
from kie_avatar_studio.infra.logging import (
    LOG_FILE_NAME,
    bridge_stdlib_logging,
    configure_logging,
    install_asyncio_exception_handler,
)


def _flush_loguru() -> None:
    """loguru con enqueue=True escribe en otro hilo; le damos tiempo a vaciarse."""
    logger.complete()
    time.sleep(0.1)


def test_configure_logging_returns_log_path(tmp_path: Path) -> None:
    log_file = configure_logging(tmp_path, "INFO", tui_mode=True)
    assert log_file == (tmp_path / LOG_FILE_NAME).resolve()


def test_configure_logging_writes_to_file(tmp_path: Path) -> None:
    configure_logging(tmp_path, "INFO", tui_mode=True)
    logger.info("mensaje de prueba 42")
    _flush_loguru()
    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "mensaje de prueba 42" in content


def test_configure_logging_captures_exception_with_traceback(tmp_path: Path) -> None:
    configure_logging(tmp_path, "INFO", tui_mode=True)
    try:
        raise ValueError("explosión controlada")
    except ValueError:
        logger.exception("falló algo")
    _flush_loguru()
    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "falló algo" in content
    assert "ValueError" in content
    assert "explosión controlada" in content


def test_bridge_stdlib_logging_redirects_to_loguru(tmp_path: Path) -> None:
    configure_logging(tmp_path, "INFO", tui_mode=True)
    bridge_stdlib_logging("INFO")
    logging.getLogger("test.bridge").error("mensaje desde logging stdlib")
    _flush_loguru()
    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "mensaje desde logging stdlib" in content


async def test_install_asyncio_exception_handler_logs_orphan(tmp_path: Path) -> None:
    configure_logging(tmp_path, "INFO", tui_mode=True)
    install_asyncio_exception_handler()

    # Forzamos al handler global a procesar un contexto, en vez de depender de
    # GC + timing de tasks no-awaited (que es no-determinístico).
    loop = asyncio.get_running_loop()
    try:
        raise RuntimeError("task huérfana")
    except RuntimeError as exc:
        loop.call_exception_handler(
            {"message": "Task exception was never retrieved", "exception": exc}
        )
    _flush_loguru()
    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "task huérfana" in content
    assert "RuntimeError" in content


async def test_log_reader_tail_returns_last_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "demo.log"
    log_file.write_text("\n".join(f"linea {i}" for i in range(1, 50)) + "\n")
    reader = LogReader(log_file)
    last10 = await reader.tail(10)
    assert last10[-1] == "linea 49"
    assert last10[0] == "linea 40"
    assert len(last10) == 10


async def test_log_reader_tail_handles_missing_file(tmp_path: Path) -> None:
    reader = LogReader(tmp_path / "no-existe.log")
    assert await reader.tail() == []


async def test_log_reader_tail_handles_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.log"
    empty.touch()
    reader = LogReader(empty)
    assert await reader.tail() == []


async def test_log_reader_tail_handles_file_smaller_than_chunk(tmp_path: Path) -> None:
    small = tmp_path / "small.log"
    small.write_text("una\ndos\ntres\n")
    reader = LogReader(small)
    assert await reader.tail(10) == ["una", "dos", "tres"]
