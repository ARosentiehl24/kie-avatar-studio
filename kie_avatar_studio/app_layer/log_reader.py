"""Lector de las últimas N líneas de un archivo de log.

Encapsulado acá para que la UI (`LogsScreen`) no tenga que conocer detalles de
filesystem y para poder mockearlo en tests sin tocar disco.

Lee asíncrono con `asyncio.to_thread` para no bloquear la event loop si el
archivo creció. No carga el archivo entero: usa un buffer chunkeado desde el
final (clásico tail) — barato incluso con logs de varios MB.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Final

_CHUNK_BYTES: Final[int] = 4096
_DEFAULT_MAX_LINES: Final[int] = 500


class LogReader:
    """Lectura tail-only de un archivo de log local."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    async def tail(self, max_lines: int = _DEFAULT_MAX_LINES) -> list[str]:
        """Devuelve las últimas `max_lines` líneas del archivo, o vacío si no existe."""
        if not self._path.exists():
            return []
        return await asyncio.to_thread(self._read_tail, max_lines)

    def _read_tail(self, max_lines: int) -> list[str]:
        lines = self._tail_bytes(max_lines).splitlines()
        return lines[-max_lines:]

    def _tail_bytes(self, max_lines: int) -> str:
        """Lee chunks desde el final hasta acumular ≥ `max_lines` saltos de línea."""
        size = self._path.stat().st_size
        if size == 0:
            return ""
        with self._path.open("rb") as fp:
            buffers: list[bytes] = []
            newlines = 0
            position = size
            while position > 0 and newlines <= max_lines:
                read_size = min(_CHUNK_BYTES, position)
                position -= read_size
                fp.seek(position)
                chunk = fp.read(read_size)
                buffers.append(chunk)
                newlines += chunk.count(b"\n")
            data = b"".join(reversed(buffers))
        return data.decode("utf-8", errors="replace")
