"""Registry declarativo del menú principal.

Permite agregar pantallas sin tocar la lógica de navegación (OCP):
basta con sumar una entrada a `MAIN_MENU` y, si corresponde, asociarla a una
Screen real cuando se implemente.

Cada item tiene un `icon` (importado de `_icons` para garantizar
double-width consistente entre terminales — ver el docstring de
`ui._icons` para la regla completa). Los items con `pending_message` se
renderizan en `dim` con sufijo `(pronto)` para distinguirlos visualmente
de los funcionales.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from . import _icons


@dataclass(frozen=True, slots=True)
class MenuItem:
    id: str
    hotkey: str
    label: str
    icon: str
    pending_message: str | None = None  # None ⇒ acción real (ej. salir)


MAIN_MENU: Final[tuple[MenuItem, ...]] = (
    MenuItem(id="new_job", hotkey="N", label="Nuevo video", icon=_icons.MENU_VIDEO),
    MenuItem(id="batch", hotkey="B", label="Procesar lote", icon=_icons.MENU_BATCH),
    MenuItem(id="automation", hotkey="F", label="Automatización", icon=_icons.MENU_AUTOMATION),
    MenuItem(id="queue", hotkey="G", label="Cola de trabajos", icon=_icons.MENU_QUEUE),
    MenuItem(id="history", hotkey="H", label="Historial", icon=_icons.MENU_HISTORY),
    MenuItem(id="presets", hotkey="P", label="Presets", icon=_icons.MENU_PRESETS),
    MenuItem(id="images", hotkey="I", label="Imágenes", icon=_icons.MENU_IMAGES),
    MenuItem(id="audios", hotkey="A", label="Audios", icon=_icons.MENU_AUDIOS),
    MenuItem(id="settings", hotkey="C", label="Configuración", icon=_icons.MENU_SETTINGS),
    MenuItem(id="logs", hotkey="L", label="Logs", icon=_icons.MENU_LOGS),
    MenuItem(id="quit", hotkey="Q", label="Salir", icon=_icons.MENU_QUIT),
)


# Lookup precomputado por id. Única fuente de verdad reutilizada por `app.py`
# (atajos globales) y por `MainMenuScreen` (selección por OptionList) — evita
# duplicación del dict y búsquedas lineales en cada acción (CR-3.7).
MENU_BY_ID: Final[dict[str, MenuItem]] = {item.id: item for item in MAIN_MENU}
