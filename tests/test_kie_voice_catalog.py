"""Tests del catálogo built-in de voces (67 entradas curadas por Kie)."""

from __future__ import annotations

from kie_avatar_studio.domain.kie_voice_catalog import (
    BUILTIN_VOICES,
    KieVoice,
    get_builtin_voice,
    is_builtin_voice,
)

EXPECTED_VOICE_COUNT = 67


def test_catalog_has_expected_count() -> None:
    """Kie publica 67 voces curadas; si cambia, hay que actualizar el catálogo."""
    assert len(BUILTIN_VOICES) == EXPECTED_VOICE_COUNT


def test_voice_ids_are_unique() -> None:
    ids = [voice.voice_id for voice in BUILTIN_VOICES]
    assert len(set(ids)) == EXPECTED_VOICE_COUNT


def test_every_voice_has_non_empty_label() -> None:
    for voice in BUILTIN_VOICES:
        assert voice.label, f"voice {voice.voice_id!r} sin label"


def test_known_voice_resolves_with_label() -> None:
    voice = get_builtin_voice("EkK5I93UQWFDigLMpZcX")
    assert voice is not None
    assert voice.label == "James"
    assert voice.description == "Husky, Engaging and Bold"


def test_unknown_voice_returns_none() -> None:
    assert get_builtin_voice("voice-id-inventado") is None


def test_is_builtin_voice() -> None:
    assert is_builtin_voice("EkK5I93UQWFDigLMpZcX") is True
    assert is_builtin_voice("voice-id-inventado") is False


def test_preview_url_uses_kie_static_cdn() -> None:
    voice = BUILTIN_VOICES[0]
    assert voice.preview_url == (
        f"https://static.aiquickdraw.com/elevenlabs/voice/{voice.voice_id}.mp3"
    )


def test_display_name_with_description() -> None:
    voice = KieVoice(voice_id="X", label="James", description="Husky")
    assert voice.display_name == "James — Husky"


def test_display_name_without_description() -> None:
    voice = KieVoice(voice_id="X", label="Pirate Marshal")
    assert voice.display_name == "Pirate Marshal"


def test_voice_without_description_in_catalog() -> None:
    """Hay 3 voces en el catálogo sin description: Northern Terry, British Football
    Announcer, Pirate Marshal — el spec no las describe."""
    no_desc = [v for v in BUILTIN_VOICES if v.description == ""]
    assert len(no_desc) >= 1
    labels = {v.label for v in no_desc}
    assert "Northern Terry" in labels


def test_catalog_is_immutable_tuple() -> None:
    """El catálogo se expone como tuple para evitar mutaciones accidentales."""
    assert isinstance(BUILTIN_VOICES, tuple)
