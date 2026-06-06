"""Helpers y modelo del contexto de ejecución de un workflow.

Extraído de `workflow_step_runner.py` para mantenerlo dentro del límite
CR-3.2 (≤300 líneas) y separar la lógica de:
- contexto compartido entre steps (resolución TTS, voice settings, base ref)
- gestión del progress dict por step (init, mutación, cleanup)
- helpers de construcción de prompts/refs

No tiene estado mutable propio: todas las funciones son puras o trabajan
sobre el `WorkflowStep` recibido por argumento.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from ..domain.models import (
    ImageAssetRef,
    StepType,
    VoiceSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStep,
)
from ..domain.policies import expected_progress_keys_for_step

DEFAULT_TURBO_MODEL: Final[str] = "elevenlabs/text-to-speech-turbo-v2-5"


class WorkflowExecutionContext:
    """Contexto compartido por todos los steps de UN workflow ejecutándose.

    Centraliza referencias inmutables durante la ejecución (audio_language,
    voice settings resueltos, imagen base) para que el step runner no
    tenga que volver a resolverlos por step.
    """

    def __init__(
        self,
        *,
        audio_language: str | None,
        voice_id: str,
        voice_settings: VoiceSettings | None,
        base_image_ref: ImageAssetRef,
        output_dir: Path,
    ) -> None:
        self.audio_language = audio_language
        self.voice_id = voice_id
        self.voice_settings = voice_settings
        self.base_image_ref = base_image_ref
        self.output_dir = output_dir

    @property
    def tts_model(self) -> str | None:
        """Devuelve el modelo TTS apropiado para esta ejecución.

        Si `audio_language` no es `None`, fuerza turbo (acepta `language_code`).
        Si es `None`, deja `None` para que `KieClient` use el multilingual
        default (que NO acepta `language_code` y respondería 422).
        """
        return DEFAULT_TURBO_MODEL if self.audio_language else None

    def step_dir(self, step: WorkflowStep) -> Path:
        """`output_dir / step_NN_<slug>/` para un step dado."""
        folder = f"step_{step.step:02d}_{step.scene_slug}"
        return self.output_dir / folder

    def resolved_voice_settings(self) -> VoiceSettings | None:
        """Devuelve voice_settings con `language_code` ajustado al `audio_language`."""
        if self.audio_language is None:
            return self.voice_settings
        base = self.voice_settings or VoiceSettings()
        if base.language_code:
            return base
        return base.model_copy(update={"language_code": self.audio_language})


# --- progress helpers (no state) ---------------------------------------


def initialize_progress(step: WorkflowStep) -> None:
    """Rellena `step.progress` con todas las keys esperadas a PENDING."""
    expected = expected_progress_keys_for_step(step)
    for key in expected:
        if key not in step.progress:
            step.progress[key] = WorkflowProgressStatus.PENDING


def set_progress(
    step: WorkflowStep, key: WorkflowProgressKey, status: WorkflowProgressStatus
) -> None:
    """Actualiza una key del progress. Crea la entry si no existía."""
    step.progress[key] = status


def mark_remaining_progress_failed(step: WorkflowStep) -> None:
    """Marca como FAILED cualquier key que quedó RUNNING/PENDING al fallar."""
    not_terminal = (WorkflowProgressStatus.PENDING, WorkflowProgressStatus.RUNNING)
    for key, value in list(step.progress.items()):
        if value in not_terminal:
            step.progress[key] = WorkflowProgressStatus.FAILED


def build_scene_prompt(step: WorkflowStep) -> str:
    """Concatena background_description + prompt para Nano Banana refit."""
    parts: list[str] = []
    if step.background_description.strip():
        parts.append(step.background_description.strip())
    parts.append(step.prompt.strip())
    return ". ".join(parts)


def ref_dict(ref: ImageAssetRef) -> dict[str, object]:
    """Serializa el ref para `image_jobs.refs_json` (mode='json' para datetimes)."""
    return ref.model_dump(mode="json")


def is_b_roll_with_audio(step: WorkflowStep) -> bool:
    """`True` si el step es b-roll con texto (descarga audio aparte)."""
    return step.type == StepType.B_ROLL and bool(step.text)


def is_b_roll_silent(step: WorkflowStep) -> bool:
    """`True` si el step es b-roll sin texto (solo video silencioso)."""
    return step.type == StepType.B_ROLL and not step.text


__all__ = [
    "DEFAULT_TURBO_MODEL",
    "WorkflowExecutionContext",
    "build_scene_prompt",
    "initialize_progress",
    "is_b_roll_silent",
    "is_b_roll_with_audio",
    "mark_remaining_progress_failed",
    "ref_dict",
    "set_progress",
]
