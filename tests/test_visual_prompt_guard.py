from __future__ import annotations

from kie_avatar_studio.app_layer.visual_prompt_guard import (
    IMAGE_VISUAL_GUARD,
    VIDEO_VISUAL_GUARD,
    VISUAL_TEXT_GUARD,
    append_image_visual_guard,
    append_video_visual_guard,
    append_visual_text_guard,
)
from kie_avatar_studio.app_layer.workflow_execution_context import (
    build_scene_prompt,
    build_veo_prompt,
)
from kie_avatar_studio.domain.models import StepType, WorkflowStep
from kie_avatar_studio.domain.policies import MAX_I2V_PROMPT_CHARS, MAX_PROMPT_CHARS


def test_append_image_visual_guard_adds_policy_once() -> None:
    prompt = append_image_visual_guard("Escena natural de cocina")
    assert "Escena natural de cocina" in prompt
    assert IMAGE_VISUAL_GUARD in prompt
    assert append_image_visual_guard(prompt) == prompt


def test_append_video_visual_guard_adds_policy_once() -> None:
    prompt = append_video_visual_guard("Mirada a cámara, gestos naturales")
    assert "Mirada a cámara, gestos naturales" in prompt
    assert VIDEO_VISUAL_GUARD in prompt
    assert append_video_visual_guard(prompt) == prompt


def test_image_and_video_guards_diverge() -> None:
    """El guard de video debe instruir a REMOVER, no a preservar."""
    assert IMAGE_VISUAL_GUARD != VIDEO_VISUAL_GUARD
    assert "elimínalo" in VIDEO_VISUAL_GUARD.lower()
    # Ambos listan explícitamente los elementos críticos que originaban
    # el bug de subtítulos chinos / iconos Douyin en Avatar Pro.
    for guard in (IMAGE_VISUAL_GUARD, VIDEO_VISUAL_GUARD):
        assert "chinos/japoneses/coreanos" in guard.lower()
        assert "TikTok" in guard or "Douyin" in guard
        assert "marcas de agua" in guard.lower()


def test_legacy_alias_maps_to_image_guard() -> None:
    """`append_visual_text_guard` quedó como alias del guard de imagen."""
    assert VISUAL_TEXT_GUARD == IMAGE_VISUAL_GUARD
    assert append_visual_text_guard("escena") == append_image_visual_guard("escena")


def test_append_image_visual_guard_preserves_near_limit_prompt() -> None:
    prompt = "x" * MAX_PROMPT_CHARS
    assert append_image_visual_guard(prompt) == prompt


def test_append_video_visual_guard_preserves_near_limit_prompt() -> None:
    prompt = "x" * MAX_PROMPT_CHARS
    assert append_video_visual_guard(prompt) == prompt


def test_append_video_visual_guard_respects_i2v_limit() -> None:
    prompt = "x" * MAX_I2V_PROMPT_CHARS
    assert append_video_visual_guard(prompt, max_chars=MAX_I2V_PROMPT_CHARS) == prompt


def test_build_scene_prompt_keeps_raw_composition_without_guard() -> None:
    step = WorkflowStep(
        step=1,
        scene_name="B roll",
        scene_slug="b_roll",
        type=StepType.B_ROLL,
        prompt="Un producto sobre una mesa",
        change_scene=True,
        scene_description="Encimera de cocina",
    )
    assert build_scene_prompt(step) == "Encimera de cocina. Un producto sobre una mesa"


def test_build_scene_prompt_product_only_avoids_keep_background_hint() -> None:
    step = WorkflowStep(
        step=2,
        scene_name="Producto solo",
        scene_slug="producto_solo",
        type=StepType.B_ROLL,
        prompt="Plano detalle del suplemento",
        change_scene=False,
        include_product=True,
        include_model=False,
        product_prompt="Frasco ámbar con etiqueta visible",
    )
    prompt = build_scene_prompt(step)
    assert "Usa únicamente la foto de referencia del producto" in prompt
    assert "permite interacción humana parcial" in prompt
    assert "Mantén exactamente el mismo fondo" not in prompt


def test_build_veo_prompt_adds_spoken_text_when_present() -> None:
    step = WorkflowStep(
        step=1,
        scene_name="A roll con diálogo",
        scene_slug="a_roll_dialogo",
        type=StepType.A_ROLL,
        prompt="La modelo sostiene el suplemento frente a cámara",
        text="Este suplemento me ayudó con la pesadez después de comer.",
        include_product=True,
        include_model=True,
    )
    prompt = build_veo_prompt(step)
    assert "La modelo sostiene el suplemento frente a cámara" in prompt
    assert "La persona en escena debe decir exactamente" in prompt
    assert "Este suplemento me ayudó con la pesadez después de comer." in prompt
