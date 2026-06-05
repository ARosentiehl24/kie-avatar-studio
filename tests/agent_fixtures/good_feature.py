# capa: ui
# Fixture "limpio" para tests/test_agent_smoke.py. No debe disparar hallazgos.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from kie_avatar_studio.domain.errors import JobValidationError

_ESPERA_SEGURA_SEGUNDOS = 1


async def saludar_async(nombre: str) -> str:
    """Devuelve un saludo con timestamp consciente de zona horaria."""
    await asyncio.sleep(_ESPERA_SEGURA_SEGUNDOS)
    ahora = datetime.now(UTC).isoformat()
    if not nombre:
        raise JobValidationError("nombre vacío")
    return f"hola {nombre} ({ahora})"
