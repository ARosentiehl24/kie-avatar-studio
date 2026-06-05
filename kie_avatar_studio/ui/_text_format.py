"""Helpers de texto compartidos entre pantallas Textual.

Evita duplicar `_truncate` y similares cuando dos o más pantallas
necesitan el mismo formateo (CR-3.7). Funciones puras, sin estado.
"""

from __future__ import annotations


def truncate(text: str, max_len: int) -> str:
    """Trunca `text` a `max_len` chars añadiendo `…` si excede."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
