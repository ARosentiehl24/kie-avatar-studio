"""Registry declarativo del menú principal.

Permite agregar pantallas sin tocar la lógica de navegación (OCP):
basta con sumar una entrada a `MAIN_MENU` y, si corresponde, asociarla a una
Screen real cuando se implemente.

Cada item tiene un `icon` (emoji o glyph Unicode) para mejorar el escaneo
visual del menú. Los items con `pending_message` se renderizan en `dim` con
sufijo `(pronto)` para distinguirlos visualmente de los funcionales.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class MenuItem:
    id: str
    hotkey: str
    label: str
    icon: str
    pending_message: str | None = None  # None ⇒ acción real (ej. salir)


MAIN_MENU: Final[tuple[MenuItem, ...]] = (
    MenuItem(
        id="new_job",
        hotkey="N",
        label="Nuevo video",
        icon="🎬",
    ),
    MenuItem(
        id="batch",
        hotkey="B",
        label="Procesar lote",
        icon="📦",
    ),
    MenuItem(
        id="queue",
        hotkey="G",
        label="Cola de trabajos",
        icon="⏳",
    ),
    MenuItem(
        id="history",
        hotkey="H",
        label="Historial",
        icon="📜",
    ),
    MenuItem(
        id="presets",
        hotkey="P",
        label="Presets",
        icon="🎤",
    ),
    MenuItem(
        id="images",
        hotkey="I",
        label="Imágenes",
        icon="📷",
    ),
    MenuItem(
        id="audios",
        hotkey="A",
        label="Audios",
        icon="🔊",
    ),
    MenuItem(
        id="settings",
        hotkey="C",
        label="Configuración",
        icon="🔧",
    ),
    MenuItem(
        id="logs",
        hotkey="L",
        label="Logs",
        icon="📋",
    ),
    MenuItem(
        id="quit",
        hotkey="Q",
        label="Salir",
        icon="🚪",
    ),
)


# Lookup precomputado por id. Única fuente de verdad reutilizada por `app.py`
# (atajos globales) y por `MainMenuScreen` (selección por OptionList) — evita
# duplicación del dict y búsquedas lineales en cada acción (CR-3.7).
MENU_BY_ID: Final[dict[str, MenuItem]] = {item.id: item for item in MAIN_MENU}
