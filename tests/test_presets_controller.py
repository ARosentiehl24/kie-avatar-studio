"""Tests del `VoicePresetsController`: CRUD + validación."""

from __future__ import annotations

from pathlib import Path

import pytest

from kie_avatar_studio.app_layer.presets_controller import VoicePresetsController
from kie_avatar_studio.domain.errors import (
    VoicePresetNotFoundError,
    VoicePresetValidationError,
)
from kie_avatar_studio.domain.models import VoiceSettings
from kie_avatar_studio.infra.presets_store import VoicePresetsStore


@pytest.fixture
async def controller(tmp_path: Path) -> VoicePresetsController:
    store = VoicePresetsStore(tmp_path / "presets")
    await store.init()
    return VoicePresetsController(store)


# --- create ----------------------------------------------------------------


async def test_create_genera_id_desde_label(
    controller: VoicePresetsController,
) -> None:
    preset = await controller.create(label="Narrador Calmo", voice_id="EkK5I93UQWFDigLMpZcX")
    # Slug: lowercase + underscore (sanitize_filename usa _ no -).
    assert preset.id == "narrador_calmo"
    assert preset.label == "Narrador Calmo"


async def test_create_rechaza_label_vacio(
    controller: VoicePresetsController,
) -> None:
    with pytest.raises(VoicePresetValidationError, match="label"):
        await controller.create(label="   ", voice_id="EkK5I93UQWFDigLMpZcX")


async def test_create_rechaza_label_demasiado_largo(
    controller: VoicePresetsController,
) -> None:
    with pytest.raises(VoicePresetValidationError, match="64"):
        await controller.create(label="x" * 65, voice_id="EkK5I93UQWFDigLMpZcX")


async def test_create_normaliza_voice_settings_vacios_a_none(
    controller: VoicePresetsController,
) -> None:
    """`VoiceSettings(stability=None, ...)` (vacío) debe persistirse como None."""
    settings = VoiceSettings()  # todos None
    preset = await controller.create(
        label="x", voice_id="EkK5I93UQWFDigLMpZcX", voice_settings=settings
    )
    assert preset.voice_settings is None


async def test_create_persiste_voice_settings_no_vacios(
    controller: VoicePresetsController,
) -> None:
    settings = VoiceSettings(stability=0.3, speed=1.1)
    preset = await controller.create(
        label="x", voice_id="EkK5I93UQWFDigLMpZcX", voice_settings=settings
    )
    assert preset.voice_settings is not None
    assert preset.voice_settings.stability == 0.3
    assert preset.voice_settings.speed == 1.1


async def test_create_rechaza_descripcion_demasiado_larga(
    controller: VoicePresetsController,
) -> None:
    with pytest.raises(VoicePresetValidationError, match="200"):
        await controller.create(
            label="x",
            voice_id="EkK5I93UQWFDigLMpZcX",
            description="z" * 201,
        )


async def test_create_descripcion_vacia_persiste_como_none(
    controller: VoicePresetsController,
) -> None:
    preset = await controller.create(label="x", voice_id="EkK5I93UQWFDigLMpZcX", description="  ")
    assert preset.description is None


# --- update ----------------------------------------------------------------


async def test_update_conserva_id(controller: VoicePresetsController) -> None:
    original = await controller.create(label="viejo", voice_id="EkK5I93UQWFDigLMpZcX")
    updated = await controller.update(
        original.id, label="renombrado", voice_id="EkK5I93UQWFDigLMpZcX"
    )
    # El id NO cambia aunque el label sí.
    assert updated.id == original.id
    assert updated.label == "renombrado"


async def test_update_lanza_si_no_existe(
    controller: VoicePresetsController,
) -> None:
    with pytest.raises(VoicePresetNotFoundError):
        await controller.update("ghost", label="x", voice_id="EkK5I93UQWFDigLMpZcX")


# --- delete ----------------------------------------------------------------


async def test_delete_remueve(controller: VoicePresetsController) -> None:
    preset = await controller.create(label="x", voice_id="EkK5I93UQWFDigLMpZcX")
    await controller.delete(preset.id)
    assert await controller.get(preset.id) is None


async def test_delete_idempotente(controller: VoicePresetsController) -> None:
    # No lanza si no existe.
    await controller.delete("ghost")


# --- get / get_for_use -----------------------------------------------------


async def test_get_devuelve_preset_existente(
    controller: VoicePresetsController,
) -> None:
    created = await controller.create(label="x", voice_id="EkK5I93UQWFDigLMpZcX")
    fetched = await controller.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id


async def test_get_for_use_lanza_si_no_existe(
    controller: VoicePresetsController,
) -> None:
    with pytest.raises(VoicePresetNotFoundError):
        await controller.get_for_use("ghost")


async def test_list_all_ordenado_por_label(
    controller: VoicePresetsController,
) -> None:
    await controller.create(label="zorro", voice_id="EkK5I93UQWFDigLMpZcX")
    await controller.create(label="alfa", voice_id="EkK5I93UQWFDigLMpZcX")
    listed = await controller.list_all()
    assert [p.label for p in listed] == ["alfa", "zorro"]
