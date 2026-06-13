"""Guardas visuales compartidas para prompts de imagen/video."""

from __future__ import annotations

from typing import Final

from ..domain.policies import MAX_PROMPT_CHARS

VISUAL_TEXT_GUARD: Final[str] = (
    "Visual text policy: no added captions, subtitles, text overlays, UI, "
    "titles, labels, signage, floating letters, watermarks, or invented readable "
    "words. Do not add any new text beyond text already naturally present in "
    "the reference image."
)


def append_visual_text_guard(prompt: str, *, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """Añade una instrucción anti-letreros sin romper el límite de Kie."""
    clean = prompt.strip()
    if not clean or VISUAL_TEXT_GUARD in clean:
        return clean
    guarded = f"{clean}. {VISUAL_TEXT_GUARD}"
    if len(guarded) > max_chars:
        return clean
    return guarded
