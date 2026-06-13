from __future__ import annotations

from kie_avatar_studio.app_layer.visual_prompt_guard import (
    IMAGE_VISUAL_GUARD,
    VIDEO_VISUAL_GUARD,
    VISUAL_TEXT_GUARD,
    append_image_visual_guard,
    append_video_visual_guard,
    append_visual_text_guard,
)
from kie_avatar_studio.app_layer.workflow_execution_context import build_scene_prompt
from kie_avatar_studio.domain.models import StepType, WorkflowStep
from kie_avatar_studio.domain.policies import MAX_I2V_PROMPT_CHARS, MAX_PROMPT_CHARS


def test_append_image_visual_guard_adds_policy_once() -> None:
    prompt = append_image_visual_guard("Natural kitchen scene")
    assert "Natural kitchen scene" in prompt
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
    assert "remove" in VIDEO_VISUAL_GUARD.lower()
    # Ambos listan explícitamente los elementos críticos que originaban
    # el bug de subtítulos chinos / iconos Douyin en Avatar Pro.
    for guard in (IMAGE_VISUAL_GUARD, VIDEO_VISUAL_GUARD):
        assert "Chinese" in guard
        assert "TikTok" in guard or "Douyin" in guard
        assert "watermark" in guard.lower()


def test_legacy_alias_maps_to_image_guard() -> None:
    """`append_visual_text_guard` quedó como alias del guard de imagen."""
    assert VISUAL_TEXT_GUARD == IMAGE_VISUAL_GUARD
    assert append_visual_text_guard("scene") == append_image_visual_guard("scene")


def test_append_image_visual_guard_preserves_near_limit_prompt() -> None:
    prompt = "x" * MAX_PROMPT_CHARS
    assert append_image_visual_guard(prompt) == prompt


def test_append_video_visual_guard_preserves_near_limit_prompt() -> None:
    prompt = "x" * MAX_PROMPT_CHARS
    assert append_video_visual_guard(prompt) == prompt


def test_append_video_visual_guard_respects_i2v_limit() -> None:
    prompt = "x" * MAX_I2V_PROMPT_CHARS
    assert append_video_visual_guard(prompt, max_chars=MAX_I2V_PROMPT_CHARS) == prompt


def test_build_scene_prompt_applies_image_visual_guard() -> None:
    step = WorkflowStep(
        step=1,
        scene_name="B roll",
        scene_slug="b_roll",
        type=StepType.B_ROLL,
        prompt="A product on a table",
        change_scene=True,
        scene_description="Kitchen counter",
    )
    # Las scene_image se generan con Nano Banana → guard de imagen
    # (preventivo). El guard de video se aplica luego en Avatar Pro / i2v.
    assert IMAGE_VISUAL_GUARD in build_scene_prompt(step)
