"""Opciones de idioma para presets de voz (Kie/ElevenLabs)."""

from __future__ import annotations

from typing import Final

LANGUAGE_AUTO_SENTINEL: Final[str] = "__auto__"

_LANGUAGE_OPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("Auto / sin forzar idioma", LANGUAGE_AUTO_SENTINEL),
    ("Español latinoamericano — es-419", "es-419"),
    ("Español — es", "es"),
    ("Español España — es-ES", "es-ES"),
    ("English — en", "en"),
    ("English US — en-US", "en-US"),
    ("English UK — en-GB", "en-GB"),
    ("Português Brasil — pt-BR", "pt-BR"),
    ("Português — pt", "pt"),
    ("Français — fr", "fr"),
    ("Deutsch — de", "de"),
    ("Italiano — it", "it"),
    ("Nederlands — nl", "nl"),
    ("Polski — pl", "pl"),
    ("Türkçe — tr", "tr"),
    ("हिन्दी — hi", "hi"),
    ("العربية — ar", "ar"),
    ("中文 — zh", "zh"),
    ("日本語 — ja", "ja"),
    ("한국어 — ko", "ko"),
)


def voice_language_options(current: str) -> list[tuple[str, str]]:
    """Devuelve opciones del Select, preservando códigos existentes desconocidos."""
    options = list(_LANGUAGE_OPTIONS)
    known_values = {value for _, value in options}
    if current not in known_values:
        options.append((f"Código actual — {current}", current))
    return options


def selected_language_code(value: object) -> str | None:
    if isinstance(value, str) and value != LANGUAGE_AUTO_SENTINEL:
        return value
    return None
