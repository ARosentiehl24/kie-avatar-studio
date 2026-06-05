"""Controller para `VoicePreset`: CRUD + validación.

Espejo simétrico de `AudiosController`/`ImagesController`. Solo
orquesta validación + persistencia. Sin estado.

La validación granular (rangos de voice_settings, voice_id válido)
reusa `domain.policies` que ya valida los mismos campos cuando se
crea un AudioJob. Esto garantiza que un preset guardado SIEMPRE
produce un AudioJob válido al usarlo.
"""

from __future__ import annotations

from typing import Final

from loguru import logger

from ..domain.errors import (
    VoicePresetNotFoundError,
    VoicePresetValidationError,
)
from ..domain.models import VoicePreset, VoiceSettings
from ..domain.policies import validate_voice_id, validate_voice_settings
from ..domain.ports import VoicePresetStore
from .ids import sanitize_filename

_LABEL_MAX_LENGTH: Final[int] = 64
_DESCRIPTION_MAX_LENGTH: Final[int] = 200


class VoicePresetsController:
    """Casos de uso sobre `VoicePreset`. CRUD validado."""

    def __init__(self, store: VoicePresetStore) -> None:
        self._store = store

    async def list_all(self) -> list[VoicePreset]:
        return await self._store.list_all()

    async def get(self, preset_id: str) -> VoicePreset | None:
        return await self._store.get(preset_id)

    async def get_for_use(self, preset_id: str) -> VoicePreset:
        """Devuelve el preset listo para usar o lanza si no existe.

        Pensado para callers que asumen que el preset existe (ej. el
        modal Generate Audio cuando el usuario seleccionó uno del
        Select y vamos a precargar los inputs).
        """
        preset = await self._store.get(preset_id)
        if preset is None:
            raise VoicePresetNotFoundError(f"no existe ningún preset de voz con id={preset_id!r}")
        return preset

    async def create(
        self,
        label: str,
        voice_id: str,
        voice_settings: VoiceSettings | None = None,
        description: str | None = None,
    ) -> VoicePreset:
        """Crea un preset nuevo. El id se deriva del label sanitizado.

        Si ya existe un preset con el mismo id (label colisión), lo
        sobreescribe (mismo comportamiento que `upsert` en otros
        stores). Esto es intencional para que el usuario pueda
        renombrar/iterar sin acumular basura.
        """
        clean_label = self._validate_label(label)
        validate_voice_id(voice_id, allow_custom=True)
        if voice_settings is not None and not voice_settings.is_empty():
            validate_voice_settings(voice_settings)
        else:
            # Normalizamos `VoiceSettings(stability=None, ...)` (objeto
            # vacío) a `None` para que el JSON serializado sea más limpio.
            voice_settings = None
        clean_description = self._validate_description(description)
        preset_id = self._make_id(clean_label)
        preset = VoicePreset(
            id=preset_id,
            label=clean_label,
            voice_id=voice_id,
            voice_settings=voice_settings,
            description=clean_description,
        )
        await self._store.upsert(preset)
        logger.info("VoicePreset '{}' guardado (id={})", clean_label, preset_id)
        return preset

    async def update(
        self,
        preset_id: str,
        label: str,
        voice_id: str,
        voice_settings: VoiceSettings | None = None,
        description: str | None = None,
    ) -> VoicePreset:
        """Actualiza un preset existente conservando su id original.

        A diferencia de `create`, NO deriva el id del label. Esto
        permite renombrar el label sin que las referencias externas
        (en el futuro) se rompan. Lanza si el id no existe.
        """
        existing = await self._store.get(preset_id)
        if existing is None:
            raise VoicePresetNotFoundError(f"no existe ningún preset de voz con id={preset_id!r}")
        clean_label = self._validate_label(label)
        validate_voice_id(voice_id, allow_custom=True)
        if voice_settings is not None and not voice_settings.is_empty():
            validate_voice_settings(voice_settings)
        else:
            voice_settings = None
        clean_description = self._validate_description(description)
        updated = existing.model_copy(
            update={
                "label": clean_label,
                "voice_id": voice_id,
                "voice_settings": voice_settings,
                "description": clean_description,
            }
        )
        await self._store.upsert(updated)
        logger.info("VoicePreset '{}' actualizado (id={})", clean_label, preset_id)
        return updated

    async def delete(self, preset_id: str) -> None:
        """Borra el preset. Idempotente (no lanza si no existe)."""
        await self._store.delete(preset_id)
        logger.info("VoicePreset {} eliminado", preset_id)

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _validate_label(label: str) -> str:
        clean = label.strip()
        if not clean:
            raise VoicePresetValidationError("el label del preset no puede estar vacío")
        if len(clean) > _LABEL_MAX_LENGTH:
            raise VoicePresetValidationError(
                f"el label del preset supera {_LABEL_MAX_LENGTH} caracteres"
            )
        return clean

    @staticmethod
    def _validate_description(description: str | None) -> str | None:
        if description is None:
            return None
        clean = description.strip()
        if not clean:
            return None
        if len(clean) > _DESCRIPTION_MAX_LENGTH:
            raise VoicePresetValidationError(
                f"la descripción del preset supera {_DESCRIPTION_MAX_LENGTH} caracteres"
            )
        return clean

    @staticmethod
    def _make_id(label: str) -> str:
        """`id` derivado del label: sanitizado + lowercase.

        Mismo criterio que `ImagesController` para que los archivos
        en disco sean legibles. Si el label es 'Narrador Calmo', el
        id es 'narrador_calmo' y el archivo 'narrador_calmo.json'.
        """
        return sanitize_filename(label).lower()
