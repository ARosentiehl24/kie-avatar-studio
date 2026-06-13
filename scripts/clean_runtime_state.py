"""Limpia la DB runtime conservando API keys y outputs.

Uso:
    python scripts/clean_runtime_state.py
    python scripts/clean_runtime_state.py --yes

Este script elimina únicamente `jobs.db`, `jobs.db-wal` y `jobs.db-shm`.
No toca `data/keys.json`, `outputs/`, `inputs/`, `presets/` ni `workflows/`.
Ejecutalo con la app cerrada para evitar locks de SQLite.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from kie_avatar_studio.app_layer.runtime_state_cleaner import RuntimeStateCleaner
from kie_avatar_studio.config import load_settings
from kie_avatar_studio.infra.keys_store import KEYS_FILE_NAME


def main() -> int:
    parser = argparse.ArgumentParser(description="Limpia la DB runtime local.")
    parser.add_argument("--yes", action="store_true", help="No pedir confirmación interactiva.")
    args = parser.parse_args()

    settings = load_settings()
    settings.ensure_dirs()
    print(f"DB a limpiar: {settings.db_path}")
    print(f"Se conserva: {settings.data_dir / KEYS_FILE_NAME}")
    print(f"Se conserva: {settings.outputs_dir}")
    if not args.yes:
        answer = input("Escribí LIMPIAR para confirmar: ").strip()
        if answer != "LIMPIAR":
            print("Cancelado.")
            return 1

    result = asyncio.run(RuntimeStateCleaner(Path(settings.db_path)).cleanup())
    for path in result.removed:
        print(f"Eliminado: {path}")
    if not result.removed:
        print("No había DB runtime para eliminar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
