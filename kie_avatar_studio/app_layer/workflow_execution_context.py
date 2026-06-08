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
    SceneApprovalMode,
    StepType,
    VoiceSettings,
    WorkflowProgressKey,
    WorkflowProgressStatus,
    WorkflowStep,
)
from ..domain.policies import expected_progress_keys_for_step, resolve_effective_i2v_duration

DEFAULT_TURBO_MODEL: Final[str] = "elevenlabs/text-to-speech-turbo-2-5"


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
        i2v_duration_seconds_override: int | None = None,
        scene_approval_mode: SceneApprovalMode = SceneApprovalMode.AUTO,
        product_image_ref: ImageAssetRef | None = None,
        image_aspect_ratio: str | None = None,
    ) -> None:
        self.audio_language = audio_language
        self.voice_id = voice_id
        self.voice_settings = voice_settings
        self.base_image_ref = base_image_ref
        self.output_dir = output_dir
        # Override del workflow para la duración de los b-roll. Resuelto
        # por el WorkflowRunner antes de construir el context (lee de
        # `WorkflowPreSettings.i2v_duration_seconds`). Si es `None`,
        # cada step usa su propio `step.duration_seconds` o el default
        # global de `Settings.default_i2v_duration_seconds`.
        self.i2v_duration_seconds_override = i2v_duration_seconds_override
        # Modo de aprobación de scene_image. Cuando es MANUAL, los b-roll
        # que generan scene nueva (`change_scene=true` o
        # `include_product=true`) pausan el workflow después de generar la
        # scene_image y esperan revisión humana via la UI.
        self.scene_approval_mode = scene_approval_mode
        # Producto global a promocionar (resuelto a una ref Kie). `None`
        # si el workflow no promociona producto. Los steps con
        # `include_product=True` lo pasan como 2da ref a Nano Banana.
        self.product_image_ref = product_image_ref
        # Aspect ratio global para las imágenes generadas por Nano Banana 2
        # (tanto la base como las escenas de cada step).
        self.image_aspect_ratio = image_aspect_ratio

    def requires_scene_approval(self, step: WorkflowStep) -> bool:
        """`True` si este step necesita pausa para aprobación humana de scene_image.

        Condiciones:
        - El workflow corre en `SceneApprovalMode.MANUAL`.
        - El step es b-roll que genera una scene nueva con Nano Banana, es
          decir `change_scene=true` O `include_product=true` (ver
          `needs_scene_generation`). Los b-roll que reusan la base tal cual
          no tienen nada que aprobar; los a-roll nunca se pausan (decisión
          de producto: solo b-roll pasa por aprobación humana).
        - El step NO fue aprobado previamente (`scene_image_approved_at is None`).
        """
        if self.scene_approval_mode != SceneApprovalMode.MANUAL:
            return False
        if step.type != StepType.B_ROLL or not needs_scene_generation(step):
            return False
        return step.scene_image_approved_at is None

    def resolve_i2v_duration(self, step: WorkflowStep, default: int) -> int:
        """Resuelve la duración del b-roll para un step dado.

        Delega en `domain.policies.resolve_effective_i2v_duration` para
        evitar duplicar la regla de precedencia entre runtime y UI
        (preview del summary). Ver ese docstring para el orden completo.
        """
        return resolve_effective_i2v_duration(
            self.i2v_duration_seconds_override, step.duration_seconds, default
        )

    @property
    def tts_model(self) -> str | None:
        """Devuelve el modelo TTS apropiado para esta ejecución.

        Siempre devolvemos `None` (que se resuelve al `DEFAULT_TTS_MODEL`
        multilingual-v2 en el KieClient) para garantizar que NUNCA se use
        el modelo turbo, que es propenso a errores 500 del backend de Kie.
        """
        return None

    def step_dir(self, step: WorkflowStep) -> Path:
        """`output_dir / step_NN_<slug>/` para un step dado."""
        folder = f"step_{step.step:02d}_{step.scene_slug}"
        return self.output_dir / folder

    def resolved_voice_settings(self) -> VoiceSettings | None:
        """Devuelve voice_settings con `language_code` ajustado.

        Precedencia: el `audio_language` del JSON (específico del workflow)
        tiene prioridad sobre el `voice_settings.language_code` del preset.
        """
        if self.audio_language is not None:
            base = self.voice_settings or VoiceSettings()
            return base.model_copy(update={"language_code": self.audio_language})
        return self.voice_settings


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


