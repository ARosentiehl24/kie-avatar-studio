# capa: ui
# Fixture para tests/test_agent_smoke.py.
# Viola intencionalmente: CR-1.1, CR-3.3, CR-4.1, CR-4.2, CR-5.1, CR-5.6.
# No se importa en runtime; el detector lo lee como texto.

from __future__ import annotations

import time
from datetime import datetime

import httpx  # CR-1.1: capa `ui` no puede importar httpx

_DEFAULT_TIMEOUT = 30


def hacer_algo_pesado(payload: dict) -> dict | None:
    """Ejemplo malo a propósito: bloquea, usa utcnow y silencia excepciones."""
    time.sleep(27)  # CR-5.1 + CR-3.3 (27 magic)
    _ = datetime.utcnow()  # CR-5.6
    try:
        response = httpx.get("https://example.com", timeout=_DEFAULT_TIMEOUT)
        return response.json()
    except Exception:  # CR-4.2
        pass
    return None


def calcular_default() -> None:
    raise ValueError("no permitido aquí")  # CR-4.1
