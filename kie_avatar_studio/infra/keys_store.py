"""Persistencia multi-perfil de credenciales Kie sobre `data/keys.json`.

Decisiones:
- Formato JSON simple e inspeccionable a ojo (CR-7.3 — el usuario quiere ver qué
  guarda la app).
- Escritura atómica: dump a `.tmp` + `Path.replace` (operación atómica en POSIX y
  NTFS). Una caída a la mitad no corrompe el archivo viejo.
- `chmod 0o600` tras cada escritura (CR-7.1 — secreto local, dueño exclusivo).
- IO de filesystem fuera de la event loop con `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from ..domain.errors import KeyNotFoundError
from ..domain.models import KeyValidationStatus, KieKey

KEYS_FILE_NAME: Final[str] = "keys.json"
_FILE_MODE: Final[int] = 0o600
_INDENT: Final[int] = 2


class KeysStore:
    """Repositorio de `KieKey` sobre un único archivo JSON local."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Crea el archivo vacío si no existe y asegura permisos `0o600`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            await self._write_state({"active_key_id": None, "keys": []})
        else:
            await asyncio.to_thread(self._path.chmod, _FILE_MODE)

    async def load(self) -> list[KieKey]:
        state = await self._read_state()
        return [self._row_to_key(row) for row in state.get("keys", [])]

    async def get(self, key_id: str) -> KieKey | None:
        for key in await self.load():
            if key.id == key_id:
                return key
        return None

    async def upsert(self, key: KieKey) -> None:
        async with self._lock:
            state = await self._read_state()
            keys: list[dict[str, Any]] = state.get("keys", [])
            updated = False
            for index, row in enumerate(keys):
                if row.get("id") == key.id:
                    keys[index] = self._key_to_row(key)
                    updated = True
                    break
            if not updated:
                keys.append(self._key_to_row(key))
            state["keys"] = keys
            await self._write_state(state)

    async def delete(self, key_id: str) -> None:
        async with self._lock:
            state = await self._read_state()
            keys: list[dict[str, Any]] = state.get("keys", [])
            new_keys = [row for row in keys if row.get("id") != key_id]
            if len(new_keys) == len(keys):
                raise KeyNotFoundError(f"no existe ninguna key con id={key_id!r}")
            state["keys"] = new_keys
            if state.get("active_key_id") == key_id:
                state["active_key_id"] = None
            await self._write_state(state)

    async def get_active(self) -> KieKey | None:
        state = await self._read_state()
        active_id = state.get("active_key_id")
        if not active_id:
            return None
        for row in state.get("keys", []):
            if row.get("id") == active_id:
                return self._row_to_key(row)
        return None

    async def set_active(self, key_id: str | None) -> None:
        async with self._lock:
            state = await self._read_state()
            if key_id is not None:
                exists = any(row.get("id") == key_id for row in state.get("keys", []))
                if not exists:
                    raise KeyNotFoundError(f"no existe ninguna key con id={key_id!r}")
            state["active_key_id"] = key_id
            await self._write_state(state)

    # --- IO helpers --------------------------------------------------------

    async def _read_state(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"active_key_id": None, "keys": []}
        raw = await asyncio.to_thread(self._path.read_text, "utf-8")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"active_key_id": None, "keys": []}
        if not isinstance(parsed, dict):
            return {"active_key_id": None, "keys": []}
        return parsed

    async def _write_state(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, indent=_INDENT, ensure_ascii=False, sort_keys=False)
        await asyncio.to_thread(self._atomic_write, payload)

    def _atomic_write(self, payload: str) -> None:
        """Escribe `payload` a `self._path` de forma atómica y con permisos `0o600`."""
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.chmod(_FILE_MODE)
        tmp.replace(self._path)
        # Re-aplicamos el modo por si el destino heredó algo distinto del original.
        self._path.chmod(_FILE_MODE)

    # --- mappers -----------------------------------------------------------

    @staticmethod
    def _key_to_row(key: KieKey) -> dict[str, Any]:
        return {
            "id": key.id,
            "label": key.label,
            "key": key.key,
            "created_at": key.created_at.isoformat(),
            "last_validated_at": (
                key.last_validated_at.isoformat() if key.last_validated_at else None
            ),
            "last_validated_status": key.last_validated_status,
        }

    @staticmethod
    def _row_to_key(row: dict[str, Any]) -> KieKey:
        last_at_raw = row.get("last_validated_at")
        last_status_raw = row.get("last_validated_status")
        last_status: KeyValidationStatus | None = (
            last_status_raw if last_status_raw in {"ok", "unauthorized", "error"} else None
        )
        return KieKey(
            id=row["id"],
            label=row["label"],
            key=row["key"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_validated_at=datetime.fromisoformat(last_at_raw) if last_at_raw else None,
            last_validated_status=last_status,
        )
