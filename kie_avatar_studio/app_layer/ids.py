"""Generación y saneo de identificadores reutilizables."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Final

_FILENAME_INVALID: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.-]+")
_FALLBACK_NAME: Final[str] = "unnamed"
_SHORT_UUID_LEN: Final[int] = 6


def new_job_id() -> str:
    """Identificador único determinístico-amigable: `job_<UTCtimestamp>_<short_uuid>`."""
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"job_{stamp}_{uuid.uuid4().hex[:_SHORT_UUID_LEN]}"


def new_audio_id() -> str:
    """Identificador único para `GeneratedAudio`: `aud_<UTCtimestamp>_<short_uuid>`.

    Mismo formato que `new_job_id` para mantener consistencia visual en logs
    y en las tablas de la TUI.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"aud_{stamp}_{uuid.uuid4().hex[:_SHORT_UUID_LEN]}"


def sanitize_filename(name: str) -> str:
    """Devuelve un nombre seguro para usar en filesystem; nunca retorna cadena vacía."""
    cleaned = _FILENAME_INVALID.sub("_", name).strip("_")
    return cleaned or _FALLBACK_NAME
