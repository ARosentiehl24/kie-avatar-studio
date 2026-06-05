"""Tests del modelo `VoiceSettings` (rangos exactos del OpenAPI de Kie)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kie_avatar_studio.domain.models import VoiceSettings


def test_empty_settings_is_empty() -> None:
    settings = VoiceSettings()
    assert settings.is_empty()
    assert settings.model_dump(exclude_none=True) == {}


def test_full_settings_not_empty() -> None:
    settings = VoiceSettings(
        stability=0.5,
        similarity_boost=0.75,
        style=0.0,
        speed=1.0,
        language_code="es",
    )
    assert not settings.is_empty()
    dumped = settings.model_dump(exclude_none=True)
    assert dumped == {
        "stability": 0.5,
        "similarity_boost": 0.75,
        "style": 0.0,
        "speed": 1.0,
        "language_code": "es",
    }


def test_partial_settings_not_empty() -> None:
    """Si UN solo campo está seteado, no es empty (el JSON irá al endpoint)."""
    assert not VoiceSettings(stability=0.3).is_empty()
    assert not VoiceSettings(language_code="en").is_empty()


@pytest.mark.parametrize(
    "field,value",
    [
        ("stability", -0.01),
        ("stability", 1.01),
        ("similarity_boost", -0.01),
        ("similarity_boost", 1.01),
        ("style", -0.01),
        ("style", 1.01),
        ("speed", 0.69),
        ("speed", 1.21),
    ],
)
def test_rejects_out_of_range(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        VoiceSettings(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("stability", 0.0),
        ("stability", 1.0),
        ("similarity_boost", 0.0),
        ("similarity_boost", 1.0),
        ("style", 0.0),
        ("style", 1.0),
        ("speed", 0.7),
        ("speed", 1.2),
    ],
)
def test_accepts_boundary_values(field: str, value: float) -> None:
    settings = VoiceSettings(**{field: value})
    assert getattr(settings, field) == value


def test_serialization_roundtrip() -> None:
    original = VoiceSettings(stability=0.5, language_code="es")
    restored = VoiceSettings.model_validate_json(original.model_dump_json())
    assert restored == original


def test_exclude_none_drops_unset_fields() -> None:
    """Solo los campos con valor se mandan a Kie; el resto usa el default del proveedor."""
    settings = VoiceSettings(stability=0.5)
    assert settings.model_dump(exclude_none=True) == {"stability": 0.5}
