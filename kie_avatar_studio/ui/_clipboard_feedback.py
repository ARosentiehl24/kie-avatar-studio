"""Helper UI compartido para copiar al clipboard con feedback claro.

Centraliza el patrón "intentar copiar + reportar al usuario con mensaje
accionable" que las 3 pantallas (Audios, Images, Videos) usan.

Diseño del mensaje: corto y accionable. El éxito no incluye la URL
(ya está en el clipboard del SO y/o visible en la tabla); el error sí
la incluye en una línea aparte para que el usuario la pueda copiar a
mano del status bar.
"""

from __future__ import annotations

from collections.abc import Callable

from ..app_layer.clipboard import ClipboardResult, copy_to_clipboard


async def copy_url_with_feedback(
    url: str,
    *,
    osc52_fallback: Callable[[str], None] | None,
) -> tuple[str, bool]:
    """Copia `url` al clipboard. Devuelve `(message, is_error)`.

    Mensajes mantenidos a una línea (caben en el toast de Textual sin
    truncar el contenido importante). La URL solo aparece en el error,
    cuando el usuario la necesita para copiar a mano.

    `osc52_fallback` lo pasa el caller con `self.app.copy_to_clipboard`
    (intento defensivo via secuencia escape; muchas terminales lo
    aceptan transparentemente).
    """
    result: ClipboardResult = await copy_to_clipboard(url, osc52_fallback=osc52_fallback)

    if result.success and result.backend != "osc52":
        # Backend nativo confirmado: el clipboard del SO sí recibió el texto.
        return ("✅ URL copiada al clipboard", False)

    if result.success and result.backend == "osc52":
        # Best-effort: la secuencia OSC 52 se envió al terminal, pero
        # no podemos saber si la aceptó.
        return ("↪ URL enviada al clipboard (terminal escape)", False)

    # Falló todo: backend explícito y sin fallback. Aquí SÍ damos la URL
    # porque el usuario la necesita para copiar a mano.
    detail = result.error or "sin backend disponible"
    return (f"❌ No pude copiar ({detail}): {url}", True)
