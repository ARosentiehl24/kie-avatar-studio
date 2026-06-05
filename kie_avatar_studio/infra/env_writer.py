"""Escritura segura sobre `.env` vía `python-dotenv`.

Centraliza el único punto del paquete que muta el `.env`. Mantiene un backup
`.env.bak` (un nivel) y delega el parseo/serialización a `python-dotenv` para
preservar comentarios y formato del archivo.

CR-11.2: este wrapper es el único mecanismo autorizado para persistir cambios
no-keys. Las capas superiores reciben el `Protocol EnvWriter` por inyección.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key


class DotenvWriter:
    """Implementa `domain.ports.EnvWriter` sobre un único archivo `.env`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def set(self, key: str, value: str) -> None:
        self._ensure_file_with_backup()
        set_key(str(self._path), key, value, quote_mode="always")

    def get(self, key: str) -> str | None:
        if not self._path.exists():
            return None
        values = dotenv_values(str(self._path))
        return values.get(key)

    def unset(self, key: str) -> None:
        if not self._path.exists():
            return
        self._ensure_file_with_backup()
        unset_key(str(self._path), key)

    def _ensure_file_with_backup(self) -> None:
        """Crea el `.env` si no existe; si existe, copia a `.env.bak` antes de mutar."""
        if not self._path.exists():
            self._path.touch(mode=0o600)
            return
        backup = self._path.with_suffix(self._path.suffix + ".bak")
        shutil.copy2(self._path, backup)