def needs_scene_generation(step: WorkflowStep) -> bool:
    """`True` si el step requiere generar una scene_image nueva con Nano Banana.

    Se dispara la generación cuando el step cambia la escena
    (`change_scene=True`) O cuando incluye el producto promocional
    (`include_product=True`, que hay que componer sobre la base). Si
    ninguno aplica, el step reusa la imagen base tal cual (sin gastar
    Nano Banana).
    """
    return step.change_scene or step.include_product


# Instrucción (en inglés, idioma de los prompts de Nano Banana) para
# preservar el fondo de la base cuando se compone un producto sin cambiar
# la escena (`include_product=True` + `change_scene=False`).
_KEEP_BACKGROUND_HINT: Final[str] = (
    "Keep the exact same background, environment and scene from the "
    "reference person image; do not change the setting, only add the product"
)


def build_scene_prompt(step: WorkflowStep) -> str:
    """Construye el prompt de Nano Banana para la scene_image del step.

    Composición según los flags del step:
    - `change_scene=True`: incluye `scene_description` (cambia el entorno).
    - `include_product=True` SIN `change_scene`: añade una instrucción para
      preservar el fondo de la base (solo se compone el producto encima).
    - Siempre incluye el `prompt` del step (la acción/escena a animar).
    - `include_product=True`: añade `product_prompt` al final (cómo/dónde
      colocar el producto).
    """
    parts: list[str] = []
    if step.change_scene and step.scene_description.strip():
        parts.append(step.scene_description.strip())
    elif step.include_product and not step.change_scene:
        parts.append(_KEEP_BACKGROUND_HINT)
    parts.append(step.prompt.strip())
    if step.include_product and step.product_prompt.strip():
        parts.append(step.product_prompt.strip())
    return ". ".join(parts)


def ref_dict(ref: ImageAssetRef) -> dict[str, object]:
    """Serializa el ref para `image_jobs.refs_json` (mode='json' para datetimes)."""
    return ref.model_dump(mode="json")


def is_b_roll_with_audio(step: WorkflowStep) -> bool:
    """`True` si el step es b-roll que necesita TTS aparte (audio.mp3 + video silencioso).

    Requiere `voiceover=True` (default) Y `text` no vacío. Si `voiceover=False`,
    el b-roll va por el path de sound nativo de Kling (sin TTS, sin importar
    el text).
    """
    return step.type == StepType.B_ROLL and step.voiceover and bool(step.text)


def is_b_roll_native_sound(step: WorkflowStep) -> bool:
    """`True` si el step es b-roll que pide sound effects nativos de Kling 3.0.

    `voiceover=False` indica que NO se llama a TTS; Kling genera el audio
    embebido en el video basado en el prompt (`sound=true` en el body).
    """
    return step.type == StepType.B_ROLL and step.voiceover is False


def is_b_roll_silent(step: WorkflowStep) -> bool:
    """`True` si el step es b-roll silencioso (sin texto y con voiceover=True).

    `voiceover=True` (default) + `text=""` → video silencioso, sin TTS ni
    sound efx nativos. El usuario tendrá solo `video.mp4` sin audio.
    """
    return step.type == StepType.B_ROLL and step.voiceover and not step.text


__all__ = [
    "DEFAULT_TURBO_MODEL",
    "WorkflowExecutionContext",
    "build_scene_prompt",
    "initialize_progress",
    "is_b_roll_native_sound",
    "is_b_roll_silent",
    "is_b_roll_with_audio",
    "mark_remaining_progress_failed",
    "needs_scene_generation",
    "ref_dict",
    "set_progress",
]
