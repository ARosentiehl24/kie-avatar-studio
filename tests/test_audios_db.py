"""Tests de `AudiosDB` — CRUD + JSON column para voice_settings + delete_many."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kie_avatar_studio.domain.errors import AudioNotFoundError
from kie_avatar_studio.domain.models import GeneratedAudio, VoiceSettings
from kie_avatar_studio.infra.audios_db import AudiosDB


@pytest.fixture
async def db(tmp_path: Path) -> AudiosDB:
    d = AudiosDB(tmp_path / "jobs.db")
    await d.init()
    return d


def _audio(audio_id: str = "aud-1", **overrides: object) -> GeneratedAudio:
    base: dict[str, object] = dict(
        id=audio_id,
        label=f"label-{audio_id}",
        script="Hola mundo",
        voice_id="EkK5I93UQWFDigLMpZcX",
        kie_url=f"https://tempfile.redpandaai.co/kieai/{audio_id}.mp3",
        kie_file_path=f"kieai/{audio_id}.mp3",
    )
    base.update(overrides)
    return GeneratedAudio(**base)  # type: ignore[arg-type]


async def test_init_creates_schema(db: AudiosDB) -> None:
    await db.upsert(_audio())
    fetched = await db.get("aud-1")
    assert fetched is not None


async def test_get_missing_returns_none(db: AudiosDB) -> None:
    assert await db.get("ghost") is None


async def test_list_recent_orders_desc(db: AudiosDB) -> None:
    earlier = _audio("a", generated_at=datetime(2026, 1, 1, tzinfo=UTC))
    later = _audio("b", generated_at=datetime(2026, 1, 2, tzinfo=UTC))
    await db.upsert(earlier)
    await db.upsert(later)
    listed = await db.list_recent()
    assert [a.id for a in listed] == ["b", "a"]


async def test_upsert_persists_voice_settings_as_json(db: AudiosDB) -> None:
    settings = VoiceSettings(stability=0.3, similarity_boost=0.9, language_code="es")
    await db.upsert(_audio(voice_settings=settings))
    fetched = await db.get("aud-1")
    assert fetched is not None
    assert fetched.voice_settings is not None
    assert fetched.voice_settings.stability == 0.3
    assert fetched.voice_settings.similarity_boost == 0.9
    assert fetched.voice_settings.language_code == "es"
    # Los no seteados quedan en None (no se inventan defaults).
    assert fetched.voice_settings.style is None
    assert fetched.voice_settings.speed is None


async def test_upsert_with_none_voice_settings(db: AudiosDB) -> None:
    await db.upsert(_audio())
    fetched = await db.get("aud-1")
    assert fetched is not None
    assert fetched.voice_settings is None


async def test_upsert_with_empty_voice_settings_persists_as_none(db: AudiosDB) -> None:
    """Si voice_settings está vacío (todos los campos None), persistimos NULL
    para que `voice_settings_json` no quede como `{}` ruidoso en la DB."""
    await db.upsert(_audio(voice_settings=VoiceSettings()))
    fetched = await db.get("aud-1")
    assert fetched is not None
    assert fetched.voice_settings is None


async def test_optional_metadata_fields(db: AudiosDB) -> None:
    audio = _audio(file_size=12345, mime_type="audio/mpeg", duration_seconds=4.2)
    await db.upsert(audio)
    fetched = await db.get("aud-1")
    assert fetched is not None
    assert fetched.file_size == 12345
    assert fetched.mime_type == "audio/mpeg"
    assert fetched.duration_seconds == 4.2


async def test_upsert_updates_existing(db: AudiosDB) -> None:
    await db.upsert(_audio(label="original"))
    await db.upsert(_audio(label="renombrado"))
    fetched = await db.get("aud-1")
    assert fetched is not None
    assert fetched.label == "renombrado"
    assert len(await db.list_recent()) == 1


async def test_delete_existing(db: AudiosDB) -> None:
    await db.upsert(_audio())
    await db.delete("aud-1")
    assert await db.get("aud-1") is None


async def test_delete_missing_raises(db: AudiosDB) -> None:
    with pytest.raises(AudioNotFoundError):
        await db.delete("ghost")


async def test_delete_many_removes_listed_ids(db: AudiosDB) -> None:
    for i in range(3):
        await db.upsert(_audio(f"aud-{i}"))
    await db.delete_many(["aud-0", "aud-2"])
    remaining = await db.list_recent()
    assert {a.id for a in remaining} == {"aud-1"}


async def test_delete_many_with_empty_list_is_noop(db: AudiosDB) -> None:
    await db.upsert(_audio())
    await db.delete_many([])
    assert await db.get("aud-1") is not None


async def test_delete_many_ignores_missing_ids(db: AudiosDB) -> None:
    """Idempotente: borra los que existen, ignora los que no, sin lanzar."""
    await db.upsert(_audio("aud-1"))
    await db.delete_many(["aud-1", "ghost1", "ghost2"])
    assert await db.get("aud-1") is None


async def test_serialization_preserves_timezone(db: AudiosDB) -> None:
    original_dt = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    await db.upsert(_audio(generated_at=original_dt))
    fetched = await db.get("aud-1")
    assert fetched is not None
    assert fetched.generated_at == original_dt
    assert fetched.generated_at.tzinfo is not None


async def test_age_calculation_through_db_roundtrip(db: AudiosDB) -> None:
    """Smoke: el TTL helper sigue funcionando tras ir y volver de SQLite."""
    old = _audio(generated_at=datetime.now(UTC) - timedelta(days=15))
    await db.upsert(old)
    fetched = await db.get("aud-1")
    assert fetched is not None
    assert fetched.is_expired(14) is True
