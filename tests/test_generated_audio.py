"""Tests del modelo `GeneratedAudio` (defaults, expires_at, is_expired, time_left)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kie_avatar_studio.domain.models import GeneratedAudio, VoiceSettings
from kie_avatar_studio.domain.policies import KIE_GENERATED_RETENTION_DAYS


def _audio(generated_at: datetime | None = None, **overrides: object) -> GeneratedAudio:
    base: dict[str, object] = dict(
        id="aud-1",
        label="Saludo",
        script="Hola mundo",
        voice_id="EkK5I93UQWFDigLMpZcX",
        kie_url="https://tempfile.redpandaai.co/kieai/abc.mp3",
        kie_file_path="kieai/abc.mp3",
    )
    if generated_at is not None:
        base["generated_at"] = generated_at
    base.update(overrides)
    return GeneratedAudio(**base)  # type: ignore[arg-type]


def test_default_fields() -> None:
    audio = _audio()
    assert audio.id == "aud-1"
    assert audio.voice_settings is None
    assert audio.file_size is None
    assert audio.mime_type is None
    assert audio.duration_seconds is None
    assert isinstance(audio.generated_at, datetime)


def test_voice_settings_optional_can_be_present() -> None:
    audio = _audio(voice_settings=VoiceSettings(stability=0.5))
    assert audio.voice_settings is not None
    assert audio.voice_settings.stability == 0.5


def test_serialization_roundtrip_with_settings() -> None:
    original = _audio(voice_settings=VoiceSettings(stability=0.3, language_code="en"))
    restored = GeneratedAudio.model_validate_json(original.model_dump_json())
    assert restored == original


def test_serialization_roundtrip_without_settings() -> None:
    original = _audio()
    restored = GeneratedAudio.model_validate_json(original.model_dump_json())
    assert restored == original


# --- Expiración derivada de generated_at + retention -------------------------


def test_expires_at_adds_retention_days() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    audio = _audio(generated_at=now)
    assert audio.expires_at(KIE_GENERATED_RETENTION_DAYS) == now + timedelta(
        days=KIE_GENERATED_RETENTION_DAYS
    )


def test_is_expired_true_after_retention_window() -> None:
    generated = datetime(2026, 1, 1, tzinfo=UTC)
    audio = _audio(generated_at=generated)
    later = generated + timedelta(days=KIE_GENERATED_RETENTION_DAYS + 1)
    assert audio.is_expired(KIE_GENERATED_RETENTION_DAYS, now=later) is True


def test_is_expired_false_within_window() -> None:
    generated = datetime(2026, 1, 1, tzinfo=UTC)
    audio = _audio(generated_at=generated)
    later = generated + timedelta(days=KIE_GENERATED_RETENTION_DAYS - 1, hours=23)
    assert audio.is_expired(KIE_GENERATED_RETENTION_DAYS, now=later) is False


def test_time_left_positive_within_window() -> None:
    generated = datetime(2026, 1, 1, tzinfo=UTC)
    audio = _audio(generated_at=generated)
    later = generated + timedelta(days=2)
    assert audio.time_left(KIE_GENERATED_RETENTION_DAYS, now=later) == timedelta(days=12)


def test_time_left_negative_after_expiry() -> None:
    generated = datetime(2026, 1, 1, tzinfo=UTC)
    audio = _audio(generated_at=generated)
    later = generated + timedelta(days=KIE_GENERATED_RETENTION_DAYS + 6)
    delta = audio.time_left(KIE_GENERATED_RETENTION_DAYS, now=later)
    assert delta.total_seconds() < 0
    assert delta == timedelta(days=-6)
