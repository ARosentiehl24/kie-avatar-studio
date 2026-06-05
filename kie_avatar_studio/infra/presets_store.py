"""Persistencia file-based de `VoicePreset` (1 JSON por preset).

Cumple `domain.ports.VoicePresetStore`. Decisión documentada:
NO usar SQLite acá. Razones:

- Los presets son pocos (~docenas máximo). El overhead de SQLite no
  aporta.
- Editables a mano con cualquier editor de texto (útil para tweaks
  rápidos o pegar configs de stack overflow / Discord de Kie).
- Versionables con git si el usuario quiere trackear cambios.
- Portables entre instalaciones: copia `presets/voices/*.json` y listo.
- Cero migración de schema futura (los archivos no se rompen al
  agregar campos opcionales gracias a Pydantic).

Estructura en disco:

    <settings.presets_dir>/
    └── voices/
        ├── narrador-calmo.json
        ├── locutora-comercial.json
        └── ...

El nombre del archivo es `<preset.id>.json` (id = slug del label).
Lecturas/escrituras async via `asyncio.to_thread` para no bloquear
la event loop con I/O del filesystem.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from ..domain.models import VoicePreset

_VOICES_SUBDIR = "voices"
_JSON_EXTENSION = ".json"


class VoicePresetsStore:
    """Repositorio file-based de `VoicePreset`."""

    def __init__(self, presets_dir: Path) -> None:
        self._root = presets_dir / _VOICES_SUBDIR

    async def init(self) -> None:
        """Crea `presets_dir/voices/` si no existe. Idempotente."""
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)

    async def list_all(self) -> list[VoicePreset]:
        """Devuelve TODOS los presets ordenados por label asc.

        Ignora archivos JSON corruptos (loguea warning) en lugar de
        romper toda la pantalla. El usuario los puede arreglar a mano
        sin que la app crashee al arrancar.
        """
        if not self._root.exists():
            return []
        files = await asyncio.to_thread(self._list_json_files)
        presets: list[VoicePreset] = []
        for path in files:
            try:
                preset = await asyncio.to_thread(_read_preset, path)
                presets.append(preset)
            except (OSError, ValueError) as exc:
                logger.warning("Preset {} ignorado (no parseable): {}", path.name, exc)
        presets.sort(key=lambda p: p.label.lower())
        return presets

    async def get(self, preset_id: str) -> VoicePreset | None:
        path = self._path_for(preset_id)
        if not path.is_file():
            return None
        try:
            return await asyncio.to_thread(_read_preset, path)
        except (OSError, ValueError) as exc:
            logger.warning("Preset {} no parseable: {}", preset_id, exc)
            return None

    async def upsert(self, preset: VoicePreset) -> None:
        """Crea o sobreescribe el JSON del preset.

        Mismo patrón que `upsert` de SQLite stores: idempotente, no
        diferencia create de update. El `updated_at` lo refresca al
        timestamp actual (igual que JobsDB).
        """
        from datetime import UTC, datetime

        preset.updated_at = datetime.now(UTC)
        path = self._path_for(preset.id)
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)
        # `model_dump_json` excluye los campos `None` solo si lo pedimos
        # explícito. Acá los queremos serializar (mantiene shape estable
        # para que un editor a mano vea los campos disponibles).
        payload = preset.model_dump_json(indent=2)
        await asyncio.to_thread(path.write_text, payload, encoding="utf-8")

    async def delete(self, preset_id: str) -> None:
        """Borra el JSON. Idempotente (no rompe si no existe)."""
        path = self._path_for(preset_id)
        if path.is_file():
            await asyncio.to_thread(path.unlink)

    # --- internals ---------------------------------------------------------

    def _path_for(self, preset_id: str) -> Path:
        return self._root / f"{preset_id}{_JSON_EXTENSION}"

    def _list_json_files(self) -> list[Path]:
        return sorted(self._root.glob(f"*{_JSON_EXTENSION}"))


def _read_preset(path: Path) -> VoicePreset:
    """Helper sync para usar dentro de `asyncio.to_thread`."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    return VoicePreset.model_validate(data)
