"""Iconos seguros para la TUI: solo glifos con render double-width garantizado.

REGLA DURA (CR-3.7 + nueva convención de UI): ningún archivo de
`kie_avatar_studio/` debe contener emojis o glifos Unicode literales en
strings visibles al usuario. Todos los iconos pasan por las constantes
de este módulo.

### Por qué

Muchos caracteres Unicode comunes (`✓`, `✖`, `🔄`, `🖼`, `⚙`, `⏹`)
tienen `East_Asian_Width: Neutral` o `Ambiguous`. Distintos terminales
los renderizan como 1 cell ("narrow") o 2 cells ("wide") sin garantía.
Cuando un emoji narrow se concatena con texto (`"✖0 fallidos"`),
visualmente queda **pegado** al siguiente carácter sin separación, o se
solapa si la fuente lo dibuja con un glifo más ancho que la cell.

La única forma estable de obtener iconos legibles en TODOS los terminales
es usar caracteres con `Emoji_Presentation: Yes` (que son wide por
definición en la spec Unicode TR51) o forzar la presentación emoji con
Variation Selector-16 (`\ufe0f`) en chars que la admiten.

### Cómo agregar un icono nuevo

1. Verificá que sea Emoji_Presentation:Yes o admita VS16. Lookup:
   https://unicode.org/Public/UCD/latest/ucd/emoji/emoji-data.txt
2. Probalo en Python:
   ```python
   import unicodedata as u

   print(u.east_asian_width("X"))  # debe ser 'W' (Wide) o 'F' (Fullwidth)
   ```
3. Agregalo acá con un nombre descriptivo + comentario citando el code
   point + por qué se eligió ese sobre alternativas similares.

### Por qué NO usar el carácter directamente

Si dos pantallas quieren mostrar "completado", una usa `✓` y otra `✅`,
el resultado visual diverge. Centralizar elimina la inconsistencia y
hace que cambiar el icono en TODA la app sea un edit en un solo lugar.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Status: éxito / fallo / pendiente / actividad
#
# Los más usados en mensajes de status (`✓ encolado`, `✖ falló`) y en
# contadores (`✓ 5 listos`, `✖ 2 fallidos`). Antes usábamos `✓` (U+2713)
# y `✖` (U+2716) — narrow text chars que se pegaban al siguiente
# carácter. Los wide-emoji equivalentes son `✅` y `❌`.
# ---------------------------------------------------------------------------

OK: Final[str] = "✅"  # U+2705 — white heavy check mark, Emoji_Presentation
ERROR: Final[str] = "❌"  # U+274C — cross mark, Emoji_Presentation
QUEUED: Final[str] = "⏳"  # U+23F3 — hourglass not done, wide default
WORKING: Final[str] = "🔁"  # U+1F501 — clockwise repeat. Más estable que
# `🔄` (U+1F504) que en algunos terminales aparece
# narrow porque cae fuera del Emoji_Presentation set.
WARNING: Final[str] = "❗"  # U+2757 — heavy exclamation mark, Emoji_Presentation.
# Reemplaza `⚠️` que requiere VS16 para ser wide y no
# todos los terminales respetan el variation selector.

# ---------------------------------------------------------------------------
# Job categories (kinds usados en tablas/badges para distinguir tipo).
# ---------------------------------------------------------------------------

VIDEO: Final[str] = "🎬"  # U+1F3AC — clapper board, Emoji_Presentation
AUDIO: Final[str] = "🔊"  # U+1F50A — speaker high volume, Emoji_Presentation
IMAGE: Final[str] = "🎨"  # U+1F3A8 — artist palette, Emoji_Presentation.
# NO usamos `🖼` (U+1F5BC) porque tiene fallback
# inconsistente — en algunas fuentes Windows se
# rendea como 🌅 (sunrise) por el mapeo del CMap.

# ---------------------------------------------------------------------------
# Acciones (mensajes de status / flujo de los runners).
# ---------------------------------------------------------------------------

UPLOAD: Final[str] = "📤"  # U+1F4E4 — outbox tray, Emoji_Presentation
DOWNLOAD: Final[str] = "📥"  # U+1F4E5 — inbox tray, Emoji_Presentation
RETRY: Final[str] = "🔁"  # Alias semántico de WORKING para mensajes de retry.

# ---------------------------------------------------------------------------
# Menú principal (cada entry de MAIN_MENU). Todos Emoji_Presentation:Yes.
# ---------------------------------------------------------------------------

MENU_VIDEO: Final[str] = VIDEO
MENU_BATCH: Final[str] = "📦"  # U+1F4E6 — package
MENU_QUEUE: Final[str] = QUEUED
MENU_HISTORY: Final[str] = "📜"  # U+1F4DC — scroll
MENU_PRESETS: Final[str] = "🎤"  # U+1F3A4 — microphone
MENU_IMAGES: Final[str] = IMAGE
MENU_AUDIOS: Final[str] = AUDIO
MENU_SETTINGS: Final[str] = "🔧"  # U+1F527 — wrench, Emoji_Presentation (Wide
# puro, no requiere VS16 a diferencia de 🛠️).
MENU_LOGS: Final[str] = "📋"  # U+1F4CB — clipboard
MENU_QUIT: Final[str] = "🚪"  # U+1F6AA — door
