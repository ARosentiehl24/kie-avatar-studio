"""Helpers UI para construir `VoiceSettings` desde formularios."""

from __future__ import annotations

from ...domain.errors import VoiceSettingsValidationError
from ...domain.models import VoiceSettings


def build_voice_settings(
    *,
    stability: float | None,
    similarity_boost: float | None,
    style: float | None,
    speed: float | None,
    language_code: str | None,
) -> VoiceSettings | None:
    """Construye settings o `None`; errores de rango se muestran en UI."""
    if all(v is None for v in (stability, similarity_boost, style, speed, language_code)):
        return None
    try:
        return VoiceSettings(
            stability=stability,
            similarity_boost=similarity_boost,
            style=style,
            speed=speed,
            language_code=language_code,
        )
    except ValueError as exc:
        raise VoiceSettingsValidationError(str(exc)) from exc
