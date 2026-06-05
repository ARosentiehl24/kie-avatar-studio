"""Tests del `VoicePresetsStore` (file-based JSON)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kie_avatar_studio.domain.models import VoicePreset, VoiceSettings
from kie_avatar_studio.infra.presets_store import VoicePresetsStore


@pytest.fixture
async def store(tmp_path: Path) -> VoicePresetsStore:
    s = VoicePresetsStore(tmp_path / "presets")
    await s.init()
    return s


def _preset(
    preset_id: str = "narrador",
    voice_settings: VoiceSettings | None = None,
) -> VoicePreset:
    return VoicePreset(
        id=preset_id,
        label=preset_id,
        voice_id="EkK5I93UQWFDigLMpZcX",
        voice_settings=voice_settings,
    )


# --- init -----------------------------------------------------------------


async def test_init_crea_subdir_voices(tmp_path: Path) -> None:
    s = VoicePresetsStore(tmp_path / "presets")
    await s.init()
    assert (tmp_path / "presets" / "voices").is_dir()


async def test_init_idempotente(tmp_path: Path) -> None:
    s = VoicePresetsStore(tmp_path / "presets")
    await s.init()
    await s.init()
    assert await s.list_all() == []


# --- upsert / get / list ---------------------------------------------------


async def test_upsert_persiste_y_get_lee(store: VoicePresetsStore) -> None:
    preset = _preset(voice_settings=VoiceSettings(stability=0.5))
    await store.upsert(preset)
    fetched = await store.get("narrador")
    assert fetched is not None
    assert fetched.id == "narrador"
    assert fetched.voice_settings is not None
    assert fetched.voice_settings.stability == 0.5


async def test_upsert_sobrescribe_existente(store: VoicePresetsStore) -> None:
    await store.upsert(_preset())
    await store.upsert(_preset(voice_settings=VoiceSettings(stability=0.9)))
    fetched = await store.get("narrador")
    assert fetched is not None
    assert fetched.voice_settings is not None
    assert fetched.voice_settings.stability == 0.9


async def test_get_devuelve_none_si_no_existe(store: VoicePresetsStore) -> None:
    assert await store.get("ghost") is None


async def test_list_all_ordena_por_label(store: VoicePresetsStore) -> None:
    await store.upsert(_preset("zorro"))
    await store.upsert(_preset("alfa"))
    await store.upsert(_preset("mariposa"))
    listed = await store.list_all()
    assert [p.id for p in listed] == ["alfa", "mariposa", "zorro"]


async def test_list_all_vacio_devuelve_lista(tmp_path: Path) -> None:
    s = VoicePresetsStore(tmp_path / "presets")
    # Sin llamar init: no debe crashear.
    assert await s.list_all() == []


# --- delete ----------------------------------------------------------------


async def test_delete_remueve_archivo(store: VoicePresetsStore, tmp_path: Path) -> None:
    await store.upsert(_preset())
    json_file = tmp_path / "presets" / "voices" / "narrador.json"
    assert json_file.is_file()
    await store.delete("narrador")
    assert not json_file.exists()
    assert await store.get("narrador") is None


async def test_delete_idempotente(store: VoicePresetsStore) -> None:
    # No debe lanzar si el archivo no existe.
    await store.delete("ghost")


# --- robustez --------------------------------------------------------------


async def test_archivo_json_corrupto_se_ignora(store: VoicePresetsStore, tmp_path: Path) -> None:
    """Un JSON malformado NO debe romper list_all (solo se ignora)."""
    bad = tmp_path / "presets" / "voices" / "corrupto.json"
    bad.write_text("not valid json {{{", encoding="utf-8")
    await store.upsert(_preset("ok"))

    listed = await store.list_all()

    assert len(listed) == 1
    assert listed[0].id == "ok"


async def test_json_format_es_legible(store: VoicePresetsStore, tmp_path: Path) -> None:
    """El JSON debe ser indented para que el usuario lo pueda editar a mano."""
    await store.upsert(_preset(voice_settings=VoiceSettings(stability=0.5)))
    raw = (tmp_path / "presets" / "voices" / "narrador.json").read_text(encoding="utf-8")
    # Tiene saltos de linea (indent=2) — no es un blob de una sola linea.
    assert "\n" in raw
    # Y es JSON válido.
    data = json.loads(raw)
    assert data["id"] == "narrador"
    assert data["voice_settings"]["stability"] == 0.5
