"""Limpieza segura del estado runtime local preservando credenciales y outputs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeCleanupResult:
    """Resultado de limpiar la SQLite runtime."""

    removed: tuple[Path, ...]
    missing: tuple[Path, ...]


class RuntimeStateCleaner:
    """Elimina solo la DB runtime y sus sidecars WAL/SHM."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def cleanup(self) -> RuntimeCleanupResult:
        removed: list[Path] = []
        missing: list[Path] = []
        for path in runtime_db_files(self._db_path):
            if await asyncio.to_thread(path.exists):
                await asyncio.to_thread(path.unlink)
                removed.append(path)
            else:
                missing.append(path)
        return RuntimeCleanupResult(removed=tuple(removed), missing=tuple(missing))


def runtime_db_files(db_path: Path) -> tuple[Path, Path, Path]:
    """Devuelve `jobs.db`, `jobs.db-wal` y `jobs.db-shm`."""
    return (
        db_path,
        db_path.with_name(f"{db_path.name}-wal"),
        db_path.with_name(f"{db_path.name}-shm"),
    )
