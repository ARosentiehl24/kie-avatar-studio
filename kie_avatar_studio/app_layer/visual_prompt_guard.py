"""Guardas visuales compartidas para prompts de imagen/video.

Dos variantes con políticas distintas:

- `append_image_visual_guard` (estática): "NO generar" texto/UI/iconos.
  Aplica a Nano Banana 2, GPT Image 2 y cualquier text-to-image. Si la
  imagen ya nace limpia, el video no puede arrastrar lo que no está.

- `append_video_visual_guard` (animación): "REMOVER" cualquier texto/UI
  que aparezca durante la animación. Aplica a Kling Avatar Pro (a-roll)
  y Kling 3.0 video (b-roll). Crítico porque Kling es un modelo entrenado
  por Kuaishou y tiende a inyectar UI Douyin/TikTok, captions auto-generados,
  caracteres CJK y watermarks — independientemente del contenido de la
  imagen de referencia.

Ambos guards listan explícitamente: caracteres chinos/japoneses/coreanos,
UI de apps sociales (TikTok/Douyin/Instagram/WhatsApp), notification
badges, brand logos y watermarks. Antes el guard único decía "preservar
texto naturalmente presente" — eso le pedía a Avatar Pro mantener
intactas las alucinaciones de Nano Banana en lugar de eliminarlas.
"""

from __future__ import annotations

from typing import Final

from ..domain.policies import MAX_PROMPT_CHARS

_FORBIDDEN_ELEMENTS: Final[str] = (
    "captions, subtitles, text overlays, UI elements, titles, labels, "
    "signage, watermarks, floating letters, Chinese/Japanese/Korean "
    "characters, social media app UI (TikTok, Douyin, Instagram, "
    "WhatsApp), notification badges, brand logos, or invented readable "
    "words"
)

IMAGE_VISUAL_GUARD: Final[str] = (
    f"Visual text policy: do NOT include any {_FORBIDDEN_ELEMENTS} anywhere in the image."
)

VIDEO_VISUAL_GUARD: Final[str] = (
    "Visual text policy: do NOT introduce or preserve any "
    f"{_FORBIDDEN_ELEMENTS}. If any such element is present in the "
    "reference image, remove it during animation. The final video must "
    "contain no on-screen text or UI other than naturally occurring "
    "real-world objects (e.g. printed product labels physically present "
    "in the scene)."
)

# Alias retrocompatible: la API legacy mapeaba a una variante única.
# Mantenido para que cambios externos no rompan; nuevos callers deben
# usar `append_image_visual_guard` o `append_video_visual_guard` según
# corresponda.
# TODO(1.4.0): retirar alias si no aparecieron callers externos.
VISUAL_TEXT_GUARD: Final[str] = IMAGE_VISUAL_GUARD


def _append_guard(prompt: str, guard: str, *, max_chars: int) -> str:
    """Concatena el guard al prompt respetando el límite de Kie."""
    clean = prompt.strip()
    if not clean or guard in clean:
        return clean
    guarded = f"{clean}. {guard}"
    if len(guarded) > max_chars:
        return clean
    return guarded


def append_image_visual_guard(prompt: str, *, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Guard para generación de imagen (Nano Banana / GPT Image 2).

    Política: NO incluir texto/UI/iconos en la imagen generada. Si el
    guard no cabe dentro de `max_chars`, devuelve el prompt sin tocar
    (no rompe el límite de Kie).
    """
    return _append_guard(prompt, IMAGE_VISUAL_GUARD, max_chars=max_chars)


def append_video_visual_guard(prompt: str, *, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Guard para generación de video (Avatar Pro / Kling 3.0 b-roll).

    Política: REMOVER cualquier texto/UI/iconos que aparezca durante la
    animación. A diferencia del guard de imagen, este instruye al modelo
    a eliminar elementos preexistentes en la imagen de referencia (no a
    preservarlos). Si no cabe, devuelve el prompt sin tocar.
    """
    return _append_guard(prompt, VIDEO_VISUAL_GUARD, max_chars=max_chars)


def append_visual_text_guard(prompt: str, *, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Alias retrocompatible — usa el guard de imagen.

    Nuevos callers deben elegir explícitamente entre
    `append_image_visual_guard` y `append_video_visual_guard`.
    """
    return append_image_visual_guard(prompt, max_chars=max_chars)
